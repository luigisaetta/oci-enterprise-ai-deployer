# Author: L. Saetta
# Version: 0.9.0
# Last modified: 2026-05-10
# License: MIT

FROM node:22-bookworm-slim AS deps

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-bookworm-slim AS builder

WORKDIR /app

ARG NEXT_PUBLIC_DEPLOYER_API_URL=http://localhost:8100
ARG NEXT_PUBLIC_DEPLOYER_API_KEY=
ENV NEXT_PUBLIC_DEPLOYER_API_URL=${NEXT_PUBLIC_DEPLOYER_API_URL}
ENV NEXT_PUBLIC_DEPLOYER_API_KEY=${NEXT_PUBLIC_DEPLOYER_API_KEY}
ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-bookworm-slim AS runner

WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=builder /app ./

EXPOSE 3000

CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0"]
