"""
DBA Optimizer — Agent DBA et Expert en Optimisation des Performances.

Construit avec le SDK Mistral Agents (`mistralai-agents`, import `mistralai.agents`).
Phase 1 : agent expert-conseil — il analyse, diagnostique et produit des scripts,
sans connexion directe à une base (aucun identifiant requis).
Phase 2 (à venir) : connexion aux bases réelles via les secrets de déploiement.

Variables d'environnement :
- MISTRAL_API_KEY : clé API Mistral (locale / `agents serve`). Le worker géré
  s'authentifie lui-même via son compte de service.
- DBA_MODEL   : modèle de l'agent (défaut : mistral-large-4).
- DBA_SANDBOX : "mistral" (défaut, prod), "docker" (dev local), "bashkit", "none".
"""

import asyncio
import os
import re
from pathlib import Path
from typing import Annotated

from pydantic import Field

from mistralai import agents

# Répertoire des skills, résolu relativement à ce fichier (jamais du cwd).
SKILLS = Path(__file__).with_name("skills")

MODEL = os.environ.get("DBA_MODEL", "mistral-large-4")
AGENT_NAME = "dba-optimizer"

INSTRUCTIONS = """
Rôle
Tu es un Administrateur de Base de Données (DBA) et un Expert en Optimisation des
Performances, spécialisé dans les moteurs relationnels (PostgreSQL, MySQL) et NoSQL
(MongoDB, Redis, Elasticsearch, Cassandra).

Mission
Garantir l'intégrité, la sécurité, la haute disponibilité et la rapidité absolue de la
couche de persistance et des requêtes applicatives de tes interlocuteurs.

Contexte
À mesure que l'application évolue, les requêtes inefficaces et l'absence d'indexation
compromettent l'expérience utilisateur et saturent les ressources serveur.

Méthode de travail
1. Clarifie le contexte avant tout diagnostic : moteur et version, volumétrie des
   tables, requête(s) ou code concernés, plan d'exécution (EXPLAIN ANALYZE /
   EXPLAIN FORMAT=JSON) si disponible, contraintes de disponibilité.
2. Objective tes analyses avec les outils statiques (analyze_sql, detect_n_plus_one,
   validate_migration), puis complète avec ton expertise : les outils sont des aides,
   pas des oracles.
3. Charge la skill pertinente quand la tâche porte sur l'optimisation de requêtes,
   la stratégie d'indexation, la sûreté des migrations ou le caching.
4. Honnêteté : tu n'as pas de connexion aux bases en phase 1. Ne prétends jamais
   avoir exécuté une requête ; demande un plan d'exécution quand c'est utile.

Livrables attendus — structure tes réponses ainsi
- Scripts de migration SQL optimisés et commentés : sections UP et DOWN,
  transactionnels, réversibles, rejouables.
- Plan d'indexation documenté : pour chaque index, type, colonnes et leur ordre,
  couverture, requêtes servies, coût d'écriture estimé, méthode de création sans
  verrou bloquant, et la mesure qui validera le gain.
- Recommandations de requêtes réécrites et de politiques de cache (Redis, vues
  matérialisées) avec objectifs mesurables avant/après.

Critères de validation de tes livrables
- Temps d'exécution des requêtes critiques minimisé (plans d'exécution optimaux) :
   montre le plan attendu et les métriques à surveiller.
- Intégrité référentielle et contraintes de clés étrangères strictement respectées.
- Sécurité renforcée : requêtes paramétrées, moindre privilège, aucun secret en dur,
   protection contre les injections SQL et les fuites de données.

Style
Réponds dans la langue de ton interlocuteur. Sois précis, concret et concis :
montre le SQL, pas la théorie. Si une information manque pour un diagnostic fiable,
pose ta question au lieu de supposer.
"""


# ---------------------------------------------------------------------------
# Outil 1 — analyse statique de requêtes SQL
# ---------------------------------------------------------------------------

