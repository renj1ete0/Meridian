"""Documents handed in through the connector, and the papers it asks for (task B-264).

The connector validates a form, keeps the file in the inbox, records an `uploads` row and
queues an `upload` task; the worker reads the file through the same extraction, chunking
and source path a fetched document takes. Nothing here parses a document.
See docs/features/connector-api.md.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .doi import DoiError, normalise_doi
from .models import TopicConfig, Upload
from .models.source import SOURCE_TIER
from .queueing import enqueue
from .schemas.uploads import WantedPage, WantedPaper
from .storable import storable

#: What `worker.main` records on a DOI no provider had a legal copy of.
NO_COPY = "no_open_access_copy"

#: Where an upload waits in the queue: with an operator's own seed, ahead of the crawl.
UPLOAD_PRIORITY = 100

DEFAULT_INBOX_ROOT = "/data/inbox"
DEFAULT_MAX_MB = 100
MAX_TITLE_CHARS = 1_000
MAX_DESCRIPTION_CHARS = 10_000
MAX_URL_CHARS = 2_048

PDF = "application/pdf"
HTML = "text/html"
XHTML = "application/xhtml+xml"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
CSV = "text/csv"

#: Media type → the extension the inbox file gets. The keys are exactly what the worker
#: extracts; `tests/unit/test_upload_media_types.py` holds the two together.
UPLOAD_MEDIA_TYPES: dict[str, str] = {
    PDF: ".pdf",
    HTML: ".html",
    XHTML: ".html",
    DOCX: ".docx",
    XLSX: ".xlsx",
    PPTX: ".pptx",
    CSV: ".csv",
}

#: A file sent as `application/octet-stream` is typed by its name's extension.
BY_EXTENSION = {
    ".pdf": PDF,
    ".html": HTML,
    ".htm": HTML,
    ".xhtml": XHTML,
    ".docx": DOCX,
    ".xlsx": XLSX,
    ".pptx": PPTX,
    ".csv": CSV,
}

#: Declared types that say nothing, so the file name decides.
GENERIC_MEDIA_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})

#: Office Open XML files are zip containers.
_ZIP_MAGIC = b"PK\x03\x04"
_OOXML = frozenset({DOCX, XLSX, PPTX})


class UploadRefused(ValueError):
    """The upload is not accepted; ``status`` is the HTTP status that says why."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclasses.dataclass(frozen=True)
class UploadFields:
    """What the sender said about the document, validated."""

    doi: str | None = None
    title: str | None = None
    description: str | None = None
    topic: str | None = None
    source_url: str | None = None
    source_tier: str | None = None


# -- configuration -----------------------------------------------------------


def inbox_root() -> Path:
    """Where handed-in files are kept: `MERIDIAN_INBOX_ROOT`, else `/data/inbox`."""
    return Path(os.environ.get("MERIDIAN_INBOX_ROOT") or DEFAULT_INBOX_ROOT)


def max_upload_bytes() -> int:
    """The size cap, from `MERIDIAN_CONNECTOR_MAX_MB` (default 100). Raises on nonsense."""
    raw = (os.environ.get("MERIDIAN_CONNECTOR_MAX_MB") or "").strip()
    if not raw:
        return DEFAULT_MAX_MB * 1024 * 1024
    try:
        megabytes = int(raw)
    except ValueError as exc:
        raise ValueError(f"MERIDIAN_CONNECTOR_MAX_MB must be a whole number, got {raw!r}") from exc
    if megabytes < 1:
        raise ValueError(f"MERIDIAN_CONNECTOR_MAX_MB must be at least 1, got {megabytes}")
    return megabytes * 1024 * 1024


# -- the file ----------------------------------------------------------------


def media_type_for(declared: str | None, filename: str | None) -> str:
    """The media type to store, from the part's declared type or, when generic, the name.

    Raises `UploadRefused` (415) for anything the worker could not extract.
    """
    kind = (declared or "").split(";", 1)[0].strip().lower()
    if kind in GENERIC_MEDIA_TYPES:
        kind = BY_EXTENSION.get(Path(filename or "").suffix.lower(), "")
    if kind == "application/csv":
        kind = CSV
    if kind not in UPLOAD_MEDIA_TYPES:
        allowed = ", ".join(sorted(UPLOAD_MEDIA_TYPES))
        raise UploadRefused(
            415, f"{declared or 'no media type'!s} cannot be read here; send one of: {allowed}"
        )
    return kind


