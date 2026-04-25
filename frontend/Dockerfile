# =============================================================================
# FEAnalyzer — frontend image
#
# Multi-stage:
#   1. ``builder`` installs npm dependencies and runs ``vite build``.
#   2. ``runtime`` is an Nginx image that serves the built SPA and also
#      acts as the reverse proxy for /api/, /admin/, /healthz/ and the
#      Django static/media volumes. This replaces the stand-alone nginx
#      service from Stage 1 so we only run one edge container.
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1 — build the SPA
# -----------------------------------------------------------------------------
FROM node:20-alpine AS builder

WORKDIR /app

# Install dependencies first so Docker can cache them across source edits.
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

COPY . .

# VITE_API_BASE_URL can be overridden at build time if the SPA is ever
# hosted on a different origin than the API.
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run build


# -----------------------------------------------------------------------------
# Stage 2 — serve the SPA + reverse-proxy the API via Nginx
# -----------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

RUN rm -f /etc/nginx/conf.d/default.conf \
    && mkdir -p /etc/nginx/snippets /var/www/static /var/www/media

COPY nginx/nginx.conf           /etc/nginx/conf.d/feanalyzer.conf
COPY nginx/proxy_headers.conf   /etc/nginx/snippets/feanalyzer_proxy_headers.conf

COPY --from=builder /app/dist   /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
