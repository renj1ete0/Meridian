# Base images

What every image is built on or pulled as, and why. The rule (ADR 0025): **Alpine when every
wheel the image installs is published for musl on Python 3.14, Debian slim (trixie) when one
is not.** Nothing is compiled from source to make Alpine fit.

Sizes are the uncompressed image as `docker images` reports it, measured on x86_64 on
2026-11-01 (`B-266`). "Before" is the previous Dockerfile with the same locked dependencies,
except the worker, postgres and crawl4ai, which are the images the local stack was already
running.

Every Python runtime stage removes the base image's pip (the app runs from a uv-built venv,
and pip's vendored packages are what a scanner flags), and every Alpine runtime stage runs
`apk upgrade` for fixes published since the base was built.

## Built here

| Image | Dockerfile | Base | Size before → after | Why this base |
|---|---|---|---|---|
| api | `services/api/Dockerfile` | `python:3.14-alpine3.24` | 439 → 343 MB | Every wheel in `meridian-api`'s closure (asyncpg, pydantic-core, uvloop, cryptography, numpy…) exists for musllinux on cp314 |
| tools | `deploy/tools/Dockerfile` | `python:3.14-alpine3.24` | 453 → 342 MB | `meridian-core` plus alembic: all musl wheels. Runs as uid 1001 |
| orchestrator | `services/orchestrator/Dockerfile` | `python:3.14-slim-trixie` | 812 → 821 MB | **Not Alpine:** onnxruntime (markitdown → magika) publishes no musl wheel and no source distribution |
| worker | `services/worker/Dockerfile` | `python:3.14-slim-trixie` | 10.0 → 10.1 GB | **Not Alpine:** torch, triton and onnxruntime publish no musl wheels; poppler comes from Debian |
| web | `web/Dockerfile` | `node:26-alpine` → `nginx:1.30-alpine-slim` | 77 → 23 MB | Static files behind nginx's stable line. Runs as `nginx` (uid 101), not root |
| postgres | `deploy/postgres/Dockerfile` | `pgvector/pgvector:0.8.7-pg17-bookworm` | 634 → 634 MB | **Not trixie yet:** a glibc upgrade changes collation order under existing text indexes; moving needs a planned `REINDEX`. Pinned explicitly so the upstream default cannot move it silently |
| crawl4ai | `deploy/crawl4ai/Dockerfile` | `unclecode/crawl4ai:0.9.5` | 6.0 → 8.2 GB (upstream) | Upstream's image plus `config.yml` |

`uv` is copied into the Python builders from `ghcr.io/astral-sh/uv:0.13.6`.

## Pulled as they are

| Service | Image | Why pinned this way |
|---|---|---|
| datadirs | `alpine:3.24` | Runs one `chown` as root with no network. A shell is all it needs |
| searxng | `searxng/searxng:2026.11.1-9ded3bec1` | Upstream publishes dated builds and no releases. A dated tag means every machine runs the same one; bump it deliberately and check a search still answers |
| cloudflared | `cloudflare/cloudflared:2026.10.7` | Release tag |
| watchtower | `nickfedor/watchtower:1.23.2` | Release tag; also in `deploy/embedder-node/` |

## Checking a change

- **Wheels for musl:** export the image's closure (`uv export --frozen --no-dev --package
  <pkg>`) and look in `uv.lock` for a `musllinux` cp314 (or abi3) wheel for x86_64 and
  aarch64, or a pure-Python one, for every binary package. One missing means `slim-trixie`.
- **Vulnerabilities:** Trivy, run as a container (`aquasec/trivy`), `image` on each built
  image and `fs` on the repository. The last results are in
  [handover.md](../handover.md#dependency-audit-2026-11-01).
