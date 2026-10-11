"""Lexical search over Chinese, Japanese and Korean text (task `B-217`).

These scripts are written without spaces between words, so the `english` parser keeps a
whole run as one token and no word inside it can be found. Runs are indexed instead as
overlapping two-character pairs, the usual approach for unsegmented scripts; a query's run
becomes a phrase of the same pairs. The database half is `meridian_cjk_bigrams()`, created
by migration `b217`, and the two must split alike. See docs/features/search.md#cjk.
"""

from __future__ import annotations

import re

__all__ = ["CJK_CLASS", "SQL_FUNCTION", "TS_CONFIG", "bigrams", "has_cjk", "query_text"]

#: Kana, CJK ideographs (with extension A) and Hangul syllables, as escapes both Python's
#: and Postgres's regular expressions read. Written into SQL exactly as the migration has
#: it: the partial index serves only a predicate that matches it character for character.
CJK_CLASS = r"\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af"

#: The SQL function and the configuration its output is indexed under. `simple`: there is
#: nothing to stem in a pair of characters, and English stop words are not these languages'.
SQL_FUNCTION = "meridian_cjk_bigrams"
TS_CONFIG = "simple"

_RUN = re.compile(f"[{CJK_CLASS}]+")


def has_cjk(text: str) -> bool:
    """Whether the text holds any character these scripts are indexed by."""
    return _RUN.search(text) is not None


def _pairs(run: str) -> str:
    if len(run) < 2:
        return run
    return " ".join(run[i : i + 2] for i in range(len(run) - 1))


def bigrams(text: str) -> str:
    """Each run as overlapping pairs, set off by spaces; other text unchanged.

    The Python twin of `meridian_cjk_bigrams()`.
    """
    return _RUN.sub(lambda m: f" {_pairs(m.group())} ", text)


def query_text(query: str) -> str:
    """A query for `websearch_to_tsquery`, with each run a quoted phrase of its pairs.

    A phrase, so the pairs must stand next to each other as the query wrote them: ANDed
    loose, "数据保护" would match any passage holding 数据, 据保 and 保护 anywhere. A run
    already inside the reader's quotes joins their phrase rather than opening its own.
    """
    out: list[str] = []
    quoted = False
    last = 0
    for match in _RUN.finditer(query):
        before = query[last : match.start()]
        quoted ^= before.count('"') % 2 == 1
        out.append(before)
        pairs = _pairs(match.group())
        out.append(f" {pairs} " if quoted else f' "{pairs}" ')
        last = match.end()
    out.append(query[last:])
    return "".join(out)
