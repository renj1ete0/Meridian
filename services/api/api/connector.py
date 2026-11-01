"""The document connector (task B-264): what the corpus is missing, and documents handed back.

A separate app on its own port, mounting only `/connect/v1/*` and `/health`: none of
Explore, Admin or MCP. Every `/connect/` request needs `Authorization: Bearer
<MERIDIAN_CONNECTOR_TOKEN>`, checked before a byte of the body is read; with no token
configured every one is refused. Run as ``uvicorn api.connector:app``.
See docs/features/connector-api.md.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import IntegrityError

from meridian_core.db import check_connection, dispose_engines
from meridian_core.logging import configure_logging, get_logger
from meridian_core.models import Upload
from meridian_core.schemaready import wait_for_schema
from meridian_core.schemas.uploads import UploadRead, WantedPage
from meridian_core.uploads import (
    UploadRefused,
    check_content,
    find_by_sha,
    inbox_relative_path,
    inbox_root,
    max_upload_bytes,
    media_type_for,
    record_upload,
    validate_fields,
    wanted_dois,
)

from .deps import ReadSession, WriteSession
from .requestlog import log_requests

log = get_logger(__name__)

PREFIX = "/connect/v1"
TOKEN_ENV = "MERIDIAN_CONNECTOR_TOKEN"

#: Shorter than this and the token is treated as unset: a guessable token is no token.
MIN_TOKEN_CHARS = 16

#: Room in a request for the form's other fields and multipart framing, above the file cap.
FORM_OVERHEAD_BYTES = 1024 * 1024

#: How much of a file is read at a time while it is hashed and written to the inbox.
CHUNK_BYTES = 1024 * 1024


def gate(request: Request) -> JSONResponse | None:
    """The refusal for a `/connect/` request, or None to let it through.

    503 when no token is configured (fail closed), 401 for a missing or wrong one, then
    for an upload 411 without a length and 413 over the cap. The token is never logged.
    """
    expected = (os.environ.get(TOKEN_ENV) or "").strip()
    if len(expected) < MIN_TOKEN_CHARS:
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    f"The document connector is not configured: set {TOKEN_ENV} to a random "
                    f"string of at least {MIN_TOKEN_CHARS} characters."
                )
            },
        )
    scheme, _, given = (request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        given.strip().encode(), expected.encode()
    ):
        return JSONResponse(
            status_code=401,
            content={"detail": "A valid bearer token is required."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    if request.method == "POST":
        length = request.headers.get("content-length")
        if length is None or not length.isdigit():
            return JSONResponse(
                status_code=411, content={"detail": "An upload needs a Content-Length."}
            )
        if int(length) > max_upload_bytes() + FORM_OVERHEAD_BYTES:
            return _too_large()
    return None


def _too_large() -> JSONResponse:
    cap = max_upload_bytes() // (1024 * 1024)
    return JSONResponse(
        status_code=413, content={"detail": f"The file is larger than the {cap} MB cap."}
    )


async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Refuse a `/connect/` request before routing, so no body is parsed for a stranger."""
    if request.url.path.startswith("/connect/"):
        refusal = gate(request)
        if refusal is not None:
            return refusal
    return await call_next(request)


router = APIRouter(prefix=PREFIX, tags=["connector"])


@router.get("/wanted", response_model=WantedPage)
async def wanted(
    sess: ReadSession,
    kind: Literal["doi"] = "doi",
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    after: Annotated[str | None, Query(pattern=r"^\d{1,19}$")] = None,
) -> WantedPage:
    """Papers Meridian found and could not retrieve: no provider had a legal copy."""
    return await wanted_dois(sess, after=int(after) if after else 0, limit=limit)


@router.get("/documents/{upload_id}", response_model=UploadRead)
async def document(upload_id: int, sess: ReadSession) -> UploadRead:
    """One handed-in document: its status, its source once read, its error if it failed."""
    upload = await sess.get(Upload, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail=f"No upload {upload_id}.")
    return UploadRead.model_validate(upload)


def _upload_response(upload: Upload, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=UploadRead.model_validate(upload).model_dump(mode="json"),
    )


