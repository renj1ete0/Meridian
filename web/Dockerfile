# The Explore and Admin interface (task B-05, scaffold §5, §6).
#
# `docker-compose.yml` has referenced `build: { context: ./web }` since it was
# written and this file was not here, so the production stack could not actually
# be brought up whole. Written now because `docker-compose.local.yml` needs the
# UI to exist for a local run to be worth anything.
#
# Two stages: Node builds, nginx serves. The runtime carries no Node at all —
# the output of `vite build` is static files, and shipping the toolchain that
# produced them would mean an image an order of magnitude larger, containing a
# package manager, on a machine that fetches hostile pages.
#
# Multi-arch by construction (scaffold §5): both bases publish manifest lists,
# and nothing in the build is architecture-specific.

# --- builder ---------------------------------------------------------------
FROM node:22-slim AS builder

WORKDIR /app

# The lockfile before the source, for the same reason the Python images do it:
# dependencies change once a release and the source changes every commit.
# `npm ci` rather than `npm install` — it installs exactly the lockfile and
# fails if package.json disagrees with it, which is the property that makes a
# container build reproducible.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# `tsc -b && vite build` (the package's own `build` script). Type errors fail
# the image rather than shipping: this is the last point at which a mismatch
# between the client types and the DTOs is cheap to find.
RUN npm run build

# --- runtime ---------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

# The app calls `/api/...` relative and there is no base URL anywhere in the
# source (`P2-11`), which is what makes one build correct in every environment.
# In development Vite's dev server proxies those calls; in a container nginx
# has to, or every request 404s against the static root.
COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 8080

# Static files and a proxy, so liveness is the whole question — there is no
# database connection behind this to be separately broken, unlike the API's
# healthcheck which deliberately checks more than the process.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["nginx", "-t"]

STOPSIGNAL SIGQUIT
