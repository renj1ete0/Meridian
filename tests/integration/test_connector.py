"""The document connector against a real Postgres, end to end (task B-264).

Through the connector's own ASGI app, which opens its own connections, so the fixtures
**commit** and clean up after themselves by a per-run marker (as `test_explore_api.py`
does). The worker part runs a real `Worker` over the committed rows, inside the test's own
transaction, and drives an upload to a committed `sources` row with chunks — the read-back
`docs/guides/connectors.md` §3 asks of every new task type.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import delete, func, select, text

from meridian_core.chunks import chunk_count
from meridian_core.db import dispose_engines
from meridian_core.models import FetchPolicy, QueueTask, Source, TopicConfig, Upload
from meridian_core.models.queue import SEED_SOURCE, TASK_TYPE
from meridian_core.models.uploads import UPLOAD_STATUS
from meridian_core.sources import upsert_source
from meridian_core.uploads import NO_COPY, UPLOAD_PRIORITY
from worker.crawl import Crawler
from worker.extract.pdf import available as pdf_available
from worker.fetch import Fetcher
from worker.main import Worker, WorkerSettings
from worker.ratelimit import DomainLimiter

pytestmark = pytest.mark.usefixtures("require_db")

TOKEN = "integration-connector-token-0123456789"

PROSE = (
    "The committee met twice during the quarter and agreed a revised schedule for the "
    "programme. Its report sets out the reasons for the change, the costs of each option "
    "considered, and the measures by which progress will be judged next year. "
) * 4


def article(marker: str, title: str = "A handed-in report") -> bytes:
    return (
        f"<!doctype html><html><head><title>{title}</title></head><body><article>"
        f"<h1>{title}</h1><p>{marker}. {PROSE}</p><p>{PROSE}</p></article></body></html>"
    ).encode()


@pytest.fixture
def marker() -> str:
    return f"cx{uuid.uuid4().hex[:10]}"


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    root = tmp_path / "inbox"
    root.mkdir()
    monkeypatch.setenv("MERIDIAN_INBOX_ROOT", str(root))
    monkeypatch.setenv("MERIDIAN_RAW_ROOT", str(tmp_path / "raw"))
    monkeypatch.setenv("MERIDIAN_CONNECTOR_TOKEN", TOKEN)
    return root


@pytest.fixture
async def client(inbox):
    from api.connector import create_connector_app

    transport = httpx.ASGITransport(app=create_connector_app())
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://connector.test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as c:
        yield c
    await dispose_engines()


@pytest.fixture
async def topic(session_for, marker):
    """A committed topic the form may name, and everything this run made, removed after."""
    name = f"test_connector_{marker}"
    sess = await session_for("rw")
    sess.add(TopicConfig(topic=name, weight=0.0, floor=0.0, ceiling=1.0, status="active"))
    await sess.commit()
    yield name
    await sess.rollback()
    await sess.execute(delete(Upload).where(Upload.topic == name))
    await sess.execute(delete(Upload).where(Upload.filename.like(f"{marker}%")))
    await sess.execute(delete(QueueTask).where(QueueTask.topic == name))
    await sess.execute(delete(Source).where(Source.url.like(f"https://{marker}.test/%")))
    await sess.execute(delete(Source).where(Source.doi.like(f"10.99999/{marker}%")))
    await sess.execute(delete(TopicConfig).where(TopicConfig.topic == name))
    await sess.commit()


def doi(marker: str, suffix: str) -> str:
    return f"10.99999/{marker}-{suffix}"


async def hand_in(client, content: bytes, *, name: str, media_type: str = "text/html", **form):
    return await client.post(
        "/connect/v1/documents",
        files={"file": (name, content, media_type)},
        data={k: v for k, v in form.items() if v is not None},
    )


# -- accepting a document ------------------------------------------------------


async def test_a_document_is_kept_recorded_and_queued(
    client, inbox, topic, marker, session_for
) -> None:
    body = article(marker)
    response = await hand_in(
        client,
        body,
        name=f"{marker}.html",
        doi=f"https://doi.org/{doi(marker, 'A').upper()}",
        title="  Given title  ",
        description="sent by a test",
        topic=topic,
        source_url="https://example.org/landing",
    )
    assert response.status_code == 202, response.text
    made = response.json()
    sha = hashlib.sha256(body).hexdigest()
    assert made["status"] == "queued" and made["sha256"] == sha
    assert made["doi"] == doi(marker, "a"), "normalised as the resolver normalises"
    assert made["title"] == "Given title"
    assert "inbox_path" not in made

    sess = await session_for("ro")
    row = await sess.get(Upload, made["upload_id"])
    assert row.media_type == "text/html" and row.size_bytes == len(body)
    assert (inbox / row.inbox_path).read_bytes() == body
    assert not any((inbox / ".incoming").iterdir()), "the temporary copy is gone"
    task = await sess.get(QueueTask, row.task_id)
    assert (task.task_type, task.seed_source, task.status) == ("upload", "upload", "pending")
    assert task.url_or_query == f"upload:{row.upload_id}"
    assert task.topic == topic and task.priority == UPLOAD_PRIORITY

    status = await client.get(f"/connect/v1/documents/{row.upload_id}")
    assert status.status_code == 200 and status.json()["status"] == "queued"


async def test_the_same_bytes_again_return_the_first_upload(
    client, topic, marker, session_for
) -> None:
    body = article(marker)
    first = await hand_in(client, body, name=f"{marker}-1.html", topic=topic)
    again = await hand_in(client, body, name=f"{marker}-2.html", title="different")
    assert first.status_code == 202 and again.status_code == 200
    assert again.json()["upload_id"] == first.json()["upload_id"]
    assert again.json()["title"] is None, "the second form is ignored, not merged"
    sess = await session_for("ro")
    sha = hashlib.sha256(body).hexdigest()
    assert await sess.scalar(select(func.count()).where(Upload.sha256 == sha)) == 1
    assert (
        await sess.scalar(
            select(func.count()).where(
                QueueTask.url_or_query == f"upload:{first.json()['upload_id']}"
            )
        )
        == 1
    )


@pytest.mark.parametrize(
    "form,needle",
    [
        ({"doi": "not-a-doi"}, "doi"),
        ({"doi": "10.1234/has?query"}, "doi"),
        ({"topic": "no_such_topic_anywhere"}, "topic"),
        ({"source_tier": "excellent"}, "source_tier"),
        ({"source_url": "javascript:alert(1)"}, "source_url"),
        ({"source_url": "file:///etc/passwd"}, "source_url"),
        ({"title": "x" * 1001}, "title"),
    ],
)
async def test_a_bad_field_is_refused_and_nothing_is_kept(
    client, inbox, topic, marker, session_for, form, needle
) -> None:
    body = article(marker)
    response = await hand_in(client, body, name=f"{marker}.html", **form)
    assert response.status_code == 422, response.text
    assert needle in response.json()["detail"]
    sess = await session_for("ro")
    sha = hashlib.sha256(body).hexdigest()
    assert await sess.scalar(select(func.count()).where(Upload.sha256 == sha)) == 0
    assert not list(inbox.rglob("*.html"))


@pytest.mark.parametrize(
    "name,content,media_type,status",
    [
        ("x.png", b"\x89PNG\r\n", "image/png", 415),
        ("x.txt", b"plain words", "text/plain", 415),
        ("fake.pdf", b"<html>not a pdf</html>", "application/pdf", 415),
        ("empty.html", b"", "text/html", 422),
    ],
)
async def test_a_file_that_cannot_be_read_is_refused(
    client, inbox, topic, marker, session_for, name, content, media_type, status
) -> None:
    response = await hand_in(client, content, name=f"{marker}{name}", media_type=media_type)
    assert response.status_code == status, response.text
    sess = await session_for("ro")
    assert await sess.scalar(select(func.count()).where(Upload.filename.like(f"{marker}%"))) == 0
    assert not [p for p in inbox.rglob("*") if p.is_file()]


async def test_a_file_over_the_cap_is_refused_however_it_is_sent(
    client, inbox, topic, marker, session_for, monkeypatch
) -> None:
    monkeypatch.setenv("MERIDIAN_CONNECTOR_MAX_MB", "1")
    big = b"%PDF-1.7\n" + b"0" * (1024 * 1024 + 10)
    response = await hand_in(client, big, name=f"{marker}.pdf", media_type="application/pdf")
    # Under the declared-length allowance (cap + form overhead), so this is the streamed check.
    assert response.status_code == 413, response.text
    assert not [p for p in inbox.rglob("*") if p.is_file()]
    sess = await session_for("ro")
    assert await sess.scalar(select(func.count()).where(Upload.filename.like(f"{marker}%"))) == 0


# -- what the corpus is missing ------------------------------------------------


async def wanted_mine(client, marker: str, start: int, limit: int = 100) -> list[str]:
    found, cursor = [], str(start)
    while cursor is not None:
        page = await client.get("/connect/v1/wanted", params={"limit": limit, "after": cursor})
        assert page.status_code == 200, page.text
        body = page.json()
        found += [item["doi"] for item in body["items"] if marker in item["doi"]]
        cursor = body["next"]
    return found


async def test_wanted_lists_exactly_the_no_copy_dois_the_corpus_lacks(
    client, topic, marker, session_for
) -> None:
    sess = await session_for("rw")
    citing, _ = await upsert_source(sess, f"https://{marker}.test/citing", title="Citing page")
    rows = {
        "a": dict(status="done", error=NO_COPY),
        "b": dict(status="done", error=NO_COPY),  # the corpus has it, with text
        "c": dict(status="done", error=None),  # resolved: a copy was queued
        "d": dict(status="failed", error="resolution_unavailable: down"),
        "e": dict(status="done", error=NO_COPY),
        "f": dict(status="pending", error=None),
    }
    tasks = {}
    for suffix, fields in rows.items():
        task = QueueTask(
            url_or_query=doi(marker, suffix),
            task_type="doi",
            seed_source="citation",
            topic=topic,
            parent_source_id=citing.source_id,
            **fields,
        )
        sess.add(task)
        await sess.flush()
        tasks[suffix] = task
    await upsert_source(sess, f"https://{marker}.test/b", doi=doi(marker, "b"), text_available=True)
    await upsert_source(
        sess, f"https://{marker}.test/e", doi=doi(marker, "e"), title="Known title"
    )  # holds no text, so still wanted, and lends its title
    await sess.commit()
    start = min(t.task_id for t in tasks.values()) - 1

    assert await wanted_mine(client, marker, start) == [doi(marker, "a"), doi(marker, "e")]

    page = (await client.get("/connect/v1/wanted", params={"limit": 1, "after": start})).json()
    assert [i["doi"] for i in page["items"]] == [doi(marker, "a")]
    item = page["items"][0]
    assert item["reason"] == NO_COPY and item["topic"] == topic
    assert item["cited_from_url"] == f"https://{marker}.test/citing"
    assert page["next"] == str(tasks["a"].task_id)
    second = (
        await client.get("/connect/v1/wanted", params={"limit": 1, "after": page["next"]})
    ).json()
    assert second["items"][0]["doi"] == doi(marker, "e")
    assert second["items"][0]["title"] == "Known title"

    # Handing in a copy takes it off the list at once, before the worker has read it.
    response = await hand_in(
        client, article(marker), name=f"{marker}.html", doi=doi(marker, "a"), topic=topic
    )
    assert response.status_code == 202
    assert await wanted_mine(client, marker, start) == [doi(marker, "e")]


@pytest.mark.parametrize(
    "params", [{"kind": "url"}, {"limit": 0}, {"limit": 5000}, {"after": "-1"}, {"after": "x"}]
)
async def test_wanted_refuses_a_malformed_query(client, params) -> None:
    response = await client.get("/connect/v1/wanted", params=params)
    assert response.status_code == 422


async def test_an_unknown_upload_is_404(client) -> None:
    response = await client.get("/connect/v1/documents/987654321098")
    assert response.status_code == 404


# -- the worker turns it into a source -------------------------------------------


@asynccontextmanager
async def _session(sess):
    sess.commit = sess.flush
    yield sess


def build_worker(sess, topic: str, inbox) -> Worker:
    """A worker that claims only this run's topic and fetches nothing."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"an upload must not fetch anything: {request.url}")

    fetcher = Fetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(refuse)))
    crawler = Crawler(lambda: _session(sess), fetcher=fetcher, limiter=DomainLimiter())
    settings = WorkerSettings(
        worker_id=f"test-{uuid.uuid4().hex[:8]}",
        concurrency=1,
        idle_sleep_s=0.01,
        housekeeping_interval_s=0,
        max_embed_backlog=0,
        topics=(topic,),
        max_tasks=1,
    )
    return Worker(crawler, settings=settings, session_factory=lambda: _session(sess), inbox=inbox)


