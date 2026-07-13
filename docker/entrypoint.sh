#!/bin/bash
# whisper-api container entrypoint. TLS stays opt-in per spec §4.2: plain HTTP
# unless both SSL_CERTFILE and SSL_KEYFILE are set. When they are set but the
# cert file is missing (nothing mounted at those paths), generate a self-signed
# pair there so the container still comes up serving HTTPS; DOMAIN sets the CN.
set -euo pipefail

if [[ -n "${SSL_CERTFILE:-}" && -n "${SSL_KEYFILE:-}" && ! -f "$SSL_CERTFILE" ]]; then
  mkdir -p "$(dirname "$SSL_CERTFILE")" "$(dirname "$SSL_KEYFILE")"
  openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout "$SSL_KEYFILE" \
    -out "$SSL_CERTFILE" \
    -subj "/CN=${DOMAIN:-llm.example.int}" \
    -days 365
fi

exec python -m whisper_api "$@"
