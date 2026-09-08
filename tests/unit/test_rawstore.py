"""The raw store: path scheme, checksum, retention (task P1-11, spec §5.4).

Three things are being defended here. The path is built from a URL a hostile
site controls, so it must not escape the store or carry anything a filesystem
will interpret. The retention split has to actually split — a store that keeps
everything is not implementing §5.4, it is ignoring it. And the write has to be
atomic, because a truncated file with a checksum beside it swearing otherwise is
a corruption nobody discovers until the citation is the thing they needed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from worker.rawstore import (
    DEFAULT_EXTENSION,
    RETENTION_BY_SOURCE_TIER,
    RETENTION_RANK,
    StoredRaw,
    UnsafeRawPath,
    checksum_for,
    extension_for,
    path_for,
    raw_root,
    resolve,
    retention_for,
    safe_domain,
    store,
)

GOV = "https://www.example-org.test/report.pdf"
BLOG = "https://someone.blogspot.com/post"


# --------------------------------------------------------------------------
# The path scheme
# --------------------------------------------------------------------------


def test_the_path_is_derived_from_the_url_not_the_content() -> None:
    """A re-fetch must land on the file the last fetch wrote.

    Naming by content hash would grow one copy per visit and leave nothing able
    to find the previous one — which is the opposite of what a store kept
    against link rot is for.
    """
    first = path_for(GOV, "application/pdf")
    second = path_for(GOV, "application/pdf")
    assert first == second
    assert path_for("https://www.example-org.test/other.pdf", "application/pdf") != first


def test_the_path_carries_the_domain_the_shard_and_the_digest() -> None:
    digest = hashlib.sha256(GOV.encode()).hexdigest()
    expected = Path("example-org.test") / digest[:2] / f"{digest}.pdf"
    assert path_for(GOV, "application/pdf") == expected


def test_the_domain_leads_so_one_site_is_one_directory() -> None:
    """What a takedown and a retention sweep both actually need."""
    assert path_for(GOV).parts[0] == "example-org.test"
    assert path_for("https://data.example-org.test/x").parts[0] == "data.example-org.test"


def test_a_busy_domain_is_sharded_rather_than_one_huge_directory() -> None:
    urls = [f"https://example-org.test/page-{i}" for i in range(200)]
    shards = {path_for(u).parts[1] for u in urls}
    assert len(shards) > 50, "the hex shard is not spreading files out"


@pytest.mark.parametrize(
    "url",
    [
        "https://../../../etc/passwd",
        "https://./x",
        "https:///nohost",
        "https://exam ple.com/x",
        "https://exa\\mple.com/x",
        "https://ex%2fample.com/x",
        "https://ünïcode.example/x",  # never punycoded
        "https://-leading-hyphen.com/x",
        "https://" + "a" * 300 + ".com/x",
    ],
)
def test_a_host_that_cannot_be_a_directory_name_is_refused(url: str) -> None:
    """Refused, not sanitised.

    A path silently repaired into something plausible is a path nobody can
    reason about later, and `..` quietly becoming `__` is how a containment bug
    survives review.
    """
    with pytest.raises(UnsafeRawPath):
        path_for(url)


def test_every_generated_path_stays_inside_the_store(tmp_path: Path) -> None:
    """The property the refusals above exist to protect."""
    for url in (GOV, BLOG, "https://a.example/../../x", "https://b.example/%2e%2e/x"):
        relative = path_for(url)
        assert resolve(str(relative), root=tmp_path).is_relative_to(tmp_path.resolve())


@pytest.mark.parametrize("escape", ["../outside", "../../etc/passwd", "/etc/passwd"])
def test_resolving_a_path_that_escapes_the_store_is_refused(tmp_path: Path, escape: str) -> None:
    """`raw_file_path` is also written by whatever imports a corpus snapshot."""
    with pytest.raises(UnsafeRawPath):
        resolve(escape, root=tmp_path)


# --------------------------------------------------------------------------
# Extensions
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "media,expected",
    [
        ("text/html", ".html"),
        ("text/html; charset=utf-8", ".html"),
        ("TEXT/HTML", ".html"),
        ("application/pdf", ".pdf"),
        ("application/json", ".json"),
        (None, DEFAULT_EXTENSION),
        ("", DEFAULT_EXTENSION),
        ("application/x-whatever-they-invented", DEFAULT_EXTENSION),
    ],
)
def test_the_extension_comes_from_an_allowlist(media: str | None, expected: str) -> None:
    """Not from `content-disposition` or the URL's own suffix.

    Both are whatever the far end felt like sending, and this string becomes
    part of a filesystem path.
    """
    assert extension_for(media) == expected


def test_no_allowlisted_extension_can_carry_a_path_separator() -> None:
    """A completeness probe over the table rather than over three examples."""
    from worker.rawstore import EXTENSIONS

    for media, ext in EXTENSIONS.items():
        assert ext.startswith("."), media
        assert "/" not in ext and "\\" not in ext and ".." not in ext[1:], media


# --------------------------------------------------------------------------
# Checksums
# --------------------------------------------------------------------------


def test_the_checksum_says_what_produced_it() -> None:
    """A bare hex string is a checksum nobody can verify in five years."""
    digest = checksum_for(b"hello")
    algorithm, _, hexed = digest.partition(":")
    assert algorithm == "sha256"
    assert hexed == hashlib.sha256(b"hello").hexdigest()


def test_different_bytes_give_different_checksums() -> None:
    assert checksum_for(b"a") != checksum_for(b"b")
    assert checksum_for(b"") == checksum_for(b"")


# --------------------------------------------------------------------------
# Retention (§5.4)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source_tier,expected",
    [
        ("peer_reviewed", "primary"),
        ("government", "primary"),
        ("institutional", "primary"),
        ("press", "background"),
        ("informal", "background"),
    ],
)
def test_source_tier_decides_retention(source_tier: str, expected: str) -> None:
    assert retention_for(source_tier) == expected


def test_every_source_tier_has_a_retention_tier() -> None:
    """Completeness probe against the model's CHECK constraint, not a list.

    A new source tier must be given a retention answer rather than silently
    defaulting to the one that throws the bytes away.
    """
    from meridian_core.models.source import SOURCE_TIER

    missing = set(SOURCE_TIER.enums) - set(RETENTION_BY_SOURCE_TIER)
    assert not missing, f"source tiers with no retention rule: {sorted(missing)}"


def test_retention_tiers_are_all_ranked() -> None:
    from meridian_core.models.source import RETENTION_TIER

    assert set(RETENTION_TIER.enums) == set(RETENTION_RANK)


def test_junk_is_never_assigned_at_fetch_time() -> None:
    """It is the novelty gate's verdict on a near-duplicate (§5.4).

    Nothing at fetch time has seen the rest of the corpus, so nothing at fetch
    time is in a position to call something a near-duplicate.
    """
    assert "junk" not in RETENTION_BY_SOURCE_TIER.values()


def test_retention_never_silently_demotes_a_tier_someone_raised() -> None:
    """Same shape as §11.12's quality-tier rule, for the field that decides
    what gets kept.

    An operator who promoted a press domain to `primary` did it on purpose. A
    mechanical mapping that overruled them next Tuesday would quietly start
    throwing away the files they promoted it to keep.
    """
    assert retention_for("press", "primary") == "primary"
    assert retention_for("informal", "primary") == "primary"


def test_retention_still_promotes_upward() -> None:
    assert retention_for("government", "background") == "primary"
    assert retention_for("government", "junk") == "primary"


def test_an_unknown_current_tier_does_not_win_by_accident() -> None:
    """A tier that predates a rename must not outrank a real one."""
    assert retention_for("government", "nonsense_from_an_old_row") == "primary"


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def test_a_primary_source_is_written_to_disk(tmp_path: Path) -> None:
    stored = store(
        GOV, b"%PDF-1.7 ...", source_tier="government", media_type="application/pdf", root=tmp_path
    )

    assert stored.kept
    assert stored.retention_tier == "primary"
    written = tmp_path / stored.path
    assert written.read_bytes() == b"%PDF-1.7 ..."
    assert written.suffix == ".pdf"


def test_a_background_source_keeps_its_checksum_and_not_its_bytes(tmp_path: Path) -> None:
    """§5.4: extracted text and metadata, not the file.

    The checksum still comes back, because change detection on a re-crawl is
    what it is for and that has to work for a source whose bytes were never
    written.
    """
    stored = store(
        BLOG, b"<p>hi</p>", source_tier="informal", media_type="text/html", root=tmp_path
    )

    assert not stored.kept
    assert stored.path is None
    assert stored.retention_tier == "background"
    assert stored.checksum == checksum_for(b"<p>hi</p>")
    assert list(tmp_path.rglob("*")) == [], "nothing should have been written"


def test_the_stored_path_is_relative_to_the_store(tmp_path: Path) -> None:
    """An absolute path bakes in the container's mount point.

    `/data/raw` is where the store is mounted, not where it lives. A store moved
    to a bigger disk would invalidate every row that recorded an absolute path.
    """
    stored = store(GOV, b"x", source_tier="government", root=tmp_path)

    assert stored.path is not None
    assert not Path(stored.path).is_absolute()
    assert str(tmp_path) not in stored.path


def test_a_refetch_overwrites_rather_than_accumulating(tmp_path: Path) -> None:
    store(
        GOV, b"version one", source_tier="government", media_type="application/pdf", root=tmp_path
    )
    stored = store(
        GOV, b"version two", source_tier="government", media_type="application/pdf", root=tmp_path
    )

    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert len(files) == 1
    assert files[0].read_bytes() == b"version two"
    assert stored.checksum == checksum_for(b"version two")


def test_a_failed_write_leaves_no_temporary_file_behind(tmp_path: Path, monkeypatch) -> None:
    """One per interrupted fetch, in a directory nothing ever sweeps."""

    def explode(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("worker.rawstore.os.replace", explode)

    with pytest.raises(OSError):
        store(GOV, b"x" * 1024, source_tier="government", root=tmp_path)

    leftovers = [p.name for p in tmp_path.rglob("*") if p.is_file()]
    assert leftovers == [], f"temporary files left behind: {leftovers}"


def test_a_partial_write_never_appears_at_the_final_path(tmp_path: Path, monkeypatch) -> None:
    """The reason the write goes via a temporary name at all.

    Simulated by failing after the content is written and before the rename:
    the destination must not exist, rather than existing and being wrong.
    """
    seen: list[Path] = []

    def fail_the_rename(src, dst):
        seen.append(Path(dst))
        raise OSError(5, "I/O error")

    monkeypatch.setattr("worker.rawstore.os.replace", fail_the_rename)
    with pytest.raises(OSError):
        store(GOV, b"truncated", source_tier="government", root=tmp_path)

    assert seen, "the write never reached the rename"
    assert not seen[0].exists(), "a half-written file appeared at the path the DB records"
    # And the temporary it was staged under is gone too, so a retry starts clean.
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == []


def test_the_temporary_file_shares_the_destination_directory(tmp_path: Path, monkeypatch) -> None:
    """`os.replace` is only atomic within a filesystem, and `/tmp` often is not."""
    captured: dict[str, object] = {}
    real_mkstemp = __import__("tempfile").mkstemp

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr("worker.rawstore.tempfile.mkstemp", spy)
    stored = store(GOV, b"x", source_tier="government", media_type="application/pdf", root=tmp_path)

    assert stored.path is not None
    assert Path(captured["dir"]) == (tmp_path / stored.path).parent


def test_directories_are_created_on_demand(tmp_path: Path) -> None:
    target = tmp_path / "does" / "not" / "exist"
    stored = store(GOV, b"x", source_tier="government", root=target)

    assert stored.path is not None
    assert (target / stored.path).exists()


def test_an_empty_body_is_still_a_file(tmp_path: Path) -> None:
    """A zero-byte response is a fact about the page, not a reason to skip it."""
    stored = store(GOV, b"", source_tier="government", root=tmp_path)

    assert stored.kept
    assert (tmp_path / stored.path).read_bytes() == b""
    assert stored.bytes_written == 0


# --------------------------------------------------------------------------
# The root
# --------------------------------------------------------------------------


def test_the_root_prefers_the_argument_then_the_environment_then_the_default(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("MERIDIAN_RAW_ROOT", "/from/env")
    assert raw_root(tmp_path) == tmp_path
    assert raw_root() == Path("/from/env")
    monkeypatch.delenv("MERIDIAN_RAW_ROOT")
    assert raw_root() == Path("/data/raw")


def test_stored_raw_reports_whether_anything_was_kept() -> None:
    assert not StoredRaw(checksum="sha256:x", retention_tier="background").kept
    assert StoredRaw(checksum="sha256:x", retention_tier="primary", path="a/b").kept


def test_safe_domain_strips_the_parts_a_path_should_not_carry() -> None:
    assert safe_domain("https://WWW.Example.COM:8443/a/b?c=d") == "example.com"
