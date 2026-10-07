"""Print what changed between two `snapshot.sql` outputs, one line per changed count or field.

Used by `leakcheck.sh`. Exits 1 if anything changed. See docs/reference/commands.md#leak-check.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def entries(path: str) -> dict[str, object]:
    """Snapshot lines keyed by table and row: counts as ints, configuration rows as dicts."""
    out: dict[str, object] = {}
    for line in Path(path).read_text().splitlines():
        name, _, rest = line.partition(" ")
        if rest.startswith("rows "):
            out[f"{name} rows"] = int(rest[5:])
            continue
        row = json.loads(rest)
        key = row.get("topic") or row.get("agent_id") or row.get("domain") or row.get("name")
        out[f"{name} {key or rest}"] = row
    return out


def changes(before: dict[str, object], after: dict[str, object]) -> list[str]:
    """Human-readable differences: counts, and fields of the rows present in both."""
    lines = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        if isinstance(old, dict) and isinstance(new, dict):
            for field in sorted(set(old) | set(new)):
                if old.get(field) != new.get(field):
                    lines.extend(field_changes(f"{key}.{field}", old.get(field), new.get(field)))
        elif old is None or new is None:
            lines.append(f"{key}: {'added' if old is None else 'removed'}")
        else:
            lines.append(f"{key}: {old} -> {new}")
    return lines


def field_changes(path: str, old: object, new: object) -> list[str]:
    """One line per changed leaf, so a nested settings blob names the key that moved."""
    if isinstance(old, dict) and isinstance(new, dict):
        out = []
        for key in sorted(set(old) | set(new)):
            if old.get(key) != new.get(key):
                out.extend(field_changes(f"{path}.{key}", old.get(key), new.get(key)))
        return out
    return [f"{path}: {json.dumps(old)} -> {json.dumps(new)}"]


def main() -> int:
    """Compare the two snapshot files named on the command line."""
    found = changes(entries(sys.argv[1]), entries(sys.argv[2]))
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
