# Redwood Retail app: the mobile storefront and the Redwood Console, served by
# the FastAPI backend that already knows how to serve frontend/dist.
#
# Built from the repository root, because server.py imports firestore_auth,
# retail_catalog and customer_profiles from there and order_engine reaches the
# demo personas through customer_profiles.

# ------------------------------------------------------------------------------
# Stage 1: the Vite bundle. Two entry points, index.html and console.html,
# exactly as vite.config.ts declares them -- the console is a second HTML entry
# rather than a route, which is why it ends up in dist/ as a real file.
# ------------------------------------------------------------------------------
# Node 24 to match the toolchain the project is developed against. node:20-slim
# ships npm 10.8, which hit "Exit handler never called!" on this lockfile --
# and, worse, still exited 0, so the build sailed on to `npm run build` and
# failed there with the far less informative "tsc: not found".
FROM node:24-slim AS frontend

WORKDIR /build

# Lockfile first so npm ci is cached across source-only changes.
#
# `npm ci` fetches each tarball from the `resolved` URL recorded in the
# lockfile, so those URLs must point at registry.npmjs.org. Regenerating the
# lockfile behind a corporate npm mirror rewrites all 187 of them to an
# internal host, which resolves nowhere from Cloud Build and fails the build
# with ECONNREFUSED. Public URLs work in both places: npm's default
# `replace-registry-host=npmjs` maps them back to whatever mirror a developer
# has configured.
#
# The `test -x` guards the step above it: npm has been observed failing and
# still exiting 0, in which case the build sails on and fails two steps later
# with the far less informative "tsc: not found".
COPY mobile_client/frontend/package.json mobile_client/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund && test -x node_modules/.bin/tsc

COPY mobile_client/frontend/ ./
RUN npm run build

# ------------------------------------------------------------------------------
# Stage 2: the API, serving the bundle the first stage produced.
# ------------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY mobile_client/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The repository-root modules the backend imports. Listed rather than copied
# wholesale so the image does not carry the seeders, the agent package or the
# Terraform state.
COPY firestore_auth.py retail_catalog.py customer_profiles.py ./
# mobile_client itself has no __init__.py -- it is an implicit namespace
# package, and only mobile_client.backend carries one. Copying the directory is
# enough for the import to resolve.
COPY mobile_client/backend/ ./mobile_client/backend/

COPY --from=frontend /build/dist ./mobile_client/frontend/dist

EXPOSE 8080

# One worker, deliberately. console_service holds the SSE Broadcaster, the
# browser-clock telemetry and the recent-order buffer in process memory, so a
# second worker would serve a console that cannot see half the events. This is
# the same reason the Cloud Run service is pinned to a single instance.
#
# Concurrency comes from the event loop instead, which is the right axis for a
# service whose load is long-lived SSE streams rather than CPU.
CMD exec uvicorn mobile_client.backend.server:app \
      --host 0.0.0.0 \
      --port ${PORT:-8080} \
      --workers 1 \
      --timeout-graceful-shutdown 5
