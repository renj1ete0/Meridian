"""Fetch policy resolution (spec §6.4).

Resolution order is **per-domain row → global `'*'` row → file defaults**, with
later layers only filling gaps. Per-domain overrides exist because one setting
for a large API and a small municipal server is wrong in one direction or the
other: two concurrent requests per second is nothing to `data.gov.sg` and rude to
a council website.

The file layer is a floor, not a source of truth. §13.1 makes the database
authoritative once seeded — the YAML is read here only so a worker started
against a database that has not been seeded yet fails predictably instead of
with a KeyError halfway through a fetch.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import FetchPolicy as FetchPolicyRow
from .tiering import jittered_delay_ms, registrable_domain, resolve_tier

_FILE_DEFAULTS_PATH = Path(__file__).resolve().parents[3] / "config" / "fetch_policy.yaml"
_file_defaults_cache: dict[str, Any] | None = None

log = get_logger(__name__)

GLOBAL_DOMAIN = "*"


class ResolvedPolicy(BaseModel):
    """The effective policy for one domain, after all layers are merged."""

    model_config = ConfigDict(extra="allow")  # unknown keys survive for forward compat

    domain: str
    status: str = "active"

    respect_robots: bool = True
    user_agent: str = "MeridianBot/0.1"
    send_contact_header: bool = True

    concurrency_per_domain: int = Field(default=2, ge=1)
    delay_per_domain_ms: int = Field(default=1000, ge=0)
    delay_jitter_ms: int = Field(default=0, ge=0)
    respect_crawl_delay: bool = True

    conditional_requests: bool = True
    timeout_s: int = Field(default=30, gt=0)
    max_retries: int = Field(default=2, ge=0)
    backoff_base_s: int = Field(default=5, gt=0)

    blocked_after_failures: int = Field(default=5, ge=1)
    prefetch_filter: bool = True
    render_js: str = "auto"
    max_page_bytes: int = Field(default=20_000_000, gt=0)

    allowed_schemes: list[str] = Field(default_factory=lambda: ["http", "https"])
    require_https_final: bool = True
    block_private_addresses: bool = True
    block_cloud_metadata: bool = True
    max_redirects: int = Field(default=5, ge=0)
    revalidate_each_redirect: bool = True
    block_mixed_dns: bool = True
    allowed_content_types: list[str] = Field(default_factory=list)
    max_decompression_ratio: int = Field(default=100, gt=0)

    @property
    def is_fetchable(self) -> bool:
        """False for a domain marked blocked or paused in Admin."""
        return self.status == "active"

    def next_delay_ms(self, rng: Any | None = None) -> int:
        """The wait before the next request to this domain, jitter included."""
        return jittered_delay_ms(self.delay_per_domain_ms, self.delay_jitter_ms, rng)


def file_defaults() -> dict[str, Any]:
    """The shipped defaults. Cached — this is a floor, and it does not change."""
    global _file_defaults_cache
    if _file_defaults_cache is None:
        if _FILE_DEFAULTS_PATH.exists():
            _file_defaults_cache = yaml.safe_load(_FILE_DEFAULTS_PATH.read_text()) or {}
        else:
            _file_defaults_cache = {}
    return _file_defaults_cache


def merge_layers(*layers: dict[str, Any] | None) -> dict[str, Any]:
    """Merge settings dicts, earlier layers winning.

    A shallow merge, deliberately. Every policy value is a scalar or a whole
    list, and deep-merging a list would produce something no one wrote — an
    override of ``allowed_schemes`` must *replace* the default, not extend it.
    """
    merged: dict[str, Any] = {}
    for layer in reversed([layer for layer in layers if layer]):
        merged.update({k: v for k, v in layer.items() if v is not None})
    return merged


async def resolve_policy(sess: AsyncSession, domain: str) -> ResolvedPolicy:
    """Effective policy for ``domain``: per-domain → global → file defaults."""
    host = registrable_domain(domain)

    rows = (
        (
            await sess.execute(
                select(FetchPolicyRow).where(FetchPolicyRow.domain.in_([host, GLOBAL_DOMAIN]))
            )
        )
        .scalars()
        .all()
    )
    by_domain = {row.domain: row for row in rows}
    specific = by_domain.get(host)
    glob = by_domain.get(GLOBAL_DOMAIN)

    settings = merge_layers(
        specific.settings if specific else None,
        glob.settings if glob else None,
        file_defaults(),
    )
    # The tier map rides in the global row but is not a fetch setting.
    settings.pop("source_tiers", None)
    # Likewise the frontier block: it decides what enters the queue, not how a
    # request is made, and a ResolvedPolicy carrying it would invite callers to
    # treat "should this be crawled" as a per-request setting.
    settings.pop("frontier", None)

    # A per-domain row carries status; the global row's status is not inherited,
    # because blocking '*' would silently stop the entire crawl.
    status = specific.status if specific else "active"
    return ResolvedPolicy(domain=host, status=status, **settings)


async def source_tier_map(sess: AsyncSession) -> dict[str, Any]:
    """The domain → tier mapping, from the global fetch policy row (§5.2, §13.1).

    It rides in `fetch_policy['*'].settings` rather than in a table of its own —
    one global blob of domain policy, seeded from `config/source_tiers.yaml` at
    first boot and authoritative in the database thereafter. `resolve_policy`
    strips it out because it is not a fetch setting; this is where it is read
    back.
    """
    glob = await sess.scalar(select(FetchPolicyRow).where(FetchPolicyRow.domain == GLOBAL_DOMAIN))
    if glob is None or not glob.settings:
        return {}
    return glob.settings.get("source_tiers") or {}


async def frontier_settings(sess: AsyncSession) -> dict[str, Any]:
    """The `frontier` block from the global fetch policy row (`P1-06`).

    Rides in the same global settings blob as `source_tiers`, and is stripped
    out of `ResolvedPolicy` for the same reason: it governs what goes *into* the
    queue, not how a request is made.
    """
    glob = await sess.scalar(select(FetchPolicyRow).where(FetchPolicyRow.domain == GLOBAL_DOMAIN))
    if glob is None or not glob.settings:
        return {}
    return glob.settings.get("frontier") or {}


async def resolve_source_tier(sess: AsyncSession, domain: str) -> str:
    """The source tier for ``domain``, from the seeded mapping.

    Mechanical and deterministic (§5.2) — never a model judgement — so this is a
    lookup and nothing more. A separate query from :func:`resolve_policy` on
    purpose: the tier is a property of a *source*, not of a request, and folding
    a global mapping into a per-domain policy object to save one indexed read on
    a tiny table would be paying in clarity for something free.
    """
    return resolve_tier(domain, await source_tier_map(sess))


async def record_failure(
    sess: AsyncSession, domain: str, *, blocked_after: int | None = None
) -> bool:
    """Count a consecutive failure for a domain; block it past the threshold.

    Returns True if this failure blocked the domain. Without this, one dead site
    consumes crawl budget for weeks unnoticed (§6.4).
    """
    host = registrable_domain(domain)
    row = await sess.scalar(select(FetchPolicyRow).where(FetchPolicyRow.domain == host))
    if row is None:
        row = FetchPolicyRow(domain=host, settings={}, status="active", consecutive_failures=0)
        sess.add(row)

    row.consecutive_failures += 1
    row.updated_at = dt.datetime.now(dt.UTC)
    row.updated_by = "worker"

    threshold = blocked_after
    if threshold is None:
        policy = await resolve_policy(sess, host)
        threshold = policy.blocked_after_failures

    if row.consecutive_failures >= threshold and row.status == "active":
        row.status = "blocked"
        row.note = f"auto-blocked after {row.consecutive_failures} consecutive failures"
        await sess.flush()
        return True
    await sess.flush()
    return False


async def record_success(sess: AsyncSession, domain: str) -> None:
    """Reset the failure counter. Only *consecutive* failures block a domain."""
    host = registrable_domain(domain)
    row = await sess.scalar(select(FetchPolicyRow).where(FetchPolicyRow.domain == host))
    if row is not None and row.consecutive_failures:
        row.consecutive_failures = 0
        await sess.flush()


# --------------------------------------------------------------------------
# What a fetch outcome says about the domain (task P1-05)
# --------------------------------------------------------------------------
#
# Consecutive-failure blocking asks one question — *is this domain still worth
# spending crawl budget on* — and most fetch outcomes are not evidence either
# way. Three answers, because two would force every outcome into a judgement it
# does not support.

#: The domain answered. Whatever went wrong was about this URL or its content,
#: not about the host being gone — a 404, an oversized file, a media type the
#: allowlist does not take. Evidence the domain is alive *resets* the counter,
#: because only consecutive failures block. A domain serving nothing but 404s is
#: a real problem and a different one; ``fetch_attempts`` is where it shows up.
DOMAIN_ALIVE = frozenset(
    {
        "success",
        "not_modified",
        "too_large",
        "content_type_rejected",
        "parse_error",
    }
)

#: Nothing usable came back and the domain is why. Timeouts and connection
#: errors are the obvious members; a redirect loop is a server misconfiguration,
#: and a decompression bomb or an address ``netguard`` refuses is a host it is
#: affirmatively wrong to keep requesting from.
DOMAIN_UNREACHABLE = frozenset(
    {
        "timeout",
        "connection_error",
        "too_many_redirects",
        "decompression_bomb",
        "unsafe_target",
    }
)

#: No request went out, so there is nothing to conclude. Counting a robots
#: denial as a failure would auto-block every well-behaved site with a
#: restrictive robots.txt, and counting a refusal to fetch an already-blocked
#: domain would make the block deepen itself.
DOMAIN_NO_SIGNAL = frozenset({"robots_denied", "blocked"})

# `http_error` is deliberately in none of the three: the status code decides it.
# 5xx is the server failing, and 429 is the server saying stop, which is a
# reason to back off the domain rather than to keep asking. Every other 4xx is
# the domain answering correctly about a URL that is not there.
BACKOFF_STATUS = 429


def domain_signal(outcome: str, status_code: int | None = None) -> str:
    """``"alive"``, ``"unreachable"`` or ``"none"`` for one fetch outcome."""
    if outcome in DOMAIN_ALIVE:
        return "alive"
    if outcome in DOMAIN_UNREACHABLE:
        return "unreachable"
    if outcome in DOMAIN_NO_SIGNAL:
        return "none"
    if outcome == "http_error":
        if status_code is None or status_code >= 500 or status_code == BACKOFF_STATUS:
            return "unreachable"
        return "alive"
    # An outcome nobody classified. Not fatal on purpose: the attempt row still
    # records it, so it is visible on the health line rather than silent, and a
    # worker that runs for weeks should not die over a string. The drift test
    # over FETCH_OUTCOME is what keeps this branch unreachable in practice.
    log.warning("unclassified fetch outcome; no policy consequence", extra={"outcome": outcome})
    return "none"


async def apply_fetch_outcome(
    sess: AsyncSession,
    domain: str,
    outcome: str,
    *,
    status_code: int | None = None,
    blocked_after: int | None = None,
) -> bool:
    """Apply one fetch outcome to the domain's policy row.

    Returns True if this outcome blocked the domain, which the caller needs in
    order to drop the domain's rate-limiter state and say so on the log.
    """
    signal = domain_signal(outcome, status_code)
    if signal == "alive":
        await record_success(sess, domain)
        return False
    if signal == "unreachable":
        return await record_failure(sess, domain, blocked_after=blocked_after)
    return False
