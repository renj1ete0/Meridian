"""robots.txt: parsing, matching, and caching (task P1-04, spec §6.4, §14.2).

§14.2 makes respecting robots.txt a stated commitment, not a setting, so the
question of *which* rules a path matches has to have one answer.

**Why this is not `urllib.robotparser`.** The stdlib parser was rewritten for
RFC 9309 in Python 3.13. Before that it had no wildcard support and returned the
first matching rule rather than the longest. On the two most ordinary patterns in
a real robots.txt it gives opposite answers across the versions this project
supports (`requires-python = ">=3.12"`)::

                            3.12    3.13+
    Disallow: /*.pdf$       fetch   refuse     <- wildcard ignored entirely
    Allow: /private/notice  refuse  fetch      <- shorter Disallow won instead

A crawler whose conduct depends on which interpreter its container happened to
ship is not respecting robots.txt; it is respecting robots.txt on some machines.
The rules below are RFC 9309 §2.2: longest match wins, ``Allow`` breaks a tie,
``*`` matches any run of characters and ``$`` anchors the end.

The other half of conduct is what happens when robots.txt cannot be read.
RFC 9309 §2.3.1.3 is deliberately asymmetric and this follows it: **4xx means
allow everything** (the site has no rules to state), while a 5xx or a timeout
means **refuse everything** until it can be read. Guessing "probably fine" about
a server that is currently broken is how a crawler ends up banned. The refusal is
cached for minutes rather than a day so the domain comes back quickly.
"""

from __future__ import annotations

import dataclasses
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

from meridian_core.logging import get_logger
from meridian_core.policy import ResolvedPolicy

from .fetch import FetchResult

log = get_logger(__name__)

# How long a parsed robots.txt is trusted. A day is the conventional figure and
# the one Google documents; robots.txt changes rarely and re-fetching it per URL
# would multiply the crawl's request count by two.
ROBOTS_TTL_S = 86_400

# A refusal caused by an unreachable server is cached far more briefly. The
# alternative — caching "refuse everything" for a day because of one 503 — takes
# a domain out of the crawl for a day over a blip.
ROBOTS_ERROR_TTL_S = 600

# Google's documented parse limit, and a sane bound on a file that should be a
# few hundred lines. Beyond this the remainder is ignored rather than the file
# rejected, which is what RFC 9309 §2.5 calls for.
ROBOTS_MAX_BYTES = 512 * 1024

_GROUP_FIELDS = frozenset({"user-agent", "allow", "disallow", "crawl-delay"})


@dataclasses.dataclass(frozen=True)
class Rule:
    """One ``Allow:`` or ``Disallow:`` line, compiled for matching."""

    allow: bool
    pattern: str
    matcher: re.Pattern[str]

    @property
    def specificity(self) -> int:
        """Longer patterns win (RFC 9309 §2.2.2).

        Measured on the pattern as written, wildcards included, which is what
        every widely-deployed implementation does: ``/*.pdf$`` beats ``/`` and
        loses to ``/reports/annual.pdf``.
        """
        return len(self.pattern)


def _compile(pattern: str) -> re.Pattern[str]:
    """Turn a robots path pattern into a regex.

    ``*`` is any run of characters and a trailing ``$`` anchors the end; every
    other character is literal, which matters because paths are full of regex
    metacharacters (``.``, ``+``, ``?``, brackets) that must not be interpreted.
    """
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = "".join(".*" if char == "*" else re.escape(char) for char in body)
    return re.compile(regex + ("$" if anchored else ""))


@dataclasses.dataclass(frozen=True)
class RobotsRules:
    """The rules of one robots.txt, already narrowed to one user agent."""

    rules: tuple[Rule, ...] = ()
    crawl_delay_s: float | None = None
    sitemaps: tuple[str, ...] = ()
    # Set when robots.txt could not be read at all, so a caller can tell
    # "the site allows this" from "we could not find out" (§2.3.1.3).
    unreachable: bool = False

    def allows(self, url_or_path: str) -> bool:
        """Is this path fetchable? Longest match wins; a tie goes to Allow.

        No matching rule means allowed — robots.txt is a list of exclusions, and
        a file that mentions nothing relevant permits everything.
        """
        target = _match_target(url_or_path)
        best: Rule | None = None
        for rule in self.rules:
            if not rule.matcher.match(target):
                continue
            # Longer wins; on a tie Allow wins, so an equal-length Allow may
            # displace a Disallow but never the other way round.
            if (
                best is None
                or rule.specificity > best.specificity
                or (rule.specificity == best.specificity and rule.allow)
            ):
                best = rule
        return best.allow if best is not None else True


ALLOW_ALL = RobotsRules()
DENY_ALL = RobotsRules(
    rules=(Rule(allow=False, pattern="/", matcher=_compile("/")),), unreachable=True
)


def _match_target(url_or_path: str) -> str:
    """The path (and query) a rule is matched against.

    A full URL is accepted because that is what callers hold; the host is
    discarded, since robots.txt only ever constrains paths on its own origin.
    """
    parts = urlsplit(url_or_path)
    path = parts.path or "/"
    return f"{path}?{parts.query}" if parts.query else path


def product_token(user_agent: str) -> str:
    """The name a robots.txt group refers to, from a full User-Agent string.

    ``MeridianBot/0.1 (+https://example.org/contact)`` is ``meridianbot``: RFC
    9309 §2.2.1 matches on the product token alone, never on the version or the
    comment.
    """
    return re.split(r"[/\s]", user_agent.strip(), maxsplit=1)[0].lower()


