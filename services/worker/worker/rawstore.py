"""The raw store: where fetched bytes go, and whether they go anywhere (P1-11).

Primary sources keep the file; background and junk keep only a checksum. The path is
derived from the URL (``sha256(url)``), never the content, every write is atomic, and
only hex digits, a sanitised domain and an allowlisted extension reach the filesystem.
See docs/features/source-quality.md#the-raw-store.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import os
import re
import tempfile
from pathlib import Path

from meridian_core.logging import get_logger
from meridian_core.tiering import registrable_domain

log = get_logger(__name__)

#: Where the store lives. The compose files bind-mount the host's
#: ``$DATA_ROOT/raw`` here; development overrides it to somewhere writable.
DEFAULT_RAW_ROOT = "/data/raw"

CHECKSUM_ALGORITHM = "sha256"

#: Retention tiers that keep the bytes. The others still get a checksum, which lets
#: a later fetch say "unchanged" without the previous copy.
KEEPS_RAW_FILE = frozenset({"primary"})

#: Source tier → retention tier (§5.4's table). `junk` is deliberately not
#: reachable from here: it is the novelty gate's verdict on a near-duplicate,
#: not a property of the domain, and nothing at fetch time knows it yet.
RETENTION_BY_SOURCE_TIER = {
    "peer_reviewed": "primary",
    "government": "primary",
    "institutional": "primary",
    "press": "background",
    "informal": "background",
}
DEFAULT_RETENTION_TIER = "background"

#: Retention tiers ordered by how much they keep. Retention only ever moves *up*
#: automatically (§11.12).
RETENTION_RANK = {"junk": 0, "background": 1, "primary": 2}

#: Media type → extension. An allowlist, because the extension is part of a path;
#: anything unrecognised gets `.bin`.
EXTENSIONS = {
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "application/pdf": ".pdf",
    "application/json": ".json",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "application/rss+xml": ".xml",
    "application/atom+xml": ".xml",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/epub+zip": ".epub",
}
DEFAULT_EXTENSION = ".bin"

# A domain component that is safe to put in a path. DNS names are letters,
# digits, hyphens and dots, so anything else means the host was not a hostname
# and the caller should hear about it rather than get a path built from it.
_SAFE_DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")


class UnsafeRawPath(ValueError):
    """The URL could not be turned into a path that stays inside the store."""


@dataclasses.dataclass(frozen=True)
class StoredRaw:
    """What the store did with one fetch.

    ``path`` is None when the retention tier keeps no file. The checksum is present
    either way, and the caller writes it to `sources.checksum` regardless.
    """

    checksum: str
    retention_tier: str
    path: str | None = None
    bytes_written: int = 0
    #: The base ``path`` is relative to (task P1-45). Provenance, never used to
    #: resolve a path. See docs/features/source-quality.md#the-raw-store.
    root: str | None = None

    @property
    def kept(self) -> bool:
        return self.path is not None


def raw_root(root: str | os.PathLike[str] | None = None) -> Path:
    """The store's base directory, from the argument, the environment, or the default."""
    return Path(root or os.environ.get("MERIDIAN_RAW_ROOT") or DEFAULT_RAW_ROOT)


def checksum_for(content: bytes) -> str:
    """``sha256:<hex>`` for ``content``.

    Prefixed with the algorithm deliberately. A bare hex string is a checksum
    nobody can verify in five years without first working out what produced it,
    and the whole point of storing one is that it is still checkable then.
    """
    digest = hashlib.new(CHECKSUM_ALGORITHM, content).hexdigest()
    return f"{CHECKSUM_ALGORITHM}:{digest}"


def extension_for(media_type: str | None) -> str:
    """The file extension for a media type, from the allowlist."""
    if not media_type:
        return DEFAULT_EXTENSION
    return EXTENSIONS.get(media_type.split(";", 1)[0].strip().lower(), DEFAULT_EXTENSION)


def retention_for(source_tier: str, current: str | None = None) -> str:
    """The retention tier for a source, never lower than one already set.

    ``current`` is the tier the source row already carries; retention only moves up
    automatically. See docs/features/source-quality.md#the-raw-store.
    """
    mechanical = RETENTION_BY_SOURCE_TIER.get(source_tier, DEFAULT_RETENTION_TIER)
    if current is None:
        return mechanical
    return max(mechanical, current, key=lambda tier: RETENTION_RANK.get(tier, -1))


def safe_domain(url: str) -> str:
    """The domain component of a path, or raise if it cannot be one safely.

    Anything that is not a plain hostname (an IDN never punycoded, an empty host,
    a `..`) is refused rather than sanitised into something plausible.
    """
    domain = registrable_domain(url)
    if not domain or len(domain) > 253 or not _SAFE_DOMAIN.match(domain):
        raise UnsafeRawPath(f"cannot build a raw path for host {domain!r}")
    return domain


def path_for(url: str, media_type: str | None = None) -> Path:
    """The store-relative path for ``url``. Deterministic, and inside the store.

    ``<domain>/<first two hex digits>/<sha256(url)><ext>``: one directory per site,
    sharded so a busy site does not become one huge directory.
    """
    domain = safe_domain(url)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return Path(domain) / digest[:2] / f"{digest}{extension_for(media_type)}"


def store(
    url: str,
    content: bytes,
    *,
    source_tier: str,
    media_type: str | None = None,
    current_retention: str | None = None,
    root: str | os.PathLike[str] | None = None,
) -> StoredRaw:
    """Checksum ``content``, and write it if its retention tier keeps files.

    Returns what happened either way. A background source is not an error and
    not a partial success — it is the retention policy working, and the caller
    needs the checksum from it exactly as much as from a primary one.
    """
    retention = retention_for(source_tier, current_retention)
    digest = checksum_for(content)

    if retention not in KEEPS_RAW_FILE:
        log.debug(
            "raw bytes not retained",
            extra={"url": url, "retention_tier": retention, "source_tier": source_tier},
        )
        return StoredRaw(checksum=digest, retention_tier=retention)

    relative = path_for(url, media_type)
    base = raw_root(root)
    destination = base / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_atomically(destination, content)

    log.info(
        "raw stored",
        extra={
            "url": url,
            "path": str(relative),
            "bytes": len(content),
            "retention_tier": retention,
            "checksum": digest,
        },
    )
    # The *relative* path goes in the database; `root` beside it is provenance only.
    # See docs/features/source-quality.md#the-raw-store.
    return StoredRaw(
        checksum=digest,
        retention_tier=retention,
        path=str(relative),
        bytes_written=len(content),
        root=str(base),
    )


def resolve(relative: str, root: str | os.PathLike[str] | None = None) -> Path:
    """Absolute path for a stored file, refusing anything that escapes the store.

    `sources.raw_file_path` is written by this module today and by whatever
    imports a corpus snapshot tomorrow, so the containment check belongs on the
    read as well as the write.
    """
    base = raw_root(root).resolve()
    candidate = (base / relative).resolve()
    if candidate != base and base not in candidate.parents:
        raise UnsafeRawPath(f"{relative!r} resolves outside the raw store")
    return candidate


def _write_atomically(destination: Path, content: bytes) -> None:
    """Write via a temporary file in the same directory, then rename.

    Same directory because ``os.replace`` is only atomic within a filesystem,
    and ``/tmp`` is frequently a different one. The fsync is what makes the
    guarantee survive power loss rather than just a crashed process.
    """
    handle, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".tmp-")
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, destination)
    except BaseException:
        # Including cancellation: a temporary file left behind by a shutdown
        # would accumulate one per interrupted fetch, in a directory nothing
        # ever sweeps.
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
