"""Meridian's fast loop: fetch, extract, chunk, embed (spec §6.1).

Runs 23h/day on the ingestion node and **never calls an LLM** — the invariant
that lets ingestion keep working with every reasoning model offline (§2.1).
"""

__version__ = "0.22.0"
