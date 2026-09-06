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

from .models import FetchPolicy as FetchPolicyRow
from .tiering import jittered_delay_ms, registrable_domain

_FILE_DEFAULTS_PATH = Path(__file__).resolve().parents[3] / "config" / "fetch_policy.yaml"
_file_defaults_cache: dict[str, Any] | None = None

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

    # A per-domain row carries status; the global row's status is not inherited,
    # because blocking '*' would silently stop the entire crawl.
    status = specific.status if specific else "active"
    return ResolvedPolicy(domain=host, status=status, **settings)


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