def _lines(text: str) -> Iterable[tuple[str, str]]:
    """Yield ``(field, value)`` for each meaningful line."""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        yield field.strip().lower(), value.strip()


def parse(text: str, user_agent: str) -> RobotsRules:
    """Parse robots.txt and return the rules that apply to ``user_agent``.

    Group selection is RFC 9309 §2.2.1: the group whose name is the longest
    prefix of our product token wins, and ``*`` is the fallback used only when
    no named group matches at all. That last part is easy to get wrong — a site
    with a ``MeridianBot`` group and a stricter ``*`` group is telling us to
    ignore the ``*`` group entirely, not to obey both.
    """
    token = product_token(user_agent)

    # agent name -> rules, merged across repeated groups for the same agent.
    groups: dict[str, list[Rule]] = {}
    delays: dict[str, float] = {}
    sitemaps: list[str] = []

    current: list[str] = []
    starting_group = True

    for field, value in _lines(text):
        if field == "sitemap":
            if value:
                sitemaps.append(value)
            continue
        if field not in _GROUP_FIELDS:
            continue

        if field == "user-agent":
            # A user-agent line after a rule line begins a new group; one
            # directly after another user-agent line extends the same group.
            if not starting_group:
                current = []
                starting_group = True
            name = value.lower()
            if name:
                current.append(name)
                groups.setdefault(name, [])
            continue

        starting_group = False
        if not current:
            continue  # a rule before any user-agent line belongs to nothing

        if field == "crawl-delay":
            try:
                delay = float(value)
            except ValueError:
                continue
            if delay >= 0:
                for name in current:
                    delays[name] = delay
            continue

        # `Disallow:` with no value means "nothing is disallowed" and carries no
        # rule at all — recording it as a pattern matching everything would
        # invert its meaning. An empty `Allow:` is equally vacuous.
        if not value:
            continue
        rule = Rule(allow=field == "allow", pattern=value, matcher=_compile(value))
        for name in current:
            groups[name].append(rule)

    named = [name for name in groups if name != "*" and token.startswith(name)]
    chosen = max(named, key=len) if named else ("*" if "*" in groups else None)

    if chosen is None:
        return RobotsRules(sitemaps=tuple(sitemaps))
    return RobotsRules(
        rules=tuple(groups[chosen]),
        crawl_delay_s=delays.get(chosen),
        sitemaps=tuple(sitemaps),
    )


def robots_url(url: str) -> str:
    """The robots.txt governing ``url``.

    Per origin, not per registrable domain: ``https://data.example.gov`` and
    ``http://example.gov`` publish separate files and may say different things.
    """
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))


Fetch = Callable[[str, ResolvedPolicy], Awaitable[FetchResult]]


class RobotsCache:
    """Fetches and caches robots.txt, one entry per origin.

    Takes the fetch callable rather than a :class:`~worker.fetch.Fetcher` so the
    request goes out through the same rate limiter as everything else — a
    crawler that respects a domain's delay for pages and hammers it for
    robots.txt has missed the point — and through the same SSRF guard, because
    ``/robots.txt`` on an attacker-supplied host is as much a fetch as any other.
    """

    def __init__(self, fetch: Fetch, *, ttl_s: int = ROBOTS_TTL_S) -> None:
        self._fetch = fetch
        self._ttl_s = ttl_s
        self._cache: dict[str, tuple[float, RobotsRules]] = {}

    def _cached(self, origin: str) -> RobotsRules | None:
        entry = self._cache.get(origin)
        if entry is None:
            return None
        expires_at, rules = entry
        if expires_at <= time.monotonic():
            del self._cache[origin]
            return None
        return rules

    def _store(self, origin: str, rules: RobotsRules) -> RobotsRules:
        ttl = ROBOTS_ERROR_TTL_S if rules.unreachable else self._ttl_s
        self._cache[origin] = (time.monotonic() + ttl, rules)
        return rules

    async def rules_for(self, url: str, policy: ResolvedPolicy) -> RobotsRules:
        """The rules governing ``url``, fetching robots.txt if not cached."""
        target = robots_url(url)
        cached = self._cached(target)
        if cached is not None:
            return cached

        # robots.txt is plain text, small, and never worth a browser. The
        # content-type allowlist is dropped for it specifically: servers label
        # it text/plain, text/html and application/octet-stream about equally,
        # and none of that changes what the file means.
        robots_policy = policy.model_copy(
            update={
                "allowed_content_types": [],
                "max_page_bytes": ROBOTS_MAX_BYTES,
                "render_js": "never",
                # A site that serves its pages over https but redirects
                # robots.txt to http would otherwise take the whole domain out
                # of the crawl. The file carries no content worth protecting.
                "require_https_final": False,
            }
        )
        result = await self._fetch(target, robots_policy)

        if result.ok:
            return self._store(target, parse(result.text(), policy.user_agent))

        if result.outcome == "too_large":
            # Over the parse limit is not a refusal to serve; RFC 9309 §2.5 says
            # to use what was read, and nothing was kept, so treat it as silent.
            log.warning("robots.txt over the parse limit; treating as empty", extra={"url": target})
            return self._store(target, ALLOW_ALL)

        if result.status_code is not None and 400 <= result.status_code < 500:
            return self._store(target, ALLOW_ALL)

        log.warning(
            "robots.txt unreachable; refusing the origin until it can be read",
            extra={"url": target, "outcome": result.outcome, "detail": result.detail},
        )
        return self._store(target, DENY_ALL)

    def prime(self, url: str, rules: RobotsRules) -> None:
        """Seed the cache directly. For tests and for a warm restart."""
        self._store(robots_url(url), rules)
