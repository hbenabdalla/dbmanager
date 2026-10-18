# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# build identifier shown next to the version on Platform -> About
ARG VITE_BUILD_VERSION=""
ENV VITE_BUILD_VERSION=${VITE_BUILD_VERSION}
RUN npm run build

# Unprivileged nginx (listens on 8080, runs as uid 101, OpenShift compatible)
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
LABEL org.opencontainers.image.title="database-manager-frontend"
ENV BACKEND_URL=http://backend:8000
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD wget -q -O /dev/null http://127.0.0.1:8080/nginx-health || exit 1