async def run_upload(client, session_for, topic, inbox, content, **form):
    response = await hand_in(client, content, topic=topic, **form)
    assert response.status_code == 202, response.text
    sess = await session_for("rw")
    stats = await build_worker(sess, topic, inbox).run()
    assert stats.claimed == 1
    upload = await sess.get(Upload, response.json()["upload_id"])
    await sess.refresh(upload)
    return sess, upload


async def test_the_worker_makes_an_uploaded_page_a_source_with_chunks(
    client, inbox, topic, marker, session_for
) -> None:
    """The whole feature, read back from committed rows (connectors guide §3)."""
    sess, upload = await run_upload(
        client,
        session_for,
        topic,
        inbox,
        article(marker, "Extracted title"),
        name=f"{marker}.html",
        doi=doi(marker, "w"),
        title="The sender's title",
        description="why it was sent",
    )
    try:
        assert upload.status == "done", upload.error
        assert upload.error is None and upload.source_id is not None
        task = await sess.get(QueueTask, upload.task_id)
        assert (task.status, task.seed_source) == ("done", "upload")

        source = await sess.get(Source, upload.source_id)
        assert source.url == f"https://doi.org/{doi(marker, 'w')}"
        assert source.doi == doi(marker, "w")
        assert source.title == "The sender's title"
        assert source.text_available is True
        assert source.source_tier == "peer_reviewed", "a DOI and no tier given"
        assert source.retention_tier == "primary" and source.raw_file_path
        assert source.trust_state == "cleared"
        assert source.crawled_for == [topic]
        assert source.checksum == f"sha256:{upload.sha256}"
        provenance = source.extra["upload"]
        assert provenance["upload_id"] == upload.upload_id
        assert provenance["sha256"] == upload.sha256
        assert provenance["description"] == "why it was sent"
        assert source.extra["media_type"] == "text/html"
        assert await chunk_count(sess, source.source_id) > 0
    finally:
        # The worker's settle holds the upload row's lock until this transaction ends.
        await sess.rollback()


