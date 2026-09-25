#!/bin/sh
set -eu

mkdir -p /app/data/torrserver/bin /app/experimental/data
if [ ! -x /app/data/torrserver/bin/TorrServer ]; then
    cp /opt/jf-resolve/torrserver/TorrServer /app/data/torrserver/bin/TorrServer
    chmod 755 /app/data/torrserver/bin/TorrServer
fi

secret_file=/app/data/.docker-secret
if [ ! -s "$secret_file" ]; then
    python -c 'import secrets; print(secrets.token_urlsafe(48))' > "$secret_file"
    chmod 600 "$secret_file"
fi
export SECRET_KEY="$(sed -n '1p' "$secret_file")"

exec "$@"
