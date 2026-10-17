#!/bin/sh
# Creates the two application roles and the application database (runs once, on an empty volume).
#   app_owner: owns the schema, used only by migrations.
#   app_rw:    runtime role; NOT the table owner, so Row-Level Security applies to it (Phase 1).
set -eu

: "${APP_OWNER_DB_PASSWORD:?APP_OWNER_DB_PASSWORD is required}"
: "${APP_RW_DB_PASSWORD:?APP_RW_DB_PASSWORD is required}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner_pw="$APP_OWNER_DB_PASSWORD" -v rw_pw="$APP_RW_DB_PASSWORD" <<'SQL'
CREATE ROLE app_owner LOGIN PASSWORD :'owner_pw';
CREATE ROLE app_rw LOGIN PASSWORD :'rw_pw';
CREATE DATABASE intrv OWNER app_owner;
REVOKE ALL ON DATABASE intrv FROM PUBLIC;
GRANT CONNECT ON DATABASE intrv TO app_rw;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname intrv <<'SQL'
ALTER SCHEMA public OWNER TO app_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO app_rw;
SQL