async def test_an_upload_without_a_doi_is_keyed_by_its_digest(
    client, inbox, topic, marker, session_for
) -> None:
    sess, upload = await run_upload(
        client,
        session_for,
        topic,
        inbox,
        article(marker),
        name=f"{marker}.html",
        source_url="https://example.org/where-it-came-from",
    )
    try:
        assert upload.status == "done", upload.error
        source = await sess.get(Source, upload.source_id)
        assert source.url == f"upload://{upload.sha256}"
        assert source.source_tier == "informal"
        assert source.retention_tier == "primary", "kept even at a tier that keeps no bytes"
        assert source.extra["upload"]["source_url"] == "https://example.org/where-it-came-from"
        # Nothing recorded against a made-up domain.
        assert await sess.get(FetchPolicy, "upload") is None
    finally:
        await sess.rollback()


async def test_a_corrupt_pdf_fails_with_a_reason_and_makes_no_source(
    client, inbox, topic, marker, session_for
) -> None:
    sess, upload = await run_upload(
        client,
        session_for,
        topic,
        inbox,
        b"%PDF-1.7\nthis is not really a PDF at all",
        name=f"{marker}.pdf",
        media_type="application/pdf",
        doi=doi(marker, "bad"),
    )
    try:
        assert upload.status == "failed"
        assert upload.error.startswith("no_text:"), upload.error
        assert upload.source_id is None
        task = await sess.get(QueueTask, upload.task_id)
        assert task.status == "failed" and task.error == upload.error
        url = f"https://doi.org/{doi(marker, 'bad')}"
        assert await sess.scalar(select(func.count()).where(Source.url == url)) == 0
    finally:
        await sess.rollback()


