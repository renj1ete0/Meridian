# The dev database image with libfaketime, so Postgres's now() can be moved too.
FROM meridian/postgres:pg17-age
USER root
RUN apt-get update && apt-get install -y --no-install-recommends faketime \
    && rm -rf /var/lib/apt/lists/*
