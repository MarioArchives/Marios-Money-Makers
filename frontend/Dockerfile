# Multi-stage image for the React/Vite frontend.
#
#   dev   -> `docker compose up` (docker-compose.yml): Vite dev server on 5173.
#   build -> compiles the SPA to static files (dist/).
#   prod  -> docker-compose.prod.yml: Caddy serving dist/ and proxying /api
#            to the backend container (see Caddyfile).
FROM node:22-slim AS base
WORKDIR /app
COPY package.json package-lock.json ./

# ---------------------------------------------------------------- dev ----
FROM base AS dev
RUN npm install
COPY index.html vite.config.ts tsconfig.json tsconfig.app.json tsconfig.node.json ./
COPY public ./public
COPY src ./src
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# -------------------------------------------------------------- build ----
FROM base AS build
# Keep the build inside ~1 GB boxes (t3.micro + swap).
ENV NODE_OPTIONS=--max-old-space-size=768
RUN npm ci --no-audit --no-fund
COPY index.html vite.config.ts tsconfig.json tsconfig.app.json tsconfig.node.json ./
COPY public ./public
COPY src ./src
# Empty = same origin: the browser calls /api/... on the host that served
# the page, which Caddy proxies to the backend. Set to a full URL only if
# the API is hosted elsewhere.
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

# --------------------------------------------------------------- prod ----
FROM caddy:2-alpine AS prod
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
EXPOSE 80 443