def check_content(media_type: str, head: bytes) -> None:
    """Refuse bytes that are plainly not the declared type (415). ``head`` is the first KB."""
    if media_type == PDF:
        if b"%PDF-" not in head[:1024]:
            raise UploadRefused(415, "declared a PDF, but the file does not start like one")
    elif media_type in _OOXML:
        if not head.startswith(_ZIP_MAGIC):
            raise UploadRefused(415, f"declared {media_type}, but the file is not a zip container")
    elif b"\x00" in head or head.startswith((b"%PDF-", _ZIP_MAGIC)):
        raise UploadRefused(415, f"declared {media_type}, but the file is binary")


def inbox_relative_path(sha256: str, media_type: str) -> str:
    """``<two hex digits>/<sha256><ext>``: only the digest and an allowlisted extension."""
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise ValueError(f"not a sha256 hex digest: {sha256!r}")
    return f"{sha256[:2]}/{sha256}{UPLOAD_MEDIA_TYPES[media_type]}"


def resolve_inbox_path(relative: str, root: Path | None = None) -> Path:
    """Absolute path of a stored upload, refusing anything that escapes the inbox."""
    base = (root or inbox_root()).resolve()
    candidate = (base / relative).resolve()
    if base not in candidate.parents:
        raise ValueError(f"{relative!r} resolves outside the inbox")
    return candidate


def read_upload(upload: Upload, root: Path | None = None) -> bytes:
    """The upload's bytes, checked against its digest. Raises OSError or ValueError."""
    content = resolve_inbox_path(upload.inbox_path, root).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != upload.sha256:
        raise ValueError(f"inbox file does not match its digest (found {digest[:12]}…)")
    return content


# -- the form ----------------------------------------------------------------


def _text(value: str | None, name: str, limit: int) -> str | None:
    value = storable((value or "").strip()) or None
    if value is not None and len(value) > limit:
        raise UploadRefused(422, f"{name} is longer than {limit} characters")
    return value


async def validate_fields(
    sess: AsyncSession,
    *,
    doi: str | None = None,
    title: str | None = None,
    description: str | None = None,
    topic: str | None = None,
    source_url: str | None = None,
    source_tier: str | None = None,
) -> UploadFields:
    """Check every field the sender gave; raises `UploadRefused` (422) naming the first bad one.

    The DOI is normalised as the resolver normalises it, the topic must exist, the tier must
    be one `sources.source_tier` accepts, and a source URL must be http(s) with a host.
    """
    normalised_doi = None
    if doi is not None and doi.strip():
        try:
            normalised_doi = normalise_doi(doi)
        except DoiError as exc:
            raise UploadRefused(422, f"doi: {exc}") from exc

    topic = _text(topic, "topic", 200)
    if topic is not None and await sess.get(TopicConfig, topic) is None:
        raise UploadRefused(422, f"topic: no topic named {topic!r}")

    tier = _text(source_tier, "source_tier", 50)
    if tier is not None and tier not in SOURCE_TIER.enums:
        raise UploadRefused(
            422, f"source_tier: {tier!r} is not one of {', '.join(SOURCE_TIER.enums)}"
        )

    url = _text(source_url, "source_url", MAX_URL_CHARS)
    if url is not None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise UploadRefused(422, "source_url: must be an http(s) URL with a host")

    return UploadFields(
        doi=normalised_doi,
        title=_text(title, "title", MAX_TITLE_CHARS),
        description=_text(description, "description", MAX_DESCRIPTION_CHARS),
        topic=topic,
        source_url=url,
        source_tier=tier,
    )


# -- the row -----------------------------------------------------------------


async def find_by_sha(sess: AsyncSession, sha256: str) -> Upload | None:
    """The upload already holding these bytes, or None."""
    return await sess.scalar(select(Upload).where(Upload.sha256 == sha256))


async def record_upload(
    sess: AsyncSession,
    fields: UploadFields,
    *,
    sha256: str,
    filename: str,
    media_type: str,
    size_bytes: int,
    client_address: str | None = None,
    user_agent: str | None = None,
) -> Upload:
    """Write the `uploads` row and its `upload` task. Flushes; the caller commits."""
    upload = Upload(
        sha256=sha256,
        filename=storable(filename)[:500] or "upload",
        media_type=media_type,
        size_bytes=size_bytes,
        inbox_path=inbox_relative_path(sha256, media_type),
        doi=fields.doi,
        title=fields.title,
        description=fields.description,
        topic=fields.topic,
        source_url=fields.source_url,
        source_tier=fields.source_tier,
        client_address=client_address,
        user_agent=storable(user_agent)[:500] if user_agent else None,
    )
    sess.add(upload)
    await sess.flush()
    task = await enqueue(
        sess,
        f"upload:{upload.upload_id}",
        topic=fields.topic,
        seed_source="upload",
        task_type="upload",
        priority=UPLOAD_PRIORITY,
    )
    upload.task_id = task.task_id
    await sess.flush()
    return upload


