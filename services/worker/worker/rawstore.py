"""The raw store: where fetched bytes go, and whether they go anywhere (P1-11).

Link rot is the reason this exists (§5.4). Government URLs reorganise
constantly, and a citation that resolves to a 404 in three years is a citation
that cannot be checked — so a local copy plus a checksum is what keeps the
corpus honest about what it actually read.

**Not everything is kept.** §5.4 splits raw retention three ways, and the split
is the point: a Pi's NVMe cannot hold the HTML of every blog post the frontier
wanders into, and it does not need to. Primary sources — government, papers,
institutional reports — keep the file. Background sources keep their extracted
text and metadata, and the bytes are dropped once extraction has had them. Junk
is dropped by the novelty gate downstream and never reaches this module with
that tier already set unless an operator put it there.

**The path is derived from the URL, not from the content.** A re-fetch has to
land on the same path as the fetch before it, or the store grows a copy per
visit and nothing can find the previous one. So the name is
``sha256(url)`` and the *content* hash goes in the database instead, where it
answers a different question: has this page changed since we last read it.

**Every write is atomic.** Content goes to a temporary name in the same
directory and is then ``os.replace``d into place, which is atomic on POSIX. A
crash halfway through a 20MB PDF must not leave a truncated file that the
checksum beside it swears is complete — that is a corruption you only discover
years later, when the citation is the thing you needed.

The URL is attacker-influenced, so the path built from it is treated as
attacker-influenced too: the only characters that reach the filesystem are hex
digits and a sanitised domain, and the extension comes from an allowlist rather
than from anything the server said its file was called.
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

#: Retention tiers that keep the bytes. `background` keeps extracted text and
#: metadata only (§5.4), and `junk` keeps nothing — both still get a checksum,
#: which costs one hash of bytes already in memory and is what lets a later
#: fetch say "unchanged" without having the previous copy to compare against.
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

#: Retention tiers ordered by how much they keep. Retention only ever moves
#: *up* automatically, mirroring the quality-tier invariant in §11.12: a domain
#: an operator promoted to `primary` must not be silently demoted the next time
#: the mechanical mapping disagrees with them.
RETENTION_RANK = {"junk": 0, "background": 1, "primary": 2}

#: Media type → extension. An allowlist, not a guess: the extension is part of a
#: filesystem path, and `content-disposition` or a URL's own suffix is whatever
#: the far end felt like sending. Anything unrecognised gets `.bin`, which is
#: honest and harmless.
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

    ``path`` is None when the retention tier keeps no file. That is a decision,
    not a failure, so the checksum is present either way — the caller writes it
    to `sources.checksum` regardless and change detection keeps working for
    background sources that were never written to disk.
    """

    checksum: str
    retention_tier: str
    path: str | None = None
    bytes_written: int = 0

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

    ``current`` is the tier the source row already carries. Retention only moves
    up automatically — the same shape as §11.12's rule for quality tier, and for
    the same reason: an operator who promoted a domain to `primary` did so on
    purpose, and a mechanical mapping that disagreed with them next Tuesday
    would quietly start throwing the files away.
    """
    mechanical = RETENTION_BY_SOURCE_TIER.get(source_tier, DEFAULT_RETENTION_TIER)
    if current is None:
        return mechanical
    return max(mechanical, current, key=lambda tier: RETENTION_RANK.get(tier, -1))


def safe_domain(url: str) -> str:
    """The domain component of a path, or raise if it cannot be one safely.

    ``registrable_domain`` already lowercases and strips, so what is left should
    be a hostname. If it is not — an IDN that never got punycoded, an empty
    host, a `..` — that is refused rather than sanitised into something
    plausible, because a path built from a host nobody recognised is a path
    nobody can reason about later.
    """
    domain = registrable_domain(url)
    if not domain or len(domain) > 253 or not _SAFE_DOMAIN.match(domain):
        raise UnsafeRawPath(f"cannot build a raw path for host {domain!r}")
    return domain


def path_for(url: str, media_type: str | None = None) -> Path:
    """The store-relative path for ``url``. Deterministic, and inside the store.

    ``<domain>/<first two hex digits>/<sha256(url)><ext>``. The domain leads so
    that "everything from this site" is one directory — which is what both a
    takedown and a retention sweep actually need — and the hex shard keeps a
    single busy domain from becoming one directory with a hundred thousand
    entries in it.
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
    # The *relative* path is what goes in the database. An absolute one bakes in
    # `/data/raw` — the container's mount point, not the host's — and a store
    # moved to a bigger disk would invalidate every row that recorded one.
    return StoredRaw(
        checksum=digest,
        retention_tier=retention,
        path=str(relative),
        bytes_written=len(content),
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
