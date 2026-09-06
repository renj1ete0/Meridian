"""Seed configuration from ``config/*.yaml`` into the database (spec §13.1).

Run once at first boot, and safely re-runnable. After this, **the database is
authoritative** — the YAML is not read again, and editing it has no effect.
Topic weights, fetch policy, model routing and gazetteer terms are changed in
Admin or over MCP, not by editing a file on the host.

**This script loads configuration only. It never loads content.** Production
starts empty (scaffold §1.7): no sources, no chunks, no entities, no edges.
There is deliberately no fixture-loading path here, because synthetic fixtures
do not resemble real extraction output and UI built against them gets rebuilt.
Development corpora are snapshots of real crawls, restored separately.

Idempotency rule: this **inserts what is missing and leaves what exists alone**.
It does not overwrite. A weight you changed in Admin six months ago must survive
a re-run — otherwise re-seeding would silently undo steering, and §10's promise
that nothing is destroyed would be false.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from meridian_core.db import dispose_engines, session
from meridian_core.logging import configure_logging, get_logger
from meridian_core.models import (
    Agent,
    AttributeDefinition,
    FetchPolicy,
    GazetteerTerm,
    QueueTask,
    TopicConfig,
)

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

log = get_logger(__name__)


def _load(name: str) -> Any:
    path = CONFIG_DIR / name
    if not path.exists():
        log.warning("config file missing, skipping", extra={"file": name})
        return None
    with path.open() as fh:
        return yaml.safe_load(fh)


async def seed_topics(sess) -> tuple[int, int]:
    data = _load("topics.yaml") or {}
    added = skipped = 0
    for row in data.get("topics", []):
        existing = await sess.scalar(select(TopicConfig).where(TopicConfig.topic == row["topic"]))
        if existing is not None:
            skipped += 1
            continue
        sess.add(
            TopicConfig(
                topic=row["topic"],
                weight=row.get("weight", 0.0),
                floor=row.get("floor", 0.05),
                ceiling=row.get("ceiling", 1.0),
                pinned=row.get("pinned", False),
                status=row.get("status", "active"),
            )
        )
        added += 1
    return added, skipped


async def seed_attributes(sess) -> tuple[int, int]:
    data = _load("attributes.yaml") or {}
    added = skipped = 0

    async def add(name: str, description: str | None, scope: str, topic: str | None) -> None:
        nonlocal added, skipped
        existing = await sess.scalar(
            select(AttributeDefinition).where(AttributeDefinition.name == name)
        )
        if existing is not None:
            skipped += 1
            return
        sess.add(
            AttributeDefinition(
                name=name, description=description, scope=scope, topic=topic, status="active"
            )
        )
        added += 1

    for row in data.get("global", []):
        await add(row["name"], row.get("description"), "global", None)
    for topic, rows in (data.get("topic_local") or {}).items():
        for row in rows:
            await add(row["name"], row.get("description"), "topic_local", topic)
    return added, skipped


async def seed_fetch_policy(sess) -> tuple[int, int]:
    """The global '*' row. Per-domain overrides are created in Admin (§6.4).

    The source-tier mapping rides along in the same row rather than in a table
    of its own. It is one global blob of domain policy, read together with the
    fetch settings on every request, and §13.1 requires it to live in the
    database — leaving it in a file the worker re-reads would make the YAML
    authoritative again.
    """
    settings = _load("fetch_policy.yaml")
    if settings is None:
        return 0, 0
    tiers = _load("source_tiers.yaml")
    if tiers is not None:
        settings = {**settings, "source_tiers": tiers}
    existing = await sess.scalar(select(FetchPolicy).where(FetchPolicy.domain == "*"))
    if existing is not None:
        return 0, 1
    sess.add(FetchPolicy(domain="*", settings=settings, status="active", updated_by="seed"))
    return 1, 0


async def seed_gazetteer(sess) -> tuple[int, int]:
    data = _load("gazetteer_seed.yaml") or {}
    added = skipped = 0
    for row in data.get("terms", []):
        existing = await sess.scalar(
            select(GazetteerTerm).where(
                GazetteerTerm.canonical == row["canonical"],
                GazetteerTerm.jurisdiction.is_not_distinct_from(row.get("jurisdiction")),
                GazetteerTerm.entity_type == row["entity_type"],
            )
        )
        if existing is not None:
            skipped += 1
            continue
        sess.add(
            GazetteerTerm(
                canonical=row["canonical"],
                aliases=row.get("aliases") or [],
                entity_type=row["entity_type"],
                jurisdiction=row.get("jurisdiction"),
                # An ambiguous surface form offers candidates, never a decision:
                # the resolver disambiguates from document context, and leaves
                # the mention unresolved when context is insufficient (§5.5).
                ambiguous=row.get("ambiguous", False),
                topic_labels=row.get("topic_labels") or [],
                source=row.get("source", "manual"),
                # Hand-seeded terms are approved by definition; model-proposed
                # ones arrive later needing confirmation (§5.6).
                approved=row.get("approved", True),
            )
        )
        added += 1
    return added, skipped


async def seed_agents(sess) -> tuple[int, int]:
    data = _load("agents.yaml") or {}
    added = skipped = 0
    for row in data.get("agents", []):
        existing = await sess.scalar(select(Agent).where(Agent.agent_id == row["agent_id"]))
        if existing is not None:
            skipped += 1
            continue
        sess.add(
            Agent(
                agent_id=row["agent_id"],
                provider=row["provider"],
                model=row.get("model"),
                task_types=row.get("task_types") or [],
                token_scope=row.get("token_scope", "read"),
                cost_tier=row.get("cost_tier"),
                quality_tier=row.get("quality_tier"),
                max_context=row.get("max_context"),
                enabled=row.get("enabled", False),
                fallback_agent_id=row.get("fallback_agent_id"),
                # Endpoints in the YAML are ${ENV_VAR} references. They are
                # stored verbatim and resolved at call time — the registry holds
                # the name of the variable, never a secret value (§11.11).
                endpoint=row.get("endpoint"),
                health_url=row.get("health_url"),
                availability=row.get("availability", "on_demand"),
                wake_mac=row.get("wake_mac"),
            )
        )
        added += 1
    return added, skipped


async def seed_cold_start_queue(sess) -> tuple[int, int]:
    """Cold-start sources become queue tasks — the only rows that lead to content.

    These are URLs to crawl, not content itself, so this stays within "config
    only". The list is expected to be empty until it is hand-seeded (§15 phase 0,
    a task the spec deliberately reserves for a human because seed quality
    propagates through everything downstream).
    """
    data = _load("seed_sources.yaml") or {}
    sources = data.get("sources") or []
    added = skipped = 0
    for row in sources:
        url = row.get("url")
        if not url or url.startswith("<"):
            continue
        existing = await sess.scalar(select(QueueTask).where(QueueTask.url_or_query == url))
        if existing is not None:
            skipped += 1
            continue
        sess.add(
            QueueTask(
                url_or_query=url,
                # Query seeds run through the search backend rather than being
                # fetched directly; hardcoding "url" would send a search string
                # to the fetcher as if it were an address.
                task_type=row.get("task_type", "url"),
                status="pending",
                topic=row.get("topic"),
                seed_source="user",
                priority=row.get("priority", 100),  # cold-start seeds run first
            )
        )
        added += 1
    return added, skipped


async def main() -> int:
    configure_logging("seed")
    steps = {
        "topics": seed_topics,
        "attributes": seed_attributes,
        "fetch_policy": seed_fetch_policy,
        "gazetteer": seed_gazetteer,
        "agents": seed_agents,
        "cold_start_queue": seed_cold_start_queue,
    }

    totals: dict[str, tuple[int, int]] = {}
    async with session("rw") as sess:
        for name, fn in steps.items():
            added, skipped = await fn(sess)
            totals[name] = (added, skipped)
            log.info("seeded", extra={"step": name, "added": added, "skipped": skipped})

    await dispose_engines()

    width = max(len(k) for k in totals)
    print("\nSeed complete — configuration only, no content.\n")
    for name, (added, skipped) in totals.items():
        print(f"  {name:<{width}}  added {added:>3}   already present {skipped:>3}")
    if totals["cold_start_queue"][0] == 0:
        print(
            "\n  Note: no cold-start sources queued. Hand-seed config/seed_sources.yaml"
            "\n  before the first crawl — seed quality propagates through everything (§16)."
        )
    print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
