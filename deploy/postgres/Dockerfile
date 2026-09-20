# Postgres with both extensions this project needs (task `P4-01`, §3).
#
# §3 chose one store deliberately — "queue + metadata + graph: Postgres (+
# Apache AGE). One battle-tested store, one recovery path, one backup routine."
# That only works if one image carries both pgvector and AGE, and no published
# image does: `pgvector/pgvector:pg17` has vectors and no graph, and the
# `apache/age` images have a graph and no vectors.
#
# So AGE is compiled here, on top of the pgvector image, and the result is what
# `make quickstart` starts. Nobody installs an extension by hand, and the
# development stack cannot drift from production by having one of the two.
#
# **The version line is per-PostgreSQL, not global.** Apache publishes AGE
# under `PG17/`, `PG18/` and so on, and `1.7.0` is the current PG17 release —
# the number is not a project-wide version and pinning it against the wrong
# major is the mistake this comment exists to prevent.
#
# Licence: Apache-2.0, read from the `LICENSE` inside the source tarball rather
# than recalled (`B-11`, docs/licences.md).

ARG PG_MAJOR=17
ARG AGE_VERSION=1.7.0

# --- builder ---------------------------------------------------------------
FROM pgvector/pgvector:pg17 AS builder

ARG PG_MAJOR
ARG AGE_VERSION

# `postgresql-server-dev-*` for the headers, flex and bison because AGE ships a
# grammar for Cypher and generates its parser at build time.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        build-essential \
        "postgresql-server-dev-${PG_MAJOR}" \
        bison \
        flex \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# The official Apache release, with its published checksum checked before
# anything is unpacked. §16 lists supply-chain exposure in the crawler stack as
# a real and ongoing risk; a source tarball compiled into the database is the
# one place in this stack where that would matter most, and verifying the
# digest costs one line.
RUN curl -fsSL -o age.tar.gz \
        "https://downloads.apache.org/age/PG${PG_MAJOR}/${AGE_VERSION}/apache-age-${AGE_VERSION}-src.tar.gz" \
    && curl -fsSL -o age.tar.gz.sha512 \
        "https://downloads.apache.org/age/PG${PG_MAJOR}/${AGE_VERSION}/apache-age-${AGE_VERSION}-src.tar.gz.sha512" \
    && sed "s#.*apache-age#age.tar.gz: #" age.tar.gz.sha512 > /dev/null \
    && echo "$(awk '{print $1}' age.tar.gz.sha512)  age.tar.gz" | sha512sum -c - \
    && tar -xzf age.tar.gz

RUN cd "apache-age-${AGE_VERSION}" \
    && make install

# --- runtime ---------------------------------------------------------------
FROM pgvector/pgvector:pg17 AS runtime

ARG PG_MAJOR
ARG AGE_VERSION

# Only the built artefacts. The compiler, the headers and the grammar tools
# stay in the builder — a database image carrying a toolchain is a larger
# attack surface for no benefit, and this is the one container holding every
# row the system has.
COPY --from=builder /usr/lib/postgresql/${PG_MAJOR}/lib/age.so \
     /usr/lib/postgresql/${PG_MAJOR}/lib/age.so
COPY --from=builder /usr/share/postgresql/${PG_MAJOR}/extension/age* \
     /usr/share/postgresql/${PG_MAJOR}/extension/

# The licence travels with the binary. `B-11`'s rule is that a verdict is read
# off an artefact, and an artefact that does not carry its licence cannot be
# re-checked later without downloading the source again.
COPY --from=builder /build/apache-age-${AGE_VERSION}/LICENSE \
     /usr/share/doc/apache-age/LICENSE
COPY --from=builder /build/apache-age-${AGE_VERSION}/NOTICE \
     /usr/share/doc/apache-age/NOTICE

# AGE needs to be in `shared_preload_libraries` before `CREATE EXTENSION` will
# work, and this is **half** the answer.
#
# `postgresql.conf.sample` is read only by `initdb`, so this covers a database
# created from this image and does nothing whatsoever for one that already
# exists — silently, which is the worst version. The compose files pass
# `-c shared_preload_libraries=age` for exactly that reason, and that flag is
# what actually guarantees it.
#
# This stays anyway: it means a container started straight from this image,
# with no compose file, is still usable — and the failure it prevents there is
# `CREATE EXTENSION age` erroring with "unhandled cypher(cstring) function
# call", which names nothing anybody can act on.
RUN echo "shared_preload_libraries = 'age'" \
    > /usr/share/postgresql/postgresql.conf.sample.age \
    && cat /usr/share/postgresql/postgresql.conf.sample.age \
    >> /usr/share/postgresql/postgresql.conf.sample \
    && rm /usr/share/postgresql/postgresql.conf.sample.age

LABEL org.opencontainers.image.description="PostgreSQL 17 with pgvector and Apache AGE"
