# Dockerfile écrit d'après le modèle généré par `agents deploy` (SDK Mistral Agents).
# Les déploiements gérés le construisent tel quel ; il reste modifiable (paquets système, etc.).

# uv installe la version de Python demandée par le projet : pas besoin de Python dans l'image de base.
FROM ghcr.io/astral-sh/uv:0.11-debian-slim

# Le client Temporal du worker lit les certificats CA système, absents de l'image de base.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY . .
RUN uv sync --frozen --no-dev \
 && useradd --create-home --shell /usr/sbin/nologin agent \
 && chown -R agent:agent /app

# Le worker n'a pas besoin de root.
USER agent

# Le conteneur ne doit lancer QUE le worker : `agents deploy` a déjà enregistré l'agent.
CMD ["agents", "run-worker"]
