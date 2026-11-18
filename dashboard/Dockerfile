FROM node:24-bookworm-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --no-audit
COPY dashboard/app ./app
COPY dashboard/lib ./lib
COPY dashboard/next.config.ts dashboard/tsconfig.json ./
RUN npm run build

FROM node:24-bookworm-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000

COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static

USER node
EXPOSE 3000
CMD ["node", "server.js"]
