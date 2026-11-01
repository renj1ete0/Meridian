"""The document connector's gate and its checks on a file, without a database (task B-264).

The gate runs before routing, so every refusal here is reached without a database: that is
the property worth having, since a stranger's request must cost nothing. What needs
Postgres — the form's topic, the row, the queue task, the worker — is in
`tests/integration/test_connector.py`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from meridian_core.models import Upload
from meridian_core.uploads import (
    DEFAULT_MAX_MB,
    UPLOAD_MEDIA_TYPES,
    UploadRefused,
    check_content,
    inbox_relative_path,
    max_upload_bytes,
    media_type_for,
    read_upload,
    resolve_inbox_path,
    source_tier_for,
    source_url_for,
    upload_id_from_task,
)

TOKEN = "a-connector-token-long-enough"


@pytest.fixture
async def client(monkeypatch, tmp_path):
    from api.connector import create_connector_app

    monkeypatch.setenv("MERIDIAN_INBOX_ROOT", str(tmp_path / "inbox"))
    transport = httpx.ASGITransport(app=create_connector_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://connector.test") as c:
        yield c


def bearer(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# -- the gate fails closed ----------------------------------------------------


@pytest.mark.parametrize("configured", [None, "", "   ", "short-token"])
async def test_without_a_usable_token_every_request_is_refused(
    client, monkeypatch, configured
) -> None:
    """Fail closed: no token configured is not an open connector, whatever the caller sends.
    A token under the minimum length counts as none."""
    if configured is None:
        monkeypatch.delenv("MERIDIAN_CONNECTOR_TOKEN", raising=False)
    else:
        monkeypatch.setenv("MERIDIAN_CONNECTOR_TOKEN", configured)
    for method, path in (
        ("GET", "/connect/v1/wanted"),
        ("GET", "/connect/v1/documents/1"),
        ("POST", "/connect/v1/documents"),
        ("GET", "/connect/v1/openapi.json"),
    ):
        response = await client.request(method, path, headers=bearer(configured or "x"))
        assert response.status_code == 503, (method, path, response.status_code)
        assert "MERIDIAN_CONNECTOR_TOKEN" in response.json()["detail"]


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-token-of-similar-length"},
        {"Authorization": f"Basic {TOKEN}"},
        {"Authorization": f"Bearer {TOKEN}x"},
        {"Authorization": f"Bearer {TOKEN[:-1]}"},
        {"Authorization": "Bearer"},
    ],
)
async def test_a_wrong_or_missing_token_is_401(client, monkeypatch, headers) -> None:
    monkeypatch.setenv("MERIDIAN_CONNECTOR_TOKEN", TOKEN)
    response = await client.get("/connect/v1/wanted", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert TOKEN not in response.text


async def test_an_upload_over_the_cap_is_refused_before_it_is_read(
    client, monkeypatch, tmp_path
) -> None:
    """413 from the declared length, so the body is never spooled and nothing reaches the
    inbox or the database (there is no database here to reach)."""
    monkeypatch.setenv("MERIDIAN_CONNECTOR_TOKEN", TOKEN)
    monkeypatch.setenv("MERIDIAN_CONNECTOR_MAX_MB", "1")
    response = await client.post(
        "/connect/v1/documents",
        headers=bearer(),
        files={"file": ("big.pdf", b"%PDF-1.7\n" + b"x" * (3 * 1024 * 1024), "application/pdf")},
    )
    assert response.status_code == 413
    assert not (tmp_path / "inbox").exists() or not any((tmp_path / "inbox").rglob("*.pdf"))


async def test_the_unauthenticated_routes_are_only_health(client, monkeypatch) -> None:
    """Completeness: a route outside `/connect/` would skip the gate. Only `/health` may."""
    from fastapi.routing import APIRoute

    from api.connector import create_connector_app

    paths = set()
    for route in create_connector_app().routes:
        if isinstance(route, APIRoute):
            paths.add(route.path)
        elif hasattr(route, "effective_route_contexts"):
            paths |= {r.path for r in route.effective_route_contexts()}
        elif hasattr(route, "path"):
            paths.add(route.path)
    outside = {p for p in paths if not p.startswith("/connect/")}
    assert outside == {"/health"}, outside
    assert "/connect/v1/documents" in paths and "/connect/v1/wanted" in paths
    assert not any(p.startswith(("/api", "/mcp")) for p in paths)


# -- the file -------------------------------------------------------------------


def test_every_media_type_the_connector_accepts_is_one_the_worker_extracts() -> None:
    """Drift between two lists: a type accepted here and not routed by the worker would be
    stored, queued and then fail every time as "nothing could read this"."""
    from worker.extract.document import SUPPORTED_MEDIA_TYPES
    from worker.extract.document import supports as supports_document
    from worker.main import HTML_MEDIA_TYPES, PDF_MEDIA_TYPES

    for media_type in UPLOAD_MEDIA_TYPES:
        assert (
            media_type in HTML_MEDIA_TYPES
            or media_type in PDF_MEDIA_TYPES
            or supports_document(media_type)
        ), media_type
    # And the other way: what the worker can read, a sender can hand in.
    for media_type in HTML_MEDIA_TYPES | PDF_MEDIA_TYPES | SUPPORTED_MEDIA_TYPES:
        assert media_type_for(media_type, None) in UPLOAD_MEDIA_TYPES, media_type


@pytest.mark.parametrize(
    "declared,filename",
    [
        ("image/png", "x.png"),
        ("text/plain", "notes.txt"),
        ("application/zip", "x.zip"),
        ("application/octet-stream", "x.exe"),
        ("application/octet-stream", None),
        (None, "noextension"),
        ("application/msword", "old.doc"),
    ],
)
def test_a_type_nothing_extracts_is_refused_415(declared, filename) -> None:
    with pytest.raises(UploadRefused) as exc:
        media_type_for(declared, filename)
    assert exc.value.status == 415


def test_a_generic_type_is_decided_by_the_name_and_parameters_are_ignored() -> None:
    assert media_type_for("application/octet-stream", "Paper.PDF") == "application/pdf"
    assert media_type_for(None, "page.htm") == "text/html"
    assert media_type_for("text/html; charset=utf-8", "x.bin") == "text/html"
    assert media_type_for("application/csv", "t.csv") == "text/csv"


@pytest.mark.parametrize(
    "media_type,head",
    [
        ("application/pdf", b"<html>not a pdf</html>"),
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"%PDF-1.7"),
        ("text/html", b"%PDF-1.7 a pdf labelled as html"),
        ("text/html", b"PK\x03\x04zip labelled as html"),
        ("text/csv", b"a,b\x00,c"),
    ],
)
def test_bytes_that_are_plainly_not_the_declared_type_are_refused(media_type, head) -> None:
    with pytest.raises(UploadRefused) as exc:
        check_content(media_type, head)
    assert exc.value.status == 415


def test_bytes_that_match_their_type_pass() -> None:
    check_content("application/pdf", b"%PDF-1.7\n...")
    check_content("application/pdf", b"\xef\xbb\xbf  %PDF-1.4")  # leading junk is common
    check_content(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b"PK\x03\x04..."
    )
    check_content("text/html", b"<!doctype html><p>hi</p>")


@pytest.mark.parametrize("raw,expected", [("", DEFAULT_MAX_MB), ("1", 1), ("250", 250)])
def test_the_cap_comes_from_the_environment(monkeypatch, raw, expected) -> None:
    monkeypatch.setenv("MERIDIAN_CONNECTOR_MAX_MB", raw)
    assert max_upload_bytes() == expected * 1024 * 1024


@pytest.mark.parametrize("raw", ["0", "-5", "lots", "1.5"])
def test_a_nonsense_cap_is_refused_rather_than_guessed(monkeypatch, raw) -> None:
    monkeypatch.setenv("MERIDIAN_CONNECTOR_MAX_MB", raw)
    with pytest.raises(ValueError):
        max_upload_bytes()


def test_the_inbox_path_is_built_from_the_digest_only() -> None:
    sha = hashlib.sha256(b"x").hexdigest()
    assert inbox_relative_path(sha, "application/pdf") == f"{sha[:2]}/{sha}.pdf"
    for bad in ("../" + sha[3:], sha.upper(), sha[:-1], "/etc/passwd"):
        with pytest.raises(ValueError):
            inbox_relative_path(bad, "application/pdf")


def test_a_stored_path_cannot_escape_the_inbox(tmp_path: Path) -> None:
    root = tmp_path / "inbox"
    root.mkdir()
    for bad in ("../outside.pdf", "/etc/passwd", "aa/../../x", ""):
        with pytest.raises(ValueError):
            resolve_inbox_path(bad, root)


def test_an_inbox_file_that_changed_is_refused(tmp_path: Path) -> None:
    """The digest is the upload's identity; a file that no longer matches is not it."""
    content = b"<p>original</p>"
    sha = hashlib.sha256(content).hexdigest()
    relative = inbox_relative_path(sha, "text/html")
    (tmp_path / relative).parent.mkdir(parents=True)
    (tmp_path / relative).write_bytes(content)
    upload = Upload(sha256=sha, inbox_path=relative)
    assert read_upload(upload, tmp_path) == content
    (tmp_path / relative).write_bytes(b"<p>tampered</p>")
    with pytest.raises(ValueError):
        read_upload(upload, tmp_path)


