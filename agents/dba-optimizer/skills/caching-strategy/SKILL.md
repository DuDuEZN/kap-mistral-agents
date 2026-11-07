---
name: caching-strategy
description: Stratégies de mise en cache pour alléger la base principale — Redis, vues matérialisées PostgreSQL, tables d'agrégats MySQL. Charger pour concevoir une politique de cache ou décharger une base saturée en lecture.
---

# Stratégies de mise en cache

## 1. Décider quoi cacher
1. Mesurer : requêtes répétitives coûteuses partagées (pg_stat_statements, slow log).
   Candidate type : lecture > 100 ms, ratio lecture/écriture élevé, résultat identique
   pour beaucoup d'utilisateurs.
2. Définir la fraîcheur acceptable par donnée : quelle est la pénalité métier d'une
   donnée périmée de 30 s / 5 min / 1 h ? La TTL en découle.
3. Ne jamais cacher ce qui n'est pas mesuré : un cache mal calibré crée des bugs
   de cohérence invisibles.

## 2. Redis — pattern cache-aside
- Lecture : GET clé → miss → lire la base → `SET clé valeur EX ttl` → servir.
  **TTL systématique**, même long : c'est le filet de sécurité contre l'invalidation
  oubliée.
- Clés préfixées par domaine et versionnées (`user:profile:v2:{id}`) : monter la
  version invalide tout un domaine d'un coup, sans FLUSHDB.
- Invalidation : par événement métier (UPDATE → DEL ou publish) ; la TTL seule ne
  suffit jamais pour les données critiques.
- Anti-stampede : une entrée chaude qui expire re-déclenche N lectures de la base.
  Verrou de reconstruction : `SET lock:{clé} 1 NX EX 5` — un seul processus reconstruit,
  les autres attendent ou servent l'ancienne valeur.
- Types utiles : hashes (objets), sorted sets (classements), pub/sub (invalidation
  multi-instances), et `EX` pour tout.
- Sécurité : TLS, ACL/mot de passe, réseau privé, jamais de données sensibles en clair.

## 3. Vues matérialisées (PostgreSQL)
- Pour les agrégats coûteux lus souvent : `CREATE MATERIALIZED VIEW ... AS SELECT
  ... GROUP BY ...` + index uniques sur les colonnes clés.
- `REFRESH MATERIALIZED VIEW CONCURRENTLY` (index unique requis) : les lectures ne
  sont pas bloquées pendant le refresh.
- Planifier le refresh (pg_cron, tâche applicative) selon la fraîcheur nécessaire, et
  mesurer son coût : un refresh trop lourd qui sature la base est contre-productif.
- Comparer au coût de la requête agrégée en direct : la vue ne se justifie que si le
  refresh est nettement moins fréquent que les lectures.

## 4. MySQL
- Pas de vues matérialisées : tables d'agrégats maintenues par une tâche planifiée
  (INSERT ... SELECT REPLACE/ON DUPLICATE KEY) ou par déclencheurs — prudence sur les
  triggers quand la table source est très écrite.
- Le cache de requêtes a été supprimé en MySQL 8.0 : ne pas le recommander ; cacher
  au niveau applicatif ou Redis.

## 5. Hiérarchie de cache type
1. Cache applicatif en mémoire, très court TTL, par instance.
2. Redis partagé, TTL court/moyen, invalidation par événement.
3. Vues matérialisées / agrégats précalculés (données dérivées).
4. Base principale.
Règle d'or : chaque couche doit pouvoir tomber sans casser le service (le cache est
une accélération, jamais une source de vérité). Prévoir la dégradation quand Redis
est indisponible (timeout court, fallback base).

## 6. Mesurer le résultat
Objectifs à fixer avec l'utilisateur : taux de hit Redis cible, latence P95 avant/
après, requêtes/s tombant sur la base avant/après. Un cache sans métrique est un
cache qu'on ne peut pas auditer.
