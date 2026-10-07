"""Rebuild the corpus's areas (task `P6-30`).

``--once`` builds (`meridian_core.areabuild`) and names each area from
``config/fields.yaml``; derived data, so rerunning is safe. ``--report`` prints a build
without writing; ``--name-only`` renames the newest build (`B-74`).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import time

import numpy as np
from sqlalchemy import select

from meridian_core.areabuild import BuildReport, build_areas
from meridian_core.areaview import area_name, usable_terms
from meridian_core.db import dispose_engines, session
from meridian_core.fields import FieldLabel, assign, load_fields
from meridian_core.logging import bind_run_id, configure_logging, get_logger
from meridian_core.models import Area

log = get_logger(__name__)


async def _label_vectors(embedder, labels: list[FieldLabel]) -> np.ndarray:
    vectors = await embedder.embed([label.text() for label in labels])
    return np.asarray(vectors, dtype=np.float64)


async def name_build(sess, build_id: int, embedder) -> int:
    """Name every area of a build by field of work. Returns how many got one.

    Flushes; the caller commits. An area nothing fits keeps ``field`` NULL and
    is named by its terms.
    """
    fields, subfields = load_fields()
    field_vecs = await _label_vectors(embedder, fields)
    subfield_vecs = await _label_vectors(embedder, subfields)
    areas = list(await sess.scalars(select(Area).where(Area.build_id == build_id)))
    if not areas:
        return 0
    index = {a.area_id: i for i, a in enumerate(areas)}
    names = assign(
        [a.level for a in areas],
        np.asarray([a.centroid for a in areas], dtype=np.float64),
        fields,
        field_vecs,
        subfields,
        subfield_vecs,
        parents=[index.get(a.parent_id) for a in areas],
        weights=[a.passages for a in areas],
    )
    names = distinct_per_level([a.level for a in areas], names, [list(a.terms) for a in areas])
    for area, name in zip(areas, names, strict=True):
        area.field = name
    await sess.flush()
    return sum(1 for name in names if name)


def distinct_per_level(
    levels: list[int], names: list[str | None], terms: list[list[str]]
) -> list[str | None]:
    """Names unique within each level (`B-74`).

    Names still shared after the second choice carry the area's own best phrase from its
    cleaned terms: "Transportation (fares and transit)".
    """
    seen: dict[tuple[int, str], list[int]] = {}
    for i, name in enumerate(names):
        if name:
            seen.setdefault((levels[i], name), []).append(i)
    out = list(names)
    for (_, name), members in seen.items():
        if len(members) < 2:
            continue
        used: set[str] = set()
        for i in members:
            phrase = next(
                (
                    t
                    for t in usable_terms(terms[i])[:5]
                    if t.lower() not in name.lower() and t not in used
                ),
                None,
            )
            if phrase:
                used.add(phrase)
                out[i] = f"{name} ({phrase})"
    return _shortened(levels, out)


def _shortened(levels: list[int], names: list[str | None]) -> list[str | None]:
    """Drop the second field from a name that also carries its own phrase (`B-102`).

    Only where the short form stays unique within its level.
    """
    short: list[str | None] = []
    for name in names:
        if name and " & " in name and name.endswith(")") and " (" in name:
            head, phrase = name.rsplit(" (", 1)
            short.append(f"{head.split(' & ', 1)[0]} ({phrase}")
        else:
            short.append(name)
    taken: dict[tuple[int, str], int] = {}
    for level, name in zip(levels, short, strict=True):
        if name:
            taken[(level, name)] = taken.get((level, name), 0) + 1
    return [
        s if s is None or taken[(level, s)] == 1 else original
        for level, s, original in zip(levels, short, names, strict=True)
    ]


async def _embedder():
    from .vectors import build_embedder

    return await build_embedder()


async def run_once(
    *, write: bool, name_only: bool = False
) -> tuple[BuildReport | None, list[tuple[int, int, int, str]]]:
    """One build (or, with ``name_only``, the newest build named again).

    Returns the report and ``(level, passages, sources, name)`` rows.
    """
    async with session() as sess:
        if name_only:
            from meridian_core.areaview import latest_build

            build = await latest_build(sess)
            report = None
            build_id = build.build_id if build is not None else None
        else:
            report = await build_areas(sess)
            build_id = report.build_id
        rows: list[tuple[int, int, int, str]] = []
        if build_id is not None:
            named = await name_build(sess, build_id, await _embedder())
            log.info("areas named by field", extra={"build_id": build_id, "named": named})
            rows = [
                (level, passages, sources, area_name(list(terms), field))
                for level, passages, sources, terms, field in await sess.execute(
                    select(Area.level, Area.passages, Area.sources, Area.terms, Area.field)
                    .where(Area.build_id == build_id, Area.level < 3)
                    .order_by(Area.level, Area.passages.desc())
                )
            ]
        if write:
            await sess.commit()
        else:
            await sess.rollback()
    if report is None:
        return None, rows
    log.info(
        "areas built",
        extra={
            "build_id": report.build_id,
            "passages": report.passages,
            "regions": report.regions,
            "areas": report.areas,
            "leaves": report.leaves,
            "inherited": report.inherited,
            "bridges": report.bridges,
            "seconds": report.seconds,
            "written": write,
        },
    )
    return report, rows


def main() -> None:
    """Entry point: ``python -m worker.areas``."""
    parser = argparse.ArgumentParser(description="Rebuild the corpus's areas (P6-30).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="build and write")
    mode.add_argument("--report", action="store_true", help="build, print, write nothing")
    mode.add_argument(
        "--name-only", action="store_true", help="name the newest build by field; no rebuild"
    )
    args = parser.parse_args()

    configure_logging("areas")

    async def go():
        try:
            return await run_once(write=args.once or args.name_only, name_only=args.name_only)
        finally:
            await dispose_engines()

    with bind_run_id(f"areas-{int(time.time())}"), contextlib.suppress(KeyboardInterrupt):
        report, rows = asyncio.run(go())
    if report is None:
        for level, passages, sources, name in rows:
            indent = "  " if level == 2 else ""
            print(f"{indent}{passages:6d} passages {sources:5d} sources  {name}")
        return
    if report.build_id is None:
        print(f"{report.passages} searchable embedded passages: too few to cluster.")
        return
    print(
        f"{report.passages} passages → {report.regions} regions, {report.areas} areas, "
        f"{report.leaves} sub-areas, {report.bridges} bridges in {report.seconds}s; "
        f"{report.inherited} kept their position"
    )
    for level, passages, sources, name in rows:
        indent = "  " if level == 2 else ""
        print(f"{indent}{passages:6d} passages {sources:5d} sources  {name}")
    if args.report:
        print("\nReport only. --once writes.")


if __name__ == "__main__":
    main()
