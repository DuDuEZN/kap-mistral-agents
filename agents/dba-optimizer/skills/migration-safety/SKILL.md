---
name: migration-safety
description: Procédures de migrations de données sûres, transactionnelles et réversibles sur PostgreSQL et MySQL. Charger pour écrire ou relire un script de migration de schéma ou de données.
---

# Migrations sûres, transactionnelles et réversibles

## Règles générales
1. Toujours deux scripts : **UP** (exécution) et **DOWN** (retour arrière), testés tous
   les deux, le DOWN testé sur une base migrée.
2. Rejouable et idempotent : `IF EXISTS` / `IF NOT EXISTS` ; un script doit pouvoir
   être ré-exécuté sans erreur.
3. Sauvegarde vérifiée avant toute migration destructive (pg_dump de la table,
   mysqldump, snapshot).
4. Répétition sur une copie de production à taille réelle, puis exécution en heures
   creuses.
5. Connaître son plan de rollback **étape par étape** : jusqu'où peut-on revenir sans
   perte ?

## PostgreSQL
- Le DDL est transactionnel : envelopper `BEGIN; ... COMMIT;` — tout échec déclenche un
  ROLLBACK intégral. C'est l'avantage n°1 de PG, toujours l'utiliser.
- En tête de chaque transaction :
  `SET LOCAL lock_timeout = '2s'; SET LOCAL statement_timeout = '30s';`
  (lock_timeout surtout : une migration bloquée derrière un verrou bloque tout le
  monde derrière elle).
- Index : `CREATE INDEX CONCURRENTLY IF NOT EXISTS` — hors transaction, seul. En cas
  d'échec il reste INVALID : le supprimer puis rejouer.
- Clés étrangères : `ADD CONSTRAINT ... FOREIGN KEY ... NOT VALID;` puis
  `ALTER TABLE ... VALIDATE CONSTRAINT ...;` — la validation ne prend qu'un verrou
  SHARE UPDATE EXCLUSIVE, compatible avec les écritures.
- `ADD COLUMN ... DEFAULT ...` non nul : instantané depuis PG 11 (métadonnées).
  `NOT NULL` sans DEFAULT sur table pleine : 3 étapes — colonne nullable, backfill par
  lots, contrainte `NOT NULL` ensuite.
- Backfill par lots : `UPDATE ... WHERE id BETWEEN ... AND ...` en boucle avec commit
  par lot, puis `ANALYZE`.
- `DROP COLUMN` : en deux temps (expand-contract) — cesser d'écrire, déployer,
  vérifier zéro lecture, DROP dans une migration suivante.
- Renommage : migration en 2 phases (colonne/tables anciennes et nouvelles coexistent
  une release, alias côté application) pour rester déployable sans downtime.

## MySQL
- **DDL = commit implicite** : `BEGIN ... COMMIT` n'annule PAS le DDL. Une commande
  par étape, chaque étape atomique et réversible individuellement.
- `ALTER TABLE ... ALGORITHM=INPLACE, LOCK=NONE` quand le support est confirmé
  (vérifier par essai) ; sinon, pour les grandes tables : `gh-ost` ou
  `pt-online-schema-change`.
- `ADD COLUMN` avec DEFAULT : instantané en 8.0+ (`ALGORITHM=INSTANT`).
- `SET FOREIGN_KEY_CHECKS = 0` : à proscrire. L'intégrité référentielle est
  non négociable ; si une FK empêche une migration, c'est la migration qui est fausse.

## Pattern expand-contract (zero downtime)
1. **Expand** : ajouter le nouveau schéma à côté de l'ancien (colonnes/tables),
   compatible avec l'application en production.
2. **Backfill** : remplir par lots, sans verrou long.
3. **Basculer** : l'application lit puis écrit le nouveau schéma (double écriture
   temporaire si nécessaire).
4. **Contract** : retirer l'ancien schéma dans une migration ultérieure, une fois
   l'ancien code retiré de production.
Chaque étape reste déployable et réversible : c'est ce pattern qui rend une migration
« réversible » au sens opérationnel, pas seulement le script DOWN.

## Checklist avant exécution
- [ ] DOWN écrit et testé sur le résultat du UP.
- [ ] lock_timeout / statement_timeout posés (PG).
- [ ] Pas d'ordre destructeur sans WHERE ni sauvegarde vérifiée.
- [ ] Index créés CONCURRENTLY / INPLACE selon moteur.
- [ ] FK ajoutées NOT VALID puis VALIDATE (PG).
- [ ] Backfill par lots + ANALYZE.
- [ ] Test répété sur copie de production, fenêtre de maintenance identifiée.
