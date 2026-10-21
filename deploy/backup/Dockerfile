# Backup runner: pg_dump 16 + aws-cli + age + curl. Built once by `make up-prod`.
FROM postgres:16-alpine
RUN apk add --no-cache aws-cli age curl
COPY run.sh /usr/local/bin/intrv-backup
ENTRYPOINT ["/bin/sh", "/usr/local/bin/intrv-backup"]
