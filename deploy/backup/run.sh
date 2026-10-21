#!/bin/sh
# Nightly backups (ARCHITECTURE §21): pg_dump, a Qdrant snapshot and a copy of the object store, encrypted with
# age when BACKUP_AGE_RECIPIENT is set, uploaded to BACKUP_S3_BUCKET (any S3-compatible store, ideally another
# provider or region), local copies kept for BACKUP_KEEP_DAYS.
#
#   BACKUP_HOUR_UTC        hour to run (default 2)
#   BACKUP_S3_ENDPOINT_URL, BACKUP_S3_BUCKET, BACKUP_S3_ACCESS_KEY, BACKUP_S3_SECRET_KEY, BACKUP_S3_REGION
#   BACKUP_AGE_RECIPIENT   an age public key (age1...); keep the private key OFF this server
#   BACKUP_KEEP_DAYS       local retention (default 7)
#   BACKUP_NOW=1           run once immediately and exit (used by `make backup`)
#
# Restore needs the same MASTER_KEY_BASE64: stored API keys and OAuth tokens are encrypted with it.
set -eu

HOUR="${BACKUP_HOUR_UTC:-2}"
KEEP="${BACKUP_KEEP_DAYS:-7}"

log() { echo "$(date -u +%FT%TZ) backup: $*"; }

seal() {  # encrypt in place when a recipient is configured
  if [ -n "${BACKUP_AGE_RECIPIENT:-}" ]; then
    age -r "$BACKUP_AGE_RECIPIENT" -o "$1.age" "$1" && rm -f "$1"
    echo "$1.age"
  else
    echo "$1"
  fi
}

qdrant() {  # method path
  curl -sSf -X "$1" -H "api-key: $QDRANT_API_KEY" "${QDRANT_URL:-http://qdrant:6333}$2"
}

upload() {
  [ -n "${BACKUP_S3_BUCKET:-}" ] || return 0
  AWS_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_KEY" \
    AWS_DEFAULT_REGION="${BACKUP_S3_REGION:-auto}" \
    aws ${BACKUP_S3_ENDPOINT_URL:+--endpoint-url "$BACKUP_S3_ENDPOINT_URL"} s3 cp "$1" "s3://$BACKUP_S3_BUCKET/$2/"
}

run_backup() {
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  dir="/backups/$stamp"
  mkdir -p "$dir"
  log "starting $stamp"

  pg_dump --format=custom --no-owner --file "$dir/postgres.dump"
  pg_file="$(seal "$dir/postgres.dump")"

  mkdir -p "$dir/qdrant"
  for coll in $(qdrant GET /collections | grep -o '"name":"[^"]*"' | cut -d'"' -f4); do
    snap="$(qdrant POST "/collections/$coll/snapshots" | sed -n 's/.*"name":"\([^"]*\)".*/\1/p')"
    qdrant GET "/collections/$coll/snapshots/$snap" >"$dir/qdrant/$coll.snapshot"
    qdrant DELETE "/collections/$coll/snapshots/$snap" >/dev/null
  done
  tar -C "$dir" -cf "$dir/qdrant.tar" qdrant && rm -rf "$dir/qdrant"
  qd_file="$(seal "$dir/qdrant.tar")"

  AWS_ACCESS_KEY_ID="$S3_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$S3_SECRET_KEY" AWS_DEFAULT_REGION="${S3_REGION:-us-east-1}" \
    aws --endpoint-url "$S3_ENDPOINT_URL" s3 sync --only-show-errors "s3://${S3_BUCKET:-intrv}" "$dir/objects"
  tar -C "$dir" -cf "$dir/objects.tar" objects && rm -rf "$dir/objects"
  obj_file="$(seal "$dir/objects.tar")"

  for f in "$pg_file" "$qd_file" "$obj_file"; do upload "$f" "$stamp"; done
  find /backups -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP" -exec rm -rf {} +
  log "done $stamp ($(du -sh "$dir" | cut -f1))"
}

if [ "${BACKUP_NOW:-0}" = "1" ]; then
  run_backup
  exit 0
fi

while true; do
  now="$(date -u +%s)"
  next="$(date -u -d "$(date -u +%Y-%m-%d) ${HOUR}:00:00" +%s 2>/dev/null || echo 0)"
  [ "$next" -gt "$now" ] || next=$((next + 86400))
  [ "$next" -gt "$now" ] || next=$((now + 86400))
  log "next run in $(( (next - now) / 60 )) min"
  sleep $((next - now))
  run_backup || log "FAILED (will retry tomorrow); check the logs above"
done
