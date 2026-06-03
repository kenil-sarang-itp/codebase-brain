# CodeBase Brain — frontend image.
# Multi-stage: build the React app with Node, then serve the static bundle
# with Nginx. The final image contains no Node toolchain — just static files.

# --- Stage 1: build ------------------------------------------------------
FROM node:22-slim AS build

WORKDIR /app

# Install dependencies first (cached unless package files change).
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund

# Build the production bundle.
COPY frontend/ ./
RUN npm run build

# --- Stage 2: serve ------------------------------------------------------
FROM nginx:1.27-alpine

# Replace the default site config with our SPA + API-proxy config.
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

# Copy the built static assets from the build stage.
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
