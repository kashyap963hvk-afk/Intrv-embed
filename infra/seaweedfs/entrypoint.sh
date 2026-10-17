#!/bin/sh
# Development S3 gateway: SeaweedFS single-node server with one S3 identity taken from the
# environment. Production uses a managed S3 (R2/AWS) or a properly configured cluster.
set -eu

: "${S3_ACCESS_KEY:?S3_ACCESS_KEY is required}"
: "${S3_SECRET_KEY:?S3_SECRET_KEY is required}"

case "$S3_ACCESS_KEY$S3_SECRET_KEY" in
  *[!A-Za-z0-9_+/=-]*) echo "S3 credentials may only contain [A-Za-z0-9_+/=-]" >&2; exit 1 ;;
esac

mkdir -p /etc/seaweedfs /data
cat > /etc/seaweedfs/s3.json <<JSON
{"identities": [{"name": "intrv",
  "credentials": [{"accessKey": "${S3_ACCESS_KEY}", "secretKey": "${S3_SECRET_KEY}"}],
  "actions": ["Admin", "Read", "Write", "List", "Tagging"]}]}
JSON

exec weed server -dir=/data -s3 -s3.port=8333 -s3.config=/etc/seaweedfs/s3.json
