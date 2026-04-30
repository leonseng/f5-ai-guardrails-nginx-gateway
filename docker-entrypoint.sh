#!/bin/sh
set -e

# Set defaults
export OPENAI_API_URL=${OPENAI_API_URL:-http://localhost:11434}\

# Substitute env vars and write final config
envsubst '${OPENAI_API_URL}' \
  < /etc/nginx/templates/nginx.conf.template \
  > /etc/nginx/nginx.conf

echo "Generated /etc/nginx/nginx.conf:"
cat /etc/nginx/nginx.conf

# Hand off to the CMD
exec "$@"