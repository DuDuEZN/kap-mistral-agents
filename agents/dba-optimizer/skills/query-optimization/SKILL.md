---
name: query-optimization
description: Méthode complète d'optimisation des requêtes SQL et de résolution des requêtes N+1. Charger quand une requête est lente, quand on lit un plan EXPLAIN, ou quand on réécrit du code d'accès aux données.
---

# Optimisation des requêtes

## 1. Mesurer avant d'optimiser
- PostgreSQL : `EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)` ; hiérarchiser avec
  `pg_stat_statements` (total_exec_time) ; ne jamais optimiser sans mesure de départ.
- MySQL : `EXPLAIN FORMAT=JSON` puis `EXPLAIN ANALYZE` (8.0.18+) ; slow query log
  (`long_query_time = 0.5`) ; tables `performance_schema` / `sys`.
- Toujours capturer : temps total, lignes réelles vs estimées, lectures disques/buffers.

## 2. Lire un plan d'exécution
- Écart important rows estimées / réelles → statistiques obsolètes : `ANALYZE` la table.
- Seq Scan + Filter très sélectif sur une grande table → index manquant ou non utilisé.
- rows=1 attendu mais Seq Scan sur jointure → mauvais ordre de jointure, vérifier le
  Nested Loop avec un petit ensemble externe.
- Hash Join avec « Batches » élevé → `work_mem` insuffisant (PG) ; spill disque.
- Sort Method: external merge → tri non servi par un index ; work_mem court.
- « Rows Removed by Filter » énorme → filtre exécuté trop tard : filtrer avant la jointure.

## 3. Anti-patterns et correctifs
- Non-SARGable (`LOWER(col) =`, `LIKE '%x'`, `DATE(col) =`) : précalcul, ou index sur
  expression/trigramme.
- `SELECT *` : nommer les colonnes, viser l'index couvrant.
- `NOT IN (SELECT ...)` : `NOT EXISTS` (NULL-safe, plan plus stable).
- OFFSET profond : pagination keyset — `WHERE (created_at, id) > (:last_c, :last_id)
  ORDER BY created_at DESC, id DESC LIMIT n` avec index de tri correspondant.
- `OR` inter-colonnes : `UNION ALL` par branche (index utilisables), ou index partiels.
- `COUNT(*)` exhaustif : estimation (`pg_class.reltuples`), compteur dénormalisé, ou
  cache ; un COUNT(*) d'exactitude est rarement nécessaire sur la page 1.
- CTE (PG ≥ 12) : inlinées par défaut ; vérifier leur plan si « CTE Scan ».
- `ORDER BY` + `LIMIT` : l'index doit couvrir le tri, sinon tri complet à chaque exécution.

## 4. Résoudre les N+1 — ordre de résolution
1. Prouver le N+1 : compter les requêtes SQL exécutées pour une requête HTTP
   (logs, pg_stat_statements, echo ORM).
2. Deux voies : une seule requête avec JOIN quand le résultat est aplati, ou chargement
   par lots `WHERE id IN (...)` regroupé côté application quand les objets sont liés.
3. Eager loading selon la stack :
   - SQLAlchemy : `selectinload()` (1-N), `joinedload()` (N-1).
   - Django : `select_related()` (FK), `prefetch_related()` (M2M).
   - Prisma : `include: { ... }`.
   - TypeORM : `relations` / `leftJoinAndSelect`.
   - JPA/Hibernate : `JOIN FETCH`, `@EntityGraph`, `@BatchSize(size=...)`.
   - Mongoose : `.populate()`.
4. Re-vérifier le plan de la requête groupée : un JOIN dupliqué fausse les agrégats
   (compter via une sous-requête, pas via le JOIN 1-N).

## 5. Réécritures sûres
- `EXISTS` plutôt que `IN` pour les sous-requêtes filtrantes.
- `UNION ALL` plutôt que `UNION` quand l'unicité est déjà garantie.
- Filtrer le plus tôt possible : sous-requête scalaire / LATERAL avant agrégation.
- Fenêtrage (`ROW_NUMBER() OVER (PARTITION BY ...)`) pour « dernière ligne par groupe ».
- Objectif : constantes mesurables avant/après (ms, buffers, nombre de requêtes).