@agents.tool
async def analyze_sql(
    sql: Annotated[str, Field(description="Le script SQL à analyser.")],
    dialect: Annotated[
        str,
        Field(description="Moteur cible : 'postgresql', 'mysql' ou 'generic'."),
    ] = "generic",
) -> dict:
    """Analyse statique d'un script SQL sans l'exécuter.

    Détecte les anti-patterns de performance les plus coûteux : prédicats
    non-SARGables (fonction ou joker en tête sur une colonne, ce qui empêche
    l'usage d'un index), SELECT *, jointures sans condition, pagination
    profonde par OFFSET, OR inter-colonnes, NOT IN (SELECT ...).
    Retourne des constats avec ligne, extrait, gravité et suggestion.
    """
    dialect = (dialect or "generic").lower()
    lines = sql.splitlines()
    findings: list[dict] = []

    def add(rule: str, severity: str, suggestion: str, line: int | None = None) -> None:
        excerpt = ""
        if line is not None and 0 < line <= len(lines):
            excerpt = lines[line - 1].strip()[:160]
        findings.append(
            {
                "rule": rule,
                "severity": severity,
                "line": line,
                "excerpt": excerpt,
                "suggestion": suggestion,
            }
        )

    _ = dialect  # heuristiques communes ; les notes spécifiques sont en fin de rapport

    for i, raw in enumerate(lines, start=1):
        l = raw.lower()
        if re.search(r"\bselect\s+\*", l):
            add(
                "select_star", "medium",
                "Nomme les colonnes : un index couvrant devient exploitable et les I/O baissent.",
                i,
            )
        if re.search(r"\blike\s+N?'?%", l):
            add(
                "like_leading_wildcard", "high",
                "Joker en tête ('%...') : un index B-tree ne peut pas être parcouru. "
                "Envisage un index trigramme (pg_trgm) ou la recherche plein-texte.",
                i,
            )
        if re.search(r"\b(or\b)", l) and re.search(r"=\s*[^=]", l) and " or " in l:
            add(
                "or_across_predicates", "low",
                "OR entre prédicats de colonnes différentes : le moteur peut renoncer aux "
                "index. Teste un UNION ALL par branche ou des index partiels.",
                i,
            )
        if re.search(r"\bnot\s+in\s*\(?\s*select\b", l):
            add(
                "not_in_subquery", "medium",
                "Préfère NOT EXISTS : pas de piège avec les NULL et un plan plus stable.",
                i,
            )
        if re.search(r"\boffset\s+\d{4,}\b", l):
            add(
                "deep_offset", "high",
                "OFFSET profond relit tout le début de l'index à chaque page. Passe en "
                "pagination keyset : WHERE (col_tri, id) > (:last_v, :last_id) ORDER BY ...",
                i,
            )
        if re.search(r"\b(lower|upper|substr|substring|date_trunc|extract|cast|coalesce|trim|to_char|year|month|day|hour|abs|round|floor)\s*\(", l) \
                and re.search(r"\bwhere\b|\border\s+by\b|\bgroup\s+by\b|\bjoin\b|\bon\b", l):
            add(
                "function_on_column", "high",
                "Fonction sur une colonne dans une clause filtrante : prédicat non-SARGable. "
                "Précalcule la valeur, ou crée un index sur expression.",
                i,
            )
        if re.search(r"\bfrom\s+[\w.\"`]+\s*,\s*[\w.\"`]+", l):
            add(
                "comma_join", "high",
                "Jointure par virgule (produit cartésien) : explicite le JOIN et sa condition ON.",
                i,
            )
        if re.search(r"\bdistinct\b", l) and re.search(r"\bjoin\b", " ".join(lines[max(0, i - 3):i]).lower()):
            add(
                "distinct_with_join", "low",
                "DISTINCT après JOIN signale une cardinalité un-plusieurs mal maîtrisée : "
                "vérifie les relations et filtre avant de joindre.",
                i,
            )

    # Jointures sans condition ON : analyse par instruction.
    for stmt in sql.split(";"):
        s = stmt.lower()
        if re.search(r"\bjoin\b", s) and not re.search(r"\bon\b", s) and not re.search(r"\busing\s*\(", s):
            add(
                "join_without_on", "high",
                "JOIN sans condition ON (ou USING) : produit cartésien probable, "
                "explose lignes et temps de réponse.",
            )

    severity_rank = {"high": 3, "medium": 2, "low": 1}
    findings.sort(key=lambda f: (-severity_rank.get(f["severity"], 0), f["line"] or 0))

    notes = [
        "L'analyse statique complète l'EXPLAIN, elle ne le remplace pas : "
        "valider chaque correction par EXPLAIN (ANALYZE, BUFFERS)."
    ]
    if dialect == "postgresql":
        notes.append(
            "PostgreSQL : active pg_stat_statements pour hiérarchiser les requêtes réelles "
            "avant d'optimiser."
        )
    elif dialect == "mysql":
        notes.append(
            "MySQL : active le slow query log (long_query_time=0.5) et utilise "
            "performance_schema / sys pour cibler les requêtes coûteuses."
        )

    return {
        "dialect": dialect,
        "findings": findings,
        "summary": {
            "high": sum(1 for f in findings if f["severity"] == "high"),
            "medium": sum(1 for f in findings if f["severity"] == "medium"),
            "low": sum(1 for f in findings if f["severity"] == "low"),
        },
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Outil 2 — détection des requêtes N+1 dans le code applicatif
# ---------------------------------------------------------------------------

_DB_CALL_PATTERNS = [
    (r"\.objects\.(get|filter|create|update)\(", "ORM Django"),
    (r"session\.query\(|session\.execute\(|session\.get\(", "SQLAlchemy"),
    (r"\bprisma\.[a-z_.]+\.(find|update|create|delete)", "Prisma"),
    (r"\.findOne\(|\.findMany\(|\.findById\(|\.findByPk\(", "TypeORM / Mongoose"),
    (r"getRepository\(|\.createQuery\(|createQueryRunner", "TypeORM / JPA"),
    (r"->getRepository\(|->find\(|->findOneBy\(", "Doctrine"),
    (r"\.collection\(\w+\)\.(find|findOne|aggregate)\(", "MongoDB driver"),
    (r"\bSELECT\b|\bINSERT\b|\bUPDATE\b", "SQL brut"),
]

_EAGER_LOADING = {
    "python-sqlalchemy": "selectinload() pour les 1-N, joinedload() pour les N-1, "
    "ou une requête agrégée avec GROUP BY.",
    "django": "select_related() pour les clés étrangères, prefetch_related() pour les "
    "relations multiples, ou QuerySet.values() pour éviter le chargement d'objets.",
    "prisma": "include: { relation: true } pour charger en une passe, ou $transaction "
    "avec une requête groupée.",
    "typeorm": "relations, leftJoinAndSelect via QueryBuilder, ou IN (...) par lots "
    "d'identifiants.",
    "jpa": "JOIN FETCH / @EntityGraph / @BatchSize(size=...), ou requête DTO en projection.",
    "node-mongoose": ".populate('relation') ou $lookup dans une aggregation.",
    "generic": "Une seule requête avec JOIN, ou chargement par lots (WHERE id IN (...)), "
    "puis regroupement côté application.",
}


@agents.tool
async def detect_n_plus_one(
    code: Annotated[str, Field(description="Le code d'accès aux données à inspecter.")],
    language: Annotated[
        str,
        Field(
            description="Stack : 'python-sqlalchemy', 'django', 'prisma', 'typeorm', "
            "'jpa', 'node-mongoose' ou 'generic'."
        ),
    ] = "generic",
) -> dict:
    """Détecte les requêtes N+1 dans du code applicatif.

    Signale les appels de requête exécutés dans une boucle (for, while, forEach,
    map, each) — la première cause des cascades de requêtes invisibles — et
    recommande l'alternative de chargement groupé adaptée à la stack.
    """
    language = (language or "generic").lower()
    lines = code.splitlines()
    suspicious: list[dict] = []

    loop_re = re.compile(
        r"^(\s*)(for\s|while\s|.*\.forEach\(|.*\.map\(|.*\.each\b|.*->each|.*for\s*\()"
    )
    for idx, raw in enumerate(lines):
        m = loop_re.match(raw)
        if not m:
            continue
        loop_indent = len(m.group(1))
        for j in range(idx + 1, min(idx + 60, len(lines))):
            inner = lines[j]
            if not inner.strip():
                continue
            indent = len(inner) - len(inner.lstrip())
            if indent <= loop_indent:
                break  # sortie de la boucle
            low = inner.lower()
            for pattern, label in _DB_CALL_PATTERNS:
                if re.search(pattern, low):
                    suspicious.append(
                        {
                            "loop_line": idx + 1,
                            "call_line": j + 1,
                            "loop_excerpt": raw.strip()[:120],
                            "call_excerpt": inner.strip()[:120],
                            "detected": label,
                        }
                    )
                    break

    if not suspicious:
        return {
            "suspicious_loops": [],
            "recommendation": (
                "Aucune requête en boucle détectée statiquement. Si un N+1 est "
                "suspecté, active les logs de requêtes (pg_stat_statements, slow log, "
                "echo SQL de l'ORM) et compte les requêtes par requête HTTP."
            ),
        }

    return {
        "suspicious_loops": suspicious,
        "recommendation": (
            "Requête(s) probablement exécutée(s) en boucle (N+1). Correction de référence : "
            + _EAGER_LOADING.get(
                language,
                _EAGER_LOADING["generic"],
            )
            + " Puis vérifier avec les logs que le nombre de requêtes devient constant "
            "quel que soit N."
        ),
    }


# ---------------------------------------------------------------------------
# Outil 3 — validation de sûreté d'un script de migration
# ---------------------------------------------------------------------------

@agents.tool
async def validate_migration(
    script: Annotated[str, Field(description="Le script de migration à valider.")],
    dialect: Annotated[
        str, Field(description="Moteur cible : 'postgresql' ou 'mysql'.")
    ] = "postgresql",
) -> dict:
    """Valide la sûreté d'un script de migration avant exécution.

    Vérifie la transactionnalité, la réversibilité (section DOWN), les ordres
    destructeurs sans clause restrictive, la création d'index sans verrou
    bloquant, l'ajout de contraintes sans NOT VALID, la rejouabilité
    (IF [NOT] EXISTS) et les verrous longs.
    """
    dialect = (dialect or "postgresql").lower()
    low = script.lower()
    findings: list[dict] = []

    def add(severity: str, issue: str, recommendation: str, line: int | None = None):
        findings.append(
            {
                "severity": severity,
                "line": line,
                "issue": issue,
                "recommendation": recommendation,
            }
        )

    # --- Ordres destructeurs ou de masse sans clause restrictive -------------
    for m in re.finditer(r"^\s*(delete\s+from\s+\S+|update\s+\S+\s+set)\s*$", low, re.M):
        add(
            "critical",
            "Ordre de masse sans WHERE : DELETE/UPDATE non restreint.",
            "Restreins avec une clé primaire et traite par lots (WHERE id BETWEEN ... "
            "LIMIT n), sinon fais un TRUNCATE/TableCopy explicite et documenté.",
        )
    if re.search(r"\btruncate\b", low):
        add(
            "critical",
            "TRUNCATE présent : vidage complet et non récupérable dans la transaction.",
            "Exige une sauvegarde préalable et une validation humaine explicite.",
        )

    # --- Transactionnalité ---------------------------------------------------
    has_begin = re.search(r"\bbegin\b|start transaction", low) is not None
    has_commit = re.search(r"\bcommit\b", low) is not None
    if dialect == "postgresql" and not (has_begin and has_commit):
        add(
            "high",
            "PostgreSQL : le DDL est transactionnel mais le script n'est pas "
            "enveloppé dans BEGIN/COMMIT.",
            "Enveloppe le script : BEGIN; ... COMMIT; — tout échec déclenche un "
            "ROLLBACK intégral et automatique.",
        )
    if dialect == "mysql" and has_begin:
        add(
            "high",
            "MySQL : DDL = commit implicite — la transaction n'annule PAS le DDL.",
            "Une seule commande DDL par étape, chaque étape atomique et réversible "
            "individuellement ; grandes tables : gh-ost ou pt-online-schema-change.",
        )

    # --- Verrous et création d'index ------------------------------------------
    if dialect == "postgresql":
        if "concurrently" not in low and re.search(r"\bcreate\s+index\b", low):
            add(
                "high",
                "CREATE INDEX sans CONCURRENTLY : verrou ShareLock bloquant les "
                "écritures pendant toute la construction.",
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ... (hors transaction, "
                "exécuter seul). Prévoir work_mem confortable.",
            )
        if "not valid" not in low and re.search(r"\badd\s+constraint\b.*foreign\s+key", low):
            add(
                "medium",
                "Clé étrangère ajoutée sans NOT VALID : validation complète sous "
                "verrou sur toute la table.",
                "ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY ... NOT VALID; puis "
                "ALTER TABLE ... VALIDATE CONSTRAINT ... (verrou SHARE UPDATE "
                "EXCLUSIVE seulement).",
            )
        if "lock_timeout" not in low:
            add(
                "medium",
                "Pas de lock_timeout : une migration peut se bloquer indéfiniment "
                "derrière un verrou existant et bloquer les autres à son tour.",
                "En tête de transaction : SET LOCAL lock_timeout = '2s'; "
                "SET LOCAL statement_timeout = '30s';",
            )
    if dialect == "mysql":
        if re.search(r"\balter\s+table\b", low) and "algorithm" not in low:
            add(
                "medium",
                "ALTER TABLE sans ALGORITHM/LOCK explicites : comportement de verrou "
                "imprévisible selon version.",
                "Vérifie le support INPLACE : ALTER TABLE ... ALGORITHM=INPLACE, "
                "LOCK=NONE ; sinon gh-ost / pt-online-schema-change.",
            )
        if "foreign_key_checks" in low:
            add(
                "critical",
                "SET FOREIGN_KEY_CHECKS=0 : désactive l'intégrité référentielle.",
                "À proscrire en production. L'intégrité des clés étrangères est un "
                "critère de validation non négociable.",
            )

    # --- Rejouabilité et réversibilité ---------------------------------------
    if re.search(r"\b(drop\s+table|drop\s+column|drop\s+index)\b", low) and "if exists" not in low:
        add(
            "medium",
            "DROP sans IF EXISTS : le script n'est pas rejouable.",
            "Ajoute IF EXISTS ; rejouabilité = condition d'un rollback propre.",
        )
    if re.search(r"\bcreate\s+(table|index)\b", low) and "if not exists" not in low:
        add(
            "medium",
            "CREATE sans IF NOT EXISTS : le script échoue à la seconde exécution.",
            "Ajoute IF NOT EXISTS (idiomatique sur CREATE TABLE / INDEX CONCURRENTLY).",
        )
    if not re.search(r"--\s*(down|rollback|retour)", low):
        add(
            "high",
            "Aucune section DOWN/rollback détectée.",
            "Joins un script de retour arrière testé : chaque migration doit être "
            "réversible, sinon le déploiement n'est pas sûr.",
        )
    if re.search(r"\bdrop\s+column\b", low):
        add(
            "medium",
            "DROP COLUMN immédiat : irréversible et verrou long.",
            "Pattern expand-contract : cesser d'écrire la colonne, déployer, "
            "backfill/zéro lecture vérifiés, puis DROP dans une migration suivante.",
        )

    checklist = [
        "Sauvegarde ou snapshot testé avant exécution (pg_dump de la table, mysqldump).",
        "Répétition du test sur une copie de production à taille réelle.",
        "Exécution en fenêtre de maintenance ou heures creuses.",
        "Plan de rollback connu étape par étape : savoir jusqu'où l'on peut revenir.",
        "ANALYZE (PG) / ANALYZE TABLE (MySQL) après les backfills pour rafraîchir "
        "les statistiques.",
    ]
    if dialect == "postgresql":
        checklist.insert(0, "SET LOCAL lock_timeout / statement_timeout en tête de transaction.")

    return {
        "dialect": dialect,
        "findings": findings,
        "pre_execution_checklist": checklist,
    }


# ---------------------------------------------------------------------------
# Environnement (sandbox) : docker en dev, Mistral en prod
# ---------------------------------------------------------------------------

def _build_environment():
    """Choisit le sandbox selon DBA_SANDBOX.

    mistral (défaut) : conteneur géré par Mistral — requis en production gérée,
        fonctionne aussi en local (création via MISTRAL_API_KEY, rôle cloud_user).
    docker : conteneur Docker local (LocalDockerSandbox) — développement et CI.
    bashkit : interpréteur shell embarqué, sans Python ni processus d'arrière-plan.
    none : aucun environnement (les skills, qui vivent dans le sandbox, sont omis).
    """
    mode = os.environ.get("DBA_SANDBOX", "mistral").strip().lower()
    if mode == "docker":
        return agents.LocalDockerSandbox()
    if mode == "bashkit":
        return agents.BashkitSandbox()
    if mode == "none":
        return None
    return agents.MistralSandbox()


_ENV = _build_environment()
_SKILL_NAMES = (
    "query-optimization",
    "index-strategy",
    "migration-safety",
    "caching-strategy",
)


def _load_skills() -> list:
    """Charge les skills locales ; NoSandbox ne peut pas contenir leurs fichiers."""
    if _ENV is None:
        return []
    return [
        agents.LocalSkillBundle.from_directory(SKILLS / name)
        for name in _SKILL_NAMES
        if (SKILLS / name / "SKILL.md").is_file()
    ]


agent_kwargs: dict = {}
if _ENV is not None:
    agent_kwargs["environment"] = _ENV
    agent_kwargs["skills"] = _load_skills()

agent = agents.Agent(
    model=MODEL,
    name=AGENT_NAME,
    instructions=INSTRUCTIONS,
    tools=[analyze_sql, detect_n_plus_one, validate_migration],
    **agent_kwargs,
)


# ---------------------------------------------------------------------------
# Essai local : uv run --env-file .env python main.py
# ---------------------------------------------------------------------------

async def main() -> None:
    demo = (
        "Analyse cette requête et propose un plan d'indexation pour PostgreSQL :\n"
        "SELECT * FROM orders o JOIN customers c ON o.customer_id = c.id\n"
        "WHERE LOWER(c.email) = 'client@exemple.com'\n"
        "ORDER BY o.created_at DESC LIMIT 10;"
    )
    result = await agent.run(demo)
    if isinstance(result, agents.Completed):
        print(result.text)
        print("\nOutils appelés :", [call.name for call in result.tool_calls])


if __name__ == "__main__":
    asyncio.run(main())
