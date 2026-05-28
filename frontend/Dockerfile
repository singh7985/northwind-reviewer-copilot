# --- frontend/Dockerfile ----------------------------------------------------
# Multi-stage build for the Next.js app. Uses standalone output for a small
# runtime image.

FROM node:20-alpine AS deps
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ ./
# NEXT_PUBLIC_* values are baked into the static JS bundle at build time,
# so this must be a build arg (not just a runtime env var) for the deployed
# frontend to talk to the right backend URL. The default points at the
# production Render API; local docker builds can override with
# --build-arg NEXT_PUBLIC_API_BASE=http://localhost:8000.
ARG NEXT_PUBLIC_API_BASE=https://northwind-api-w34f.onrender.com
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE \
    NEXT_TELEMETRY_DISABLED=1
# Cache-bust marker — bump when you need to force a fresh JS bundle build
# (e.g. after changing NEXT_PUBLIC_API_BASE). Render's docker layer cache
# keys off the ARG value above, but bumping this is a belt-and-braces fix.
ARG BUILD_REV=2
RUN echo "build_rev=$BUILD_REV api_base=$NEXT_PUBLIC_API_BASE" && npm run build

FROM node:20-alpine AS runner
WORKDIR /app
# Don't hardcode PORT — Render (and most PaaS providers) inject $PORT at
# runtime, and Next.js' standalone server reads it via process.env.PORT.
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0

# Copy only what's needed at runtime
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

# Document the default port for local docker runs; Render overrides via $PORT.
EXPOSE 3000

CMD ["node", "server.js"]
