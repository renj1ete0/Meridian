"""Every dependency's licence is one somebody has already agreed to (task `B-11`).

`docs/licences.md` is the audit. This is the gate that stops it becoming a
document that was true once. A dependency added next year arrives with whatever
licence it has, and `uv add` says nothing — AGPL, a non-commercial research
term and MIT all install identically.

**An allowlist, not a denylist.** The failure being prevented is an *unfamiliar*
licence sliding in, and a denylist only catches the restrictive terms somebody
already thought of. The cost is that a new permissive licence fails the suite
until it is added here, which is the right way round: adding it is a one-line
decision somebody makes deliberately.

**Read from installed metadata**, so it describes what is actually in the
environment rather than what the lockfile implies. The one thing that costs:
optional extras are not installed, so `sentence-transformers` and `spacy` are
checked only when something has pulled them in. The audit names both.

The image and model-weight findings in `docs/licences.md` are not asserted here.
They were read off artefacts that a unit test has no business starting, and a
test that shelled out to Docker would be skipped everywhere it mattered.
"""

from __future__ import annotations

import importlib.metadata as md
import re

import pytest

#: Licence identifiers that are fine for this project, commercially and
#: otherwise. Every entry is permissive or file-level copyleft; see
#: `docs/licences.md` for the reasoning on the ones that are not a bare MIT.
ALLOWED = {
    "0bsd",
    "apache-2.0",
    "apache software license",
    "bsd license",
    "bsd-2-clause",
    "bsd-3-clause",
    "cc0-1.0",
    "cnri-python",
    "isc",
    "isc license (iscl)",
    "mit",
    "mit license",
    "mit-0",
    "mit-cmu",
    "mpl-1.1",
    "mpl-2.0",
    "mozilla public license 2.0 (mpl 2.0)",
    "psf-2.0",
    "python software foundation license",
    "zlib",
}

#: Terms that must never appear, whatever else an expression says. A licence
#: that is *only* one of these is caught by the allowlist anyway; this catches
#: the combination — "MIT AND GPL-3.0" is GPL-3.0 in practice, and would
#: otherwise pass by having a familiar-looking half.
NEVER = ("agpl", "gpl-3", "gpl-2", "sspl", "bsl", "non-commercial", "noncommercial")

#: Free-text spellings the ecosystem actually uses, mapped to the identifier
#: they mean. The allowlist judges *substance*; without this it would be a list
#: of every way six projects have written "BSD", and a gate that fails on
#: spelling teaches people to add entries without reading them.
SPELLINGS = {
    "3-clause bsd license": "bsd-3-clause",
    "apache 2.0": "apache-2.0",
    "apache software license": "apache-2.0",
    "bsd": "bsd-3-clause",
    "bsd license": "bsd-3-clause",
    "isc license (iscl)": "isc",
    "mozilla public license 2.0 (mpl 2.0)": "mpl-2.0",
    "psfl": "psf-2.0",
    "python software foundation license": "psf-2.0",
    "the bsd 2-clause license": "bsd-2-clause",
}

#: Expressions that say nothing. `python-dateutil` declares "Dual License",
#: which is true and useless; its classifiers name the two. Falling through to
#: the classifiers is the only way to judge these on substance.
UNINFORMATIVE = {"dual license", "other/proprietary license", ""}

#: Where a package offers a choice, the option this project takes. Recorded
#: here rather than resolved silently, because "which licence are we using"
#: is a decision and this is the only place it is written down in code.
#:
#: `tld` is tri-licensed. MPL-1.1 carries file-level copyleft on modifications
#: to `tld` itself and no obligation on anything importing it, and we do not
#: modify it. See `docs/licences.md`.
CHOICES = {"tld": "mpl-1.1"}

#: Expressions are split on these before each part is checked, so a combination
#: is judged part by part rather than as one unfamiliar string.
SPLIT = re.compile(r"\s+(?:and|or)\s+|;|,|/", re.IGNORECASE)


def installed() -> dict[str, str]:
    """Distribution name → its licence expression, as declared.

    Prefers `License-Expression` (PEP 639) and falls back to the classifiers,
    which is where most of the ecosystem still says it. Deduplicated because a
    venv with both `lib` and `lib64` on the path reports every distribution
    twice.
    """
    found: dict[str, str] = {}
    for dist in md.distributions():
        name = (dist.metadata["Name"] or "").lower()
        if not name or name in found:
            continue
        expression = (
            dist.metadata.get("License-Expression") or dist.metadata.get("License") or ""
        ).strip()
        if expression.lower() in UNINFORMATIVE or "\n" in expression:
            # Some packages paste the whole licence text into `License`. The
            # classifiers are the reliable field in that case.
            classifiers = [
                c.split("::")[-1].strip()
                for c in (dist.metadata.get_all("Classifier") or [])
                if c.startswith("License")
            ]
            expression = "; ".join(classifiers) or expression.splitlines()[0]
        found[name] = expression
    return found


