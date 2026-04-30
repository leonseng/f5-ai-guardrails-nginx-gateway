FROM nginx:alpine

# NJS is a dynamic module on Alpine-based images; install it explicitly.
RUN apk add --no-cache nginx-module-njs ca-certificates gettext

# Copy nginx config and NJS module.
COPY nginx/nginx.conf.template /etc/nginx/templates/nginx.conf.template
COPY nginx/guardrails.js /etc/nginx/njs/guardrails.js
COPY nginx/helpers.js    /etc/nginx/njs/helpers.js
COPY docker-entrypoint.sh /docker-entrypoint.sh

RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]