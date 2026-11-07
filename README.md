# kap-mistral-agents

**11 agents métier** pour le cycle de développement logiciel complet, construits avec le
[SDK Mistral Agents](https://docs.mistral.ai/studio/agents/overview) (`mistralai-agents`)
et déployables sur **Mistral Cloud** en déploiements gérés, pilotables depuis **Vibe Code**.

Chaque dossier de `agents/` est un **projet autonome** (un déploiement = un dossier).

## Les agents

| # | Agent | Dossier | Rôle | Sandbox par défaut |
|---|-------|---------|------|--------------------|
| 1 | Software Architect | `agents/software-architect` | Architecture technique, DAT, modèle de données, contrats d'API | aucune (conception) |
| 2 | Tech Lead | `agents/tech-lead` | Revues de code, découpage en tâches, ADR | aucune (conception) |
| 3 | UI/UX Designer | `agents/uiux-designer` | Parcours utilisateurs, wireframes, Design System (tokens) | aucune (conception) |
| 4 | Full-Stack Engineer | `agents/fullstack-developer` | Code de bout en bout, typé et testé | Mistral |
| 5 | Backend Developer | `agents/backend-developer` | APIs sécurisées, logique métier, RBAC | Mistral |
| 6 | Frontend Developer | `agents/frontend-developer` | Interfaces React/Next.js/TS accessibles et performantes | Mistral |
| 7 | Data Engineer | `agents/data-engineer` | Pipelines ETL/ELT, bases vectorielles, data lineage | Mistral |
| 8 | AI Specialist | `agents/ai-specialist` | RAG, prompts système, guardrails, évaluation des modèles | Mistral |
| 9 | DBA & Performance | `agents/dba-optimizer` | Optimisation SQL, indexation, migrations, cache | Mistral |
| 10 | DevOps Engineer | `agents/devops-engineer` | Dockerfiles, orchestration, CI/CD | Mistral |
| 11 | QA Engineer | `agents/qa-engineer` | Plans de test, E2E Playwright/Cypress, rapports de bugs | Mistral |

Les agents 1–3 sont des agents **de conception** : ils produisent des livrables textuels
(documents, ADR, wireframes, tokens) sans exécuter de code. Les agents 4–11 disposent
d'une **sandbox d'exécution** (Mistral par défaut, éventuellement `LocalDockerSandbox`
ou `BashkitSandbox` en local).

Le DBA (`agents/dba-optimizer`) embarque en plus **4 skills** (`skills/`) : optimisation
de requêtes, stratégie d'indexation, sécurité des migrations, stratégies de cache, et
**3 outils** : `analyze_sql`, `detect_n_plus_one`, `validate_migration`.

## Arborescence

```
kap-mistral-agents/
├── README.md
├── .env.example
└── agents/
    ├── software-architect/
    │   ├── main.py            # l'agent : `agent = agents.Agent(...)`
    │   ├── pyproject.toml     # entrypoint "main:agent" + dépendances
    │   ├── Dockerfile         # construit par le déploiement géré
    │   ├── .python-version    # 3.12
    │   ├── .env.example       # MISTRAL_API_KEY, AGENT_MODEL, AGENT_SANDBOX
    │   └── .gitignore / .dockerignore
    ├── tech-lead/            # même structure
    ├── ...                   # 9 autres agents, même structure
    └── dba-optimizer/
        ├── ...               # idem + 3 outils @agents.tool
        └── skills/           # 4 skills locales (SKILL.md)
```

## Prérequis

- [uv](https://docs.astral.sh/uv/) (gestionnaire de projet Python)
- Une clé API Mistral avec accès aux agents : https://console.mistral.ai/api-keys
- Pour le déploiement géré : un compte Mistral avec rôle **cloud_user** et le
  connecteur **GitHub** connecté dans Mistral Studio (https://console.mistral.ai/build/connectors)

## Développement local

```bash
cd agents/dba-optimizer        # (ou tout autre agent)
uv sync                        # installe les dépendances (.venv)
cp .env.example .env           # renseigner MISTRAL_API_KEY
uv run --env-file .env python main.py   # exécution du runner de démonstration
```

Variables utiles : `AGENT_MODEL` (défaut `mistral-large-4`), `AGENT_SANDBOX`
(`mistral` | `docker` | `bashkit` | `none`), et pour le DBA `DBA_MODEL` / `DBA_SANDBOX`.
En sandbox `docker`, `LocalDockerSandbox` exige un daemon Docker local.

> **Modèles dépréciés** : `devstral-2-25-12` et `mistral-medium-3-5-26-04` sont
> dépréciés. Le remplacement recommandé est **mistral-large-4**, utilisé par défaut.

## Déploiement géré sur Mistral Cloud

**Important** : `agents deploy` construit **le commit poussé** sur GitHub, pas la copie
locale. Le Dockerfile généré exécute `uv sync --frozen` : le fichier **`uv.lock` doit
donc être généré et commité** dans chaque dossier d'agent.

```bash
# 1. Dans CHAQUE dossier d'agent :
cd agents/<agent>
uv lock                          # génère uv.lock (à committer !)

# 2. Pousser le tout sur GitHub (branche main)

# 3. Déployer depuis chaque dossier :
cd agents/<agent>
uv run --env-file .env agents deploy   # suit les logs de build
```

Chaque agent devient un worker indépendant, sans clé API dans l'image : le
déploiement géré s'authentifie via son compte de service.

## Interagir avec les agents déployés

**Vibe Code (Web)** — conversation directe avec l'agent déployé :
`https://chat.mistral.ai/managed-agents/<nom-de-l-agent>`
(par exemple `https://chat.mistral.ai/managed-agents/dba-optimizer`)

**CLI** :
```bash
uv run --env-file .env agents chat <nom>     # conversation interactive
uv run --env-file .env agents logs <nom> --follow   # logs du worker
uv run --env-file .env agents list           # agents déployés
```

**Programmatique** :
```python
import asyncio
from mistralai import agents

async def main():
    agent = await agents.Agent.load_from(agent_name="dba-optimizer")
    result = await agent.run("Analyse cette requête SQL et propose un index.")
    print(result.text)

asyncio.run(main())
```

## Sécurité

- Aucun secret dans le code : tout passe par des variables d'environnement (`.env`
  gitignoré, `.env.example` versionné).
- Le Dockerfile fait tourner le worker en utilisateur non-root.
- Les instructions de chaque agent imposent requêtes paramétrées, validation des
  entrées et absence de fuite de données.
