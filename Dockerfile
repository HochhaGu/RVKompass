# RVKompass – Container für den Live-Betrieb (P8). Anleitung: docs/deployment.md
#
# Python-Version wie in der Werkstatt (.venv, in der die Tests laufen): 3.12.3.
FROM python:3.12.3-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/app/data/hf-cache

# cron für den monatlichen Abgleich; setpriv (util-linux) ist im Basisimage enthalten
RUN apt-get update \
    && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/* \
    && command -v setpriv

FROM base AS app

WORKDIR /app

# Exakte Versionen aus der Sperrdatei. torch ist die CPU-Variante (torch==…+cpu), die es nur im
# PyTorch-Index gibt – daher der zusätzliche Index. Keine CUDA-Pakete.
COPY requirements.lock ./
RUN pip install --no-cache-dir \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements.lock

# Nutzer ohne Root für gunicorn und die Cron-Jobs. UID/GID passend zum Eigentümer von
# /srv/rvkompass auf dem Server wählen (docker compose build --build-arg APP_UID=…).
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd --gid "${APP_GID}" rvkompass \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --home-dir /app --no-create-home \
        --shell /usr/sbin/nologin rvkompass

COPY app ./app
COPY scripts ./scripts
COPY eval ./eval
COPY docker ./docker

# Cron-Einträge laufen als Nutzer rvkompass (Nutzerfeld in /etc/cron.d)
RUN install -m 0644 docker/crontab /etc/cron.d/rvkompass \
    && chmod 0755 docker/entrypoint.sh \
    && mkdir -p /app/data /app/logs \
    && chown rvkompass:rvkompass /app/data /app/logs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=8).status == 200 else 1)"

# Startet cron (als root) und danach gunicorn als Nutzer rvkompass
ENTRYPOINT ["/app/docker/entrypoint.sh"]