async def test_a_missing_inbox_file_fails_the_upload_rather_than_retrying(
    client, inbox, topic, marker, session_for
) -> None:
    response = await hand_in(client, article(marker), name=f"{marker}.html", topic=topic)
    for path in inbox.rglob("*.html"):
        path.unlink()
    sess = await session_for("rw")
    await build_worker(sess, topic, inbox).run()
    try:
        upload = await sess.get(Upload, response.json()["upload_id"])
        await sess.refresh(upload)
        assert upload.status == "failed" and upload.error.startswith("inbox_unreadable")
    finally:
        await sess.rollback()


async def test_a_worker_without_the_inbox_leaves_uploads_for_one_that_has_it(
    client, inbox, topic, marker, session_for
) -> None:
    response = await hand_in(client, article(marker), name=f"{marker}.html", topic=topic)
    sess = await session_for("rw")
    worker = build_worker(sess, topic, None)
    # Nothing it can claim, so it idles until told to stop.
    asyncio.get_running_loop().call_later(0.5, worker.stop)
    stats = await worker.run()
    try:
        assert stats.claimed == 0
        task = await sess.get(QueueTask, response.json()["task_id"])
        assert task.status == "pending" and task.attempts == 0
    finally:
        await sess.rollback()


def build_pdf(tmp_path, line: str) -> bytes:
    import subprocess

    source = tmp_path / "in.ps"
    body = "".join(
        f"72 {720 - 14 * n} moveto ({line}) show\n" for n in range(20)
    )  # one page of repeated prose
    source.write_text(
        "%!PS-Adobe-3.0\n%%Pages: 1\n%%Page: 1 1\n/Helvetica findfont 10 scalefont setfont\n"
        f"{body}showpage\n%%EOF\n"
    )
    out = tmp_path / "out.pdf"
    subprocess.run(["ps2pdf", str(source), str(out)], check=True, capture_output=True)
    return out.read_bytes()