def parts_of(expression: str) -> list[str]:
    """The identifiers an expression resolves to, normalised.

    Parenthesised qualifiers are kept rather than stripped — `ISC License
    (ISCL)` is one spelling, not two identifiers — so `SPELLINGS` is consulted
    on the whole string before it is split at all.
    """
    whole = expression.strip().lower()
    if whole in SPELLINGS:
        return [SPELLINGS[whole]]
    return [
        SPELLINGS.get(part.strip().lower(), part.strip().lower())
        for part in SPLIT.split(expression)
        if part.strip()
    ]


def licence_of(name: str) -> str:
    """The expression to judge this package by, after any choice we have made."""
    if name in CHOICES:
        return CHOICES[name]
    return installed()[name]


def test_the_environment_has_dependencies_to_check() -> None:
    """Guard on the walk. An empty environment would make every assertion below
    vacuously true, which is how a gate stops being one."""
    found = installed()

    assert len(found) > 50, found
    assert "sqlalchemy" in found


@pytest.mark.parametrize("name", sorted(installed()))
def test_every_dependency_declares_a_licence(name: str) -> None:
    """A package with no licence at all is the worst case, not a neutral one —
    no licence means no grant of rights, however permissive the author meant to
    be. `B-11` found all three of Meridian's own packages in this state."""
    assert installed()[name], f"{name} declares no licence"


@pytest.mark.parametrize("name", sorted(installed()))
def test_no_dependency_carries_a_forbidden_term(name: str) -> None:
    """Checked against the whole expression rather than part by part, because
    "MIT AND GPL-3.0" is GPL-3.0 in practice and would otherwise pass on the
    strength of its familiar half."""
    expression = licence_of(name).lower()

    hit = [term for term in NEVER if term in expression]
    assert not hit, (
        f"{name} is licensed {licence_of(name)!r}, which contains {hit}. "
        f"See docs/licences.md — this needs a decision, not an allowlist entry"
    )


@pytest.mark.parametrize("name", sorted(installed()))
def test_every_licence_is_one_already_agreed_to(name: str) -> None:
    """The allowlist. A new licence fails until somebody looks at it and adds
    it, which is the point rather than the friction."""
    expression = licence_of(name)
    unknown = [part for part in parts_of(expression) if part not in ALLOWED]

    assert not unknown, (
        f"{name} is licensed {expression!r}; {unknown} is not in the allowlist. "
        f"Read it, decide, then add it to ALLOWED and to docs/licences.md"
    )


def test_meridians_own_packages_declare_their_licence() -> None:
    """They did not until `B-11`, which made three of the project's own
    distributions the only unlicensed things in the environment."""
    found = installed()

    for name in ("meridian-core", "meridian-api", "meridian-worker"):
        assert found.get(name, "").lower() == "mit", f"{name}: {found.get(name)!r}"


def test_the_allowlist_has_not_quietly_swallowed_a_forbidden_term() -> None:
    """The lists must not contradict each other. An `ALLOWED` or `SPELLINGS`
    entry containing a `NEVER` term would silently win, because the allowlist
    check is the one with the friendlier failure — and a restrictive licence
    arriving under a familiar spelling is exactly the hole worth closing."""
    contradictions = sorted(
        entry for entry in ALLOWED | set(SPELLINGS.values()) if any(term in entry for term in NEVER)
    )

    assert not contradictions, f"ALLOWED/SPELLINGS overlap NEVER: {contradictions}"


def test_every_choice_we_made_is_one_the_package_actually_offers() -> None:
    """`CHOICES` resolves a multi-licence package to the option we take. If the
    package relicenses, the option may no longer be on offer — and silently
    keeping our answer would be asserting a grant nobody made."""
    for name, chosen in CHOICES.items():
        declared = installed().get(name)
        assert declared, f"{name} is in CHOICES and not installed"
        assert chosen in declared.lower(), (
            f"we claim {name} under {chosen!r}, but it now declares {declared!r}"
        )
