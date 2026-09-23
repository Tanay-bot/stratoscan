-- ── Stratoscan local stack — the two non-primary logical databases ──────────
--
-- Mounted at /docker-entrypoint-initdb.d/initdb.sql, so Postgres runs this
-- exactly once: during the first initialisation of an EMPTY data volume.
-- `make down` removes that volume (`docker compose down -v`), so the next
-- `make up` re-runs this file — which is what makes `make rebuild` a real
-- start-from-zero rather than a hopeful one.
--
-- The primary database (`stratoscan`) is created by POSTGRES_DB; this file adds
-- the other two of the three logical databases the app talks to.
--
-- These are THREE DATABASES ON ONE SERVER, which mirrors production only in
-- shape: production runs three separate Neon projects, one per DSN. Keeping the
-- three DSNs distinct locally is the point — a query that reaches the wrong
-- database fails here instead of on deploy day.
--
-- CREATE DATABASE cannot run inside a transaction and must run as a superuser;
-- both hold during the initdb phase, where POSTGRES_USER is the superuser.

CREATE DATABASE stratoscan_backtest;
CREATE DATABASE stratoscan_news;