# -- where an upload lands --------------------------------------------------------


def test_the_source_url_is_the_dois_link_else_the_digest() -> None:
    sha = "a" * 64
    assert source_url_for(Upload(sha256=sha, doi="10.1234/x")) == "https://doi.org/10.1234/x"
    assert source_url_for(Upload(sha256=sha, source_url="https://example.org/p")) == (
        f"upload://{sha}"
    ), "a sender's URL must never become the key: it could overwrite a crawled page's row"


def test_the_tier_is_the_senders_else_inferred_from_the_doi() -> None:
    assert source_tier_for(Upload(source_tier="government", doi="10.1/x")) == "government"
    assert source_tier_for(Upload(doi="10.1/x")) == "peer_reviewed"
    assert source_tier_for(Upload()) == "informal"


@pytest.mark.parametrize("raw", ["upload:", "upload:x1", "url:5", "upload:-1", "5", ""])
def test_a_task_that_does_not_name_an_upload_is_refused(raw) -> None:
    with pytest.raises(ValueError):
        upload_id_from_task(raw)
    assert upload_id_from_task("upload:42") == 42


# -- what the worker does with one ------------------------------------------------


def test_an_unreadable_upload_says_why() -> None:
    from worker.extract import ExtractedDocument
    from worker.main import _unusable

    assert "nothing could read" in _unusable(None, "application/pdf")
    empty = ExtractedDocument(extractor="pdftotext-failed")
    assert _unusable(empty, "application/pdf") == "no_text: pdftotext-failed found no text"
    scan = ExtractedDocument(extractor="pdftotext", needs_ocr=True)
    assert _unusable(scan, "application/pdf") is None, "a scan goes to OCR, as a fetched one does"
    good = ExtractedDocument(extractor="trafilatura", text="Some real text. " * 30)
    assert _unusable(good, "text/html") is None


def test_the_senders_title_and_doi_win_and_provenance_is_kept() -> None:
    from worker.extract import ExtractedDocument
    from worker.main import HandedIn

    handed = HandedIn(
        document=ExtractedDocument(),
        tier="informal",
        title="Given title",
        doi="10.1234/given",
        provenance={"upload_id": 7},
    )
    fields = handed.applied_to(
        {"title": "extracted", "doi": "10.9/other", "extra": {"title_from": "text", "k": 1}}
    )
    assert fields["title"] == "Given title" and fields["doi"] == "10.1234/given"
    assert fields["extra"] == {"k": 1, "upload": {"upload_id": 7}}
    kept = HandedIn(document=ExtractedDocument(), tier="informal").applied_to({"title": "t"})
    assert kept["title"] == "t" and "doi" not in kept
