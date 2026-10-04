# Stratoscan — production image
#
# Build:  docker build -t stratoscan .
# Run:    docker run --env-file .env -p 8000:8000 stratoscan
#
# Installs from requirements-lock.txt (exact pins + SHA256 hashes), so every
# image build is bit-identical to the tested environment.

FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ── Dependencies (hashed lock, enforced) ──────────────────────────────
COPY requirements-lock.txt ./
RUN pip install --require-hashes --no-cache-dir -r requirements-lock.txt

# ── Application code ──────────────────────────────────────────────────
COPY src ./src
# Fonts for the share-card PNG renderer (src/share_card.py) — OFL, see assets/fonts.
COPY assets ./assets
COPY alembic ./alembic
COPY alembic.ini ./
COPY run.py ./
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

# ── Run as non-root (least privilege) ─────────────────────────────────
RUN useradd --create-home --uid 1000 stratoscan \
    && chown -R stratoscan:stratoscan /app
USER stratoscan

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=20s --start-period=15s --retries=3 \
    CMD ["/usr/local/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"]

# Entrypoint runs `alembic upgrade head` first so a fresh container never
# ships with a missing users table (see docker-entrypoint.sh).
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD []
