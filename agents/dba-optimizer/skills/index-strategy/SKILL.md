---
name: index-strategy
description: Conception et justification de la stratégie d'indexation PostgreSQL et MySQL (B-tree, GIN, GiST, BRIN, index composites, partiels, couvrants). Charger pour concevoir, justifier ou auditer un plan d'indexation.
---

# Stratégie d'indexation

## Principes
1. Indexer à partir des requêtes réelles observées (pg_stat_statements, slow log),
   jamais à partir d'un schéma seul.
2. Chaque index doit être justifié par écrit : requêtes servies, sélectivité, gain
   attendu, coût d'écriture. « Au cas où » n'est pas une justification.
3. Un index inutilisé se voit à `idx_scan = 0` (pg_stat_user_indexes) ou dans le
   `sys.schema_unused_indexes` MySQL → à supprimer après période d'observation.

## Ordre des colonnes d'un index composite (B-tree)
Règle : **égalités → tri → plages → couverture**.
1. Colonnes en prédicat d'égalité (`status = 'pending'`) d'abord.
2. Puis la colonne du `ORDER BY` (le tri doit être servi par l'index).
3. Puis les plages (`created_at > ...`) — une seule par index de préférence.
4. Les colonnes restantes de la clause SELECT → en `INCLUDE` (PG 11+).

## Types d'index et cas d'usage (PostgreSQL)
- **B-tree** : égalités, plages, tri, unicité. Le cheval de trait par défaut.
- **GIN** : `jsonb` (`@>`), tableaux, plein-texte (`tsvector`), `pg_trgm` pour
  recherche floue avec LIKE.
- **GiST** : géométrie, intervalles (exclusion de chevauchement), k-NN.
- **BRIN** : très grandes tables avec corrélation physique (logs, séries temporelles
  insérées séquentiellement) ; quasi gratuit en écriture.
- **Hash** : uniquement égalité simple (PG 10+), pas de tri.
- **Partiel** : `WHERE status = 'pending'` sur une petite fraction active d'une
  grande table (files d'attente, jobs).
- **Expression** : `LOWER(email)` — ne jamais casser la correspondance avec la requête.

## Spécificités MySQL / InnoDB
- Table clusterisée sur la clé primaire : une PK courte et monotone (BIGINT AUTO_INCREMENT)
  est vitale ; une PK UUIDv4 randomise les inserts (page splits).
- Les index secondaires portent la PK dans leurs feuilles : un index secondaire bien
  conçu peut être couvrant sans `INCLUDE`.
- FULLTEXT / SPATIAL disponibles ; pas d'index partiels ni d'index sur expression.

## Coûts et garde-fous
- Chaque index ralentit INSERT/UPDATE/DELETE et occupe de l'espace : viser un nombre
  restreint (< ~8) sur les tables très écrites.
- Surveiller le bloat (`pgstattuple`) et la taille (`pg_relation_size`).
- Statistiques : `ANALYZE` après un gros backfill, sinon l'optimiseur estime mal.

## Création sans verrou bloquant
- PostgreSQL : `CREATE INDEX CONCURRENTLY IF NOT EXISTS ...` — hors transaction,
  exécuter seul ; échoue proprement en laissant un index INVALID à supprimer.
- MySQL : `ALTER TABLE ... ADD INDEX ..., ALGORITHM=INPLACE, LOCK=NONE` quand le
  support est confirmé ; sinon `gh-ost` ou `pt-online-schema-change`.

## Audit d'un plan d'indexation
1. Lister les 10 requêtes les plus coûteuses (temps total, pas temps unitaire).
2. Pour chacune : le plan utilise-t-il l'index ? (`EXPLAIN` avant/après, jamais à l'aveugle).
3. Doublons/préfixes redondants : `(a, b)` couvre `(a)` → supprimer l'index `(a)`.
4. Mesurer l'impact d'écriture avant/après sur un bench d'insertion réaliste.