def upload_id_from_task(url_or_query: str) -> int:
    """The upload an `upload` task names (``upload:<id>``). Raises ValueError otherwise."""
    prefix, _, number = url_or_query.partition(":")
    if prefix != "upload" or not number.isdigit():
        raise ValueError(f"not an upload task: {url_or_query!r}")
    return int(number)


def source_url_for(upload: Upload) -> str:
    """The `sources.url` an upload is stored under: its DOI's link, else its digest.

    The sender's `source_url` is provenance only, never the key: it could name a page the
    crawl already holds, and the upload would overwrite that page's row.
    """
    if upload.doi:
        return f"https://doi.org/{upload.doi}"
    return f"upload://{upload.sha256}"


def source_tier_for(upload: Upload) -> str:
    """The sender's tier; else `peer_reviewed` for a paper with a DOI; else `informal`."""
    if upload.source_tier:
        return upload.source_tier
    return "peer_reviewed" if upload.doi else "informal"


async def settle_upload(
    sess: AsyncSession,
    upload_id: int,
    *,
    status: str,
    source_id: int | None = None,
    error: str | None = None,
) -> None:
    """Mark an upload done or failed. Flushes; the caller commits."""
    values: dict[str, object] = {"status": status, "error": error, "updated_at": func.now()}
    if source_id is not None:
        values["source_id"] = source_id
    await sess.execute(update(Upload).where(Upload.upload_id == upload_id).values(**values))
    await sess.flush()


# -- what the corpus is missing ------------------------------------------------

# The reason is inlined, not bound, so the planner can use the partial index on it.
_WANTED_DOIS = text(
    f"""
    WITH wanted AS (
        SELECT q.url_or_query AS doi,
               min(q.task_id) AS cursor,
               min(q.created_at) AS first_seen,
               max(q.fetched_at) AS last_tried,
               (array_agg(q.topic ORDER BY q.task_id)
                    FILTER (WHERE q.topic IS NOT NULL))[1] AS topic,
               (array_agg(q.parent_source_id ORDER BY q.task_id)
                    FILTER (WHERE q.parent_source_id IS NOT NULL))[1] AS cited_from
          FROM queue q
         WHERE q.task_type = 'doi' AND q.status = 'done' AND q.error = '{NO_COPY}'
         GROUP BY q.url_or_query
        HAVING min(q.task_id) > :after
    )
    SELECT w.doi, w.cursor, w.first_seen, w.last_tried, w.topic, w.cited_from,
           c.url AS cited_from_url,
           (SELECT s.title FROM sources s
             WHERE s.doi = w.doi AND s.title IS NOT NULL
             ORDER BY s.source_id LIMIT 1) AS title
      FROM wanted w
      LEFT JOIN sources c ON c.source_id = w.cited_from
     WHERE NOT EXISTS (SELECT 1 FROM sources s WHERE s.doi = w.doi AND s.text_available)
       AND NOT EXISTS (SELECT 1 FROM uploads u WHERE u.doi = w.doi AND u.status <> 'failed')
     ORDER BY w.cursor
     LIMIT :limit
    """
)


async def wanted_dois(sess: AsyncSession, *, after: int = 0, limit: int = 100) -> WantedPage:
    """DOIs no provider had a copy of that the corpus still lacks, oldest first.

    "Lacks" means no source with the DOI holds text and no upload for it is queued or done.
    Keyset-paged on the first queue row's id; ``next`` is the cursor for the page after.
    """
    rows = (await sess.execute(_WANTED_DOIS, {"after": after, "limit": limit + 1})).all()
    more = len(rows) > limit
    rows = rows[:limit]
    items = [
        WantedPaper(
            doi=row.doi,
            title=row.title,
            topic=row.topic,
            cited_from_source_id=row.cited_from,
            cited_from_url=row.cited_from_url,
            first_seen=row.first_seen,
            last_tried=row.last_tried,
            reason=NO_COPY,
        )
        for row in rows
    ]
    return WantedPage(kind="doi", items=items, next=str(rows[-1].cursor) if more else None)