@pytest.mark.skipif(
    not pdf_available() or __import__("shutil").which("ps2pdf") is None,
    reason="needs poppler and ghostscript",
)
async def test_the_worker_reads_an_uploaded_pdf_by_page(
    client, inbox, topic, marker, session_for, tmp_path
) -> None:
    content = build_pdf(tmp_path, f"{marker} The committee agreed a revised schedule today.")
    sess, upload = await run_upload(
        client,
        session_for,
        topic,
        inbox,
        content,
        name=f"{marker}.pdf",
        media_type="application/pdf",
        source_tier="government",
    )
    try:
        assert upload.status == "done", upload.error
        source = await sess.get(Source, upload.source_id)
        assert source.source_tier == "government"
        assert source.raw_file_path.endswith(".pdf")
        assert await chunk_count(sess, source.source_id) > 0
    finally:
        await sess.rollback()


# -- the database's own guards -----------------------------------------------------


async def test_the_read_only_role_cannot_write_uploads(session_for) -> None:
    sess = await session_for("ro")
    with pytest.raises(Exception) as exc:
        await sess.execute(
            text(
                "INSERT INTO uploads (sha256, filename, media_type, size_bytes, inbox_path) "
                "VALUES (repeat('a', 64), 'x', 'text/html', 1, 'aa/x.html')"
            )
        )
    assert "permission denied" in str(exc.value).lower()


async def _accepts(sess, statement: str, params: dict) -> bool:
    nested = await sess.begin_nested()
    try:
        await sess.execute(text(statement), params)
    except Exception as exc:
        await nested.rollback()
        assert "check constraint" in str(exc).lower(), exc
        return False
    await nested.rollback()
    return True


QUEUE_INSERT = (
    "INSERT INTO queue (url_or_query, task_type, status, priority, seed_source, attempts) "
    "VALUES ('check:test', :task_type, 'pending', 0, :seed_source, 0)"
)


async def test_the_queue_checks_take_every_model_value_and_refuse_others(session_for) -> None:
    """Drift, by raw SQL: the model's enums against the CHECKs the migration wrote by hand."""
    sess = await session_for("rw")
    await sess.begin()
    assert "upload" in TASK_TYPE.enums and "upload" in SEED_SOURCE.enums
    for value in TASK_TYPE.enums:
        assert await _accepts(sess, QUEUE_INSERT, {"task_type": value, "seed_source": "user"}), (
            value
        )
    for value in SEED_SOURCE.enums:
        assert await _accepts(sess, QUEUE_INSERT, {"task_type": "url", "seed_source": value}), value
    for bad in ("uploads", "Upload", "file"):
        assert not await _accepts(sess, QUEUE_INSERT, {"task_type": bad, "seed_source": "user"})
        assert not await _accepts(sess, QUEUE_INSERT, {"task_type": "url", "seed_source": bad})
    policy = "INSERT INTO fetch_policy (domain, first_seen_via) VALUES (:d, :v)"
    for value in SEED_SOURCE.enums:
        assert await _accepts(sess, policy, {"d": f"{value}.check.test", "v": value}), value
    assert not await _accepts(sess, policy, {"d": "bad.check.test", "v": "uploaded"})
    await sess.rollback()


async def test_the_uploads_checks_refuse_a_bad_status_and_digest(session_for) -> None:
    sess = await session_for("rw")
    await sess.begin()
    insert = (
        "INSERT INTO uploads (sha256, filename, media_type, size_bytes, inbox_path, status) "
        "VALUES (:sha, 'x', 'text/html', 1, 'aa/x.html', :status)"
    )
    for n, value in enumerate(UPLOAD_STATUS.enums):
        sha = hashlib.sha256(f"{uuid.uuid4()}{n}".encode()).hexdigest()
        assert await _accepts(sess, insert, {"sha": sha, "status": value}), value
    good = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    assert not await _accepts(sess, insert, {"sha": good, "status": "late"})
    for bad in (good.upper(), good[:-1], "../" + good[3:]):
        assert not await _accepts(sess, insert, {"sha": bad, "status": "queued"})
    await sess.rollback()
