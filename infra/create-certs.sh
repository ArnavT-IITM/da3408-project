#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p infra/certs
if [ ! -f infra/certs/localhost.crt ] || [ ! -f infra/certs/localhost.key ]; then
    umask 077
    openssl req -x509 -newkey rsa:2048 -sha256 -days 365 -nodes \
        -keyout infra/certs/localhost.key -out infra/certs/localhost.crt \
        -subj '/CN=localhost' \
        -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1'
fi
printf '%s\n' 'Local TLS certificate ready. This self-signed certificate is for the local demo.'
