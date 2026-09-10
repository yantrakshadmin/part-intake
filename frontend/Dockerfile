# Build the Vite SPA, then serve it from Caddy (which also proxies /api).
# Caddyfile is NOT baked in: deploy/docker-compose.prod.yml bind-mounts
# deploy/Caddyfile so basic-auth rotation needs no image rebuild.
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM caddy:2-alpine
COPY --from=build /app/dist /srv
