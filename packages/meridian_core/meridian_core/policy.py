"""Fetch policy resolution (spec §6.4).

Per-domain row, then the global `'*'` row, then file defaults, each filling gaps. The
file layer only keeps an unseeded database predictable (§13.1). See
docs/features/crawling.md#fetch-policy.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger
from .models import FetchAttempt
from .models import FetchPolicy as FetchPolicyRow
from .tiering import jittered_delay_ms, registrable_domain, resolve_tier

_FILE_DEFAULTS_PATH = Path(__file__).resolve().parents[3] / "config" / "fetch_policy.yaml"
_file_defaults_cache: dict[str, Any] | None = None

log = get_logger(__name__)

GLOBAL_DOMAIN = "*"

#: How many consecutive escalations before a domain skips the static fetch
#: (`P1-27`). One static success resets the count.
RENDER_JS_THRESHOLD = 3

#: How long the conclusion holds before the domain is re-probed, since a domain
#: going straight to the browser yields no evidence. See docs/features/crawling.md#rendering.
RENDER_JS_TTL = dt.timedelta(days=7)


#: Keys that ride in the global row's settings and are not fetch settings, so
#: :func:`resolve_policy` strips them. Shared with Admin's resolved view.
#: ``source_tiers`` (domain → tier), ``frontier`` (what enters the queue),
#: ``search_languages`` (`B-52`), ``steering_proposal_window_hours`` (`P6-38`),
#: ``display_timezone`` (`B-145`, ADR 0009).
NOT_FETCH_SETTINGS = frozenset(
    {
        "source_tiers",
        "frontier",
        "search_languages",
        "steering_proposal_window_hours",
        "display_timezone",
    }
)


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
    # Seconds to hold a challenge interstitial open in the browser; bounded, because
    # the interactive kind never clears. 0 disables the re-fetch.
    challenge_wait_s: int = Field(default=15, ge=0)
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
    for key in NOT_FETCH_SETTINGS:
        settings.pop(key, None)

    # A per-domain row carries status; the global row's status is not inherited,
    # because blocking '*' would silently stop the entire crawl.
    status = specific.status if specific else "active"

    # What the crawl learned about this domain (`P1-27`): only ever `auto` → `always`,
    # keyed off the merged value. See docs/features/crawling.md#rendering.
    unconfigured = settings.get("render_js", "auto") == "auto"
    if specific is not None and unconfigured and learned_render_js(specific):
        settings["render_js"] = "always"

    return ResolvedPolicy(domain=host, status=status, **settings)


def learned_render_js(row: FetchPolicyRow, *, now: dt.datetime | None = None) -> bool:
    """Whether this domain has earned going straight to the browser.

    Needs both the consecutive count and a recent enough observation, so the
    conclusion expires (:data:`RENDER_JS_TTL`).
    """
    if row.render_js_escalations < RENDER_JS_THRESHOLD:
        return False
    if row.render_js_learned_at is None:
        return False
    moment = now or dt.datetime.now(dt.UTC)
    return moment - row.render_js_learned_at < RENDER_JS_TTL


async def source_tier_map(sess: AsyncSession) -> dict[str, Any]:
    """The domain → tier mapping, from the global fetch policy row (§5.2, §13.1).

    Stored in `fetch_policy['*'].settings`, seeded from `config/source_tiers.yaml`.
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

    A lookup, never a model judgement (§5.2), and separate from
    :func:`resolve_policy` because a tier belongs to a source, not a request.
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
# Three answers: alive, unreachable, or no evidence. See docs/features/crawling.md#refusals.

#: The domain answered; what went wrong was about the URL. Resets the counter,
#: because only consecutive failures block.
DOMAIN_ALIVE = frozenset(
    {
        "success",
        "not_modified",
        "too_large",
        "content_type_rejected",
        "parse_error",
    }
)

#: Nothing usable came back and the domain is why, including hosts it is wrong to
#: keep requesting from (a decompression bomb, an address ``netguard`` refuses).
DOMAIN_UNREACHABLE = frozenset(
    {
        "timeout",
        "connection_error",
        "too_many_redirects",
        "decompression_bomb",
        "unsafe_target",
    }
)

#: No request went out, so there is nothing to conclude. `robots_unreachable` is
#: one cached failure served to every task on the origin.
DOMAIN_NO_SIGNAL = frozenset({"robots_denied", "robots_unreachable", "blocked"})

# `http_error` is in none of the three: 5xx and 429 count against the domain,
# every other 4xx is the domain answering.
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
    # An outcome nobody classified: logged, not fatal. The drift test over
    # FETCH_OUTCOME keeps this branch unreachable.
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


# --------------------------------------------------------------------------
# A domain that refuses every request (task B-114)
# --------------------------------------------------------------------------
# Not a consecutive rule: "never once anything but a refusal". See
# docs/features/crawling.md#refusing-domains.

#: The status a refusing domain answers with.
REFUSED_STATUS = 403

#: Requests that must have gone out, all refused, before a domain is blocked.
REFUSAL_MIN_ATTEMPTS = 20

#: How far back the refusals are counted.
REFUSAL_WINDOW_DAYS = 30

