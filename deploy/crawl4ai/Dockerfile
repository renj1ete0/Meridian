# Crawl4AI, pinned and configured (`P1-26`, spec §6.4).
#
# docker-compose.yml has referenced an image for this since `P0-01` and there
# was never a Dockerfile behind it. Thin on purpose: the upstream image is a
# 6GB browser pool and rebuilding it here would mean maintaining a Playwright
# install. What this adds is the pin and the configuration.
#
# §6.4 is specific about why the version matters. Crawl4AI's Docker API had
# critical vulnerabilities fixed in 0.8.7 (RCE, SSRF, auth bypass, arbitrary
# file write, hardcoded JWT secret), a compromised dependency replaced in
# 0.8.6, and was made secure-by-default only in 0.9.0. That is heavy churn for
# the one component in this stack whose whole job is rendering hostile content,
# so the version is pinned here rather than floated, and moving it is a
# deliberate act with a changelog entry.
FROM unclecode/crawl4ai:0.9.2

# The server-side configuration. Untrusted request bodies cannot set the
# powerful fields — 0.9.x filters them, which is exactly the hardening above —
# so anything this deployment wants applied to every crawl belongs here.
COPY config.yml /app/config.yml

# Documents the port the health check and the worker use. Publishing it is the
# compose file's decision, and it does not.
EXPOSE 11235
