# BCP Operations AI — static web UI served by nginx.
# Multi-arch base: runs on the arm64 GB10 and on x86_64.
FROM nginx:1.27-alpine

# nginx.conf is a template: the entrypoint substitutes ${API_UPSTREAM} into conf.d/default.conf at start.
COPY nginx.conf /etc/nginx/templates/default.conf.template
ENV API_UPSTREAM=host.docker.internal:8030
COPY index.html /usr/share/nginx/html/
COPY css/  /usr/share/nginx/html/css/
COPY js/   /usr/share/nginx/html/js/
COPY data/ /usr/share/nginx/html/data/

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