async def _store(upload_file: UploadFile, cap: int) -> tuple[Path, str, int, bytes]:
    """Stream the part into the inbox's incoming folder, hashing as it goes.

    Returns the temporary path, the digest, the size and the first KB. Raises
    `UploadRefused` (413) past the cap, removing what it wrote.
    """
    incoming = inbox_root() / ".incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    temporary = incoming / uuid.uuid4().hex
    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        with temporary.open("wb") as out:
            while chunk := await upload_file.read(CHUNK_BYTES):
                size += len(chunk)
                if size > cap:
                    raise UploadRefused(413, f"The file is larger than the {cap // 2**20} MB cap.")
                if len(head) < 1024:
                    head += chunk[: 1024 - len(head)]
                digest.update(chunk)
                out.write(chunk)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary, digest.hexdigest(), size, head


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure logging, wait for the schema, and close the pool on the way down."""
    configure_logging("connector")
    max_upload_bytes()  # a malformed cap stops the service here, not on the first upload
    if len((os.environ.get(TOKEN_ENV) or "").strip()) < MIN_TOKEN_CHARS:
        log.warning("connector has no usable token; every request will be refused")
    log.info("connector starting", extra={"inbox": str(inbox_root())})
    await wait_for_schema()
    try:
        yield
    finally:
        await dispose_engines()
        log.info("connector stopped")


@router.post("/documents", response_model=UploadRead, status_code=202)
async def hand_in(
    request: Request,
    sess: WriteSession,
    file: Annotated[UploadFile, File(description="The document")],
    doi: Annotated[str | None, Form()] = None,
    title: Annotated[str | None, Form()] = None,
    description: Annotated[str | None, Form()] = None,
    topic: Annotated[str | None, Form()] = None,
    source_url: Annotated[str | None, Form()] = None,
    source_tier: Annotated[str | None, Form()] = None,
) -> Response:
    """Accept a document; it enters the corpus as a crawled document would.

    202 with the new upload, or 200 with the existing one when these bytes were sent
    before (the new form's fields are then ignored).
    """
    try:
        fields = await validate_fields(
            sess,
            doi=doi,
            title=title,
            description=description,
            topic=topic,
            source_url=source_url,
            source_tier=source_tier,
        )
        media_type = media_type_for(file.content_type, file.filename)
        temporary, sha256, size, head = await _store(file, max_upload_bytes())
    except UploadRefused as exc:
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})
    try:
        if size == 0:
            raise UploadRefused(422, "file: the file is empty")
        check_content(media_type, head)
        existing = await find_by_sha(sess, sha256)
        if existing is not None:
            return _upload_response(existing, 200)
        destination = inbox_root() / inbox_relative_path(sha256, media_type)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, destination)
        upload = await record_upload(
            sess,
            fields,
            sha256=sha256,
            filename=Path(file.filename or "upload").name,
            media_type=media_type,
            size_bytes=size,
            client_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        await sess.commit()
    except UploadRefused as exc:
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})
    except IntegrityError:
        # The same bytes, sent twice at once: the other request's row is the answer.
        await sess.rollback()
        existing = await find_by_sha(sess, sha256)
        if existing is None:
            raise
        return _upload_response(existing, 200)
    finally:
        temporary.unlink(missing_ok=True)
    await sess.refresh(upload)
    log.info(
        "document handed in",
        extra={
            "upload_id": upload.upload_id,
            "bytes": size,
            "media_type": media_type,
            "doi": fields.doi,
            "topic": fields.topic,
        },
    )
    return _upload_response(upload, 202)


def create_connector_app() -> FastAPI:
    """The connector's app: the gate, its three routes and `/health`, nothing else."""
    app = FastAPI(
        title="Meridian document connector",
        version=os.environ.get("MERIDIAN_VERSION", "0"),
        summary="What the corpus is missing, and documents handed back.",
        lifespan=lifespan,
        # The schema sits behind the token like the routes; no browsable docs on a public port.
        openapi_url=f"{PREFIX}/openapi.json",
        docs_url=None,
        redoc_url=None,
    )
    app.middleware("http")(guard)
    app.middleware("http")(log_requests)

    app.include_router(router)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, object]:
        """Liveness plus whether the database answers. Needs no token; reveals nothing."""
        return {"status": "ok", "database": await check_connection("ro")}

    return app


app = create_connector_app()
