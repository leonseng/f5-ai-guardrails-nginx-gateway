FROM nginx:alpine

# NJS is a dynamic module on Alpine-based images; install it explicitly.
RUN apk add --no-cache nginx-module-njs ca-certificates

# Copy nginx config and NJS module.
COPY nginx/nginx.conf    /etc/nginx/nginx.conf
COPY nginx/guardrails.js /etc/nginx/njs/guardrails.js
COPY nginx/helpers.js    /etc/nginx/njs/helpers.js
