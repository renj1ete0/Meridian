#!/usr/bin/env python
"""Run the held-out question set and write one run file (task P2-22, spec §14.1).

Asks every question in `eval/questions.yaml` through hybrid search, exactly as
typed, and writes `eval/runs/<date>.yaml`: the context §14.1 asks for, a banner
while the set is unreviewed, the top hits per item, a heuristic grade
*proposal* per item, an empty `operator` field per item for the real score, and
a comparison with the previous run. Read-only against the database.

The vector arm needs the embedding sidecar (`MERIDIAN_EMBEDDER_URL`). Without
it, or with `--lexical-only`, the run says it is lexical-only; on whole
questions that returns almost nothing, and the file says so rather than let a
reader conclude the corpus is empty.

Usage:
    uv run python scripts/run_question_set.py            # writes eval/runs/<date>.yaml
    uv run python scripts/run_question_set.py --stdout   # prints instead

On a deployment, run it in the `tools` container, which mounts the directory the
API reads and sets `MERIDIAN_EVAL_RUNS_DIR` to it (docs/deployment.md):

    docker compose run --rm tools python scripts/run_question_set.py
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import sys
from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from meridian_core.db import normalize_url
from meridian_core.embedder import EmbeddingUnavailable, RemoteEmbedder
from meridian_core.questionset import (
    attach_comparison,
    load,
    next_run_path,
    previous_run,
    read_run,
    run_set,
    runs_dir,
)

REPO = Path(__file__).resolve().parents[1]


def dump(run: dict) -> str:
    return yaml.safe_dump(run, sort_keys=False, allow_unicode=True, width=100)


async def build(
    sess: AsyncSession, questions: Path, *, lexical_only: bool, k: int, version: str | None
) -> dict:
    qs = load(questions)
    embedder = None if lexical_only else RemoteEmbedder.from_env()
    model = None
    embed = None
    if embedder is not None:
        described = await embedder.describe()
        model = (described or {}).get("model")

        async def embed(text: str):
            try:
                return await embedder.embed_one(text)
            except EmbeddingUnavailable as exc:
                print(
                    f"embedder did not answer ({exc}); this item is lexical-only", file=sys.stderr
                )
                return None

    try:
        return await run_set(sess, qs, embed=embed, embedder_model=model, version=version, k=k)
    finally:
        if embedder is not None:
            await embedder.aclose()


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--questions", type=Path, default=REPO / "eval" / "questions.yaml")
    # `MERIDIAN_EVAL_RUNS_DIR` first: on a deployment it names the directory the
    # API mounts for Gaps, and a run written anywhere else is a run Gaps never sees.
    parser.add_argument("--runs-dir", type=Path, default=runs_dir(REPO / "eval" / "runs"))
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--lexical-only", action="store_true")
    parser.add_argument("--stdout", action="store_true", help="print the run, write nothing")
    args = parser.parse_args(argv)

    url = os.environ.get("PG_RO_URL") or os.environ.get("PG_RW_URL")
    if not url:
        raise SystemExit("PG_RO_URL (or PG_RW_URL) must be set")
    version_file = REPO / "VERSION"
    version = version_file.read_text().strip() if version_file.exists() else None

    engine = create_async_engine(normalize_url(url))
    try:
        async with async_sessionmaker(engine, class_=AsyncSession)() as sess:
            run = await build(
                sess, args.questions, lexical_only=args.lexical_only, k=args.k, version=version
            )
    finally:
        await engine.dispose()

    args.runs_dir.mkdir(parents=True, exist_ok=True)
    prior = previous_run(args.runs_dir)
    attach_comparison(run, read_run(prior) if prior else None, prior.name if prior else None)

    if run["banner"]:
        print(run["banner"], file=sys.stderr)
    if args.stdout:
        sys.stdout.write(dump(run))
        return 0
    path = next_run_path(args.runs_dir, dt.date.today())
    path.write_text(dump(run))
    print(f"wrote {path.relative_to(REPO) if path.is_relative_to(REPO) else path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
