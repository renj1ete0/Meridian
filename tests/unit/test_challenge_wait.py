"""Waiting out a bot-challenge interstitial (§6.4, `P1-03`).

A challenge is the one refusal a browser can sometimes turn into a success. The
common non-interactive kind runs a few seconds of JavaScript and then serves the
real page, so waiting is all that is needed — no evasion, just the patience an
ordinary browser has. The interactive kind never resolves, which is why the wait
is bounded and tried exactly once.

The risk being tested against is the opposite of the feature: a detector that
fires too readily sends ordinary 403s and 404s through a browser, which costs a
slot and a page load for a page that was never going to load.
"""

from __future__ import annotations

import pytest

from worker.fetch import is_challenge


# --------------------------------------------------------------------------
# Detection — and specifically, not over-detecting


def test_the_mitigation_header_is_enough() -> None:
    assert is_challenge(403, {"cf-mitigated": "challenge"})


def test_the_header_is_read_case_insensitively() -> None:
    assert is_challenge(503, {"CF-Mitigated": "Challenge"})


def test_a_body_marker_is_enough_when_there_is_no_header() -> None:
    """Not every challenge origin sets a header."""
    assert is_challenge(403, {}, b"<html><head><script src='/cdn-cgi/challenge-platform/x'>")
    assert is_challenge(503, {}, b"<title>Just a moment...</title>")


@pytest.mark.parametrize("status", [200, 301, 404, 410, 500, 502])
def test_ordinary_statuses_are_never_a_challenge(status: int) -> None:
    """A 404 through a browser is a browser slot spent on nothing."""
    assert not is_challenge(status, {"cf-mitigated": "challenge"}, b"just a moment")


def test_a_plain_403_is_not_a_challenge() -> None:
    """An ordinary forbidden page must not be re-fetched.

    This is the expensive false positive: every permission-denied URL in the
    corpus would otherwise pay for a browser launch and a settle wait.
    """
    assert not is_challenge(403, {"server": "nginx"}, b"<h1>Forbidden</h1>")


def test_a_non_challenge_mitigation_value_is_not_a_challenge() -> None:
    """Cloudflare blocks for reasons other than bot suspicion, and those do not
    resolve by waiting."""
    assert not is_challenge(403, {"cf-mitigated": "block"})


def test_a_missing_status_is_not_a_challenge() -> None:
    assert not is_challenge(None, {"cf-mitigated": "challenge"})


def test_challenge_markers_are_not_found_in_ordinary_prose() -> None:
    """An article *about* bot challenges must not trip this.

    The same distinction `P1-23` turns on: a page discussing a thing is not the
    thing. These markers are machine strings, not phrases anyone writes.
    """
    body = b"<p>Cloudflare will show a challenge page to suspicious clients.</p>"
    assert not is_challenge(403, {}, body)


def test_only_the_head_of_the_body_is_scanned() -> None:
    """A marker far down a large page is not the interstitial.

    The interstitial is small and says so immediately; scanning a whole 20MB
    body would also make detection cost scale with the page.
    """
    body = b"x" * 20_000 + b"just a moment"
    assert not is_challenge(403, {}, body)