#: Who writes the block, so a later pass can tell its own verdict from a person's.
REFUSAL_ACTOR = "refusals"


async def refusing_domains(
    sess: AsyncSession, *, now: dt.datetime | None = None
) -> list[tuple[str, int]]:
    """Active domains that refused every request in the window, with the count.

    Only requests that went out count, and only those after a person last edited the
    domain's row, so an unblock gives a fresh window.
    """
    now = now or dt.datetime.now(dt.UTC)
    since = now - dt.timedelta(days=REFUSAL_WINDOW_DAYS)

    async def tally(after: dt.datetime, domains: list[str] | None = None) -> dict[str, int]:
        refused = (FetchAttempt.outcome == "http_error") & (
            FetchAttempt.status_code == REFUSED_STATUS
        )
        stmt = (
            select(FetchAttempt.domain, func.count())
            .where(
                FetchAttempt.attempted_at > after,
                FetchAttempt.outcome.not_in(sorted(DOMAIN_NO_SIGNAL)),
            )
            .group_by(FetchAttempt.domain)
            .having(func.count() >= REFUSAL_MIN_ATTEMPTS, func.bool_and(refused))
        )
        if domains is not None:
            stmt = stmt.where(FetchAttempt.domain.in_(domains))
        return dict((await sess.execute(stmt)).all())

    candidates = await tally(since)
    if not candidates:
        return []
    policies = {
        row.domain: row
        for row in await sess.scalars(
            select(FetchPolicyRow).where(FetchPolicyRow.domain.in_(sorted(candidates)))
        )
    }
    found = []
    for domain, n in sorted(candidates.items()):
        row = policies.get(domain)
        if row is not None and row.status != "active":
            continue
        # Counted only since the row was last set by hand, or since this rule
        # lifted its own block: either way the older refusals were answered.
        if row is not None and row.updated_by not in (None, "worker"):
            recount = await tally(max(since, row.updated_at or since), [domain])
            if domain not in recount:
                continue
            n = recount[domain]
        found.append((domain, n))
    return found


async def block_refusing_domains(
    sess: AsyncSession, *, now: dt.datetime | None = None
) -> list[tuple[str, int]]:
    """Block every domain :func:`refusing_domains` finds. Does not commit.

    ``blocked``, with a note naming the rule, so it is visible and reversible in Admin.
    A block this rule made lifts after :data:`REFUSAL_WINDOW_DAYS` and the domain is
    judged afresh on the next :data:`REFUSAL_MIN_ATTEMPTS` requests; other blocks are
    not touched.
    """
    stamp = now or dt.datetime.now(dt.UTC)
    expired = await sess.scalars(
        select(FetchPolicyRow).where(
            FetchPolicyRow.status == "blocked",
            FetchPolicyRow.updated_by == REFUSAL_ACTOR,
            FetchPolicyRow.updated_at < stamp - dt.timedelta(days=REFUSAL_WINDOW_DAYS),
        )
    )
    for row in expired:
        row.status = "active"
        row.note = f"refusal block lifted after {REFUSAL_WINDOW_DAYS} days; judged afresh — B-114"
        row.updated_at = stamp
    await sess.flush()

    found = await refusing_domains(sess, now=now)
    for domain, n in found:
        row = await sess.scalar(select(FetchPolicyRow).where(FetchPolicyRow.domain == domain))
        if row is None:
            row = FetchPolicyRow(domain=domain, settings={}, consecutive_failures=0)
            sess.add(row)
        row.status = "blocked"
        row.note = (
            f"auto-blocked: all {n} requests in {REFUSAL_WINDOW_DAYS} days refused "
            f"(HTTP {REFUSED_STATUS}) — B-114"
        )
        row.updated_at = stamp
        row.updated_by = REFUSAL_ACTOR
    await sess.flush()
    if found:
        log.info("blocked refusing domains", extra={"domains": len(found)})
    return found


async def record_render_outcome(
    sess: AsyncSession,
    domain: str,
    *,
    escalated: bool,
    now: dt.datetime | None = None,
) -> None:
    """Record whether this domain needed the browser (task P1-27). Flushes.

    Only `auto` fetches are evidence. Consecutive: one static fetch that was enough
    puts the domain back to zero.
    """
    host = registrable_domain(domain)
    row = await sess.get(FetchPolicyRow, host)
    if row is None:
        # No row, nothing learned. Creating one here would fill `fetch_policy`
        # with a row per domain the frontier ever touched, which is a table of
        # configuration nobody wrote.
        return

    if not escalated:
        if row.render_js_escalations or row.render_js_learned_at:
            row.render_js_escalations = 0
            row.render_js_learned_at = None
            await sess.flush()
        return

    moment = now or dt.datetime.now(dt.UTC)
    row.render_js_escalations += 1
    if row.render_js_escalations >= RENDER_JS_THRESHOLD and not learned_render_js(row, now=moment):
        # Stamped when the threshold is crossed *and* whenever a previous
        # conclusion has expired, so a re-probe that confirms the old answer
        # renews it rather than re-counting from zero.
        row.render_js_learned_at = moment
        log.info(
            "domain goes straight to the browser from now on",
            extra={"domain": host, "escalations": row.render_js_escalations},
        )
    await sess.flush()
