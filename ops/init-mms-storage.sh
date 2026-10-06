#!/bin/sh
set -eu
mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
mc mb --ignore-existing local/virgo-mms
mc anonymous set none local/virgo-mms
mc admin policy create local virgo-mms-attachments /init/mms-storage-policy.json
mc admin user add local "$MINIO_APP_USER" "$MINIO_APP_PASSWORD"
mc admin policy attach local virgo-mms-attachments --user "$MINIO_APP_USER"
