"""Screening fetched pages for prompt injection (task P1-23, spec §2.1, §11.8).

Mechanical only, no model. Hidden imperative text is strong evidence; visible imperative
phrasing is weak, because articles about injection quote it. The page is flagged, never
deleted. See docs/features/source-quality.md#injection-screening.
"""

from __future__ import annotations

import dataclasses
import re
from collections import Counter

from lxml import etree
from lxml import html as lxml_html

from meridian_core.logging import get_logger

log = get_logger(__name__)

#: How much text a hidden element must hold before it is worth reporting:
#: `display:none` is the ordinary machinery of every dropdown and modal.
HIDDEN_TEXT_FLOOR = 80

#: Longest evidence snippet kept per finding. Enough to recognise what tripped
#: it, short enough that a page carrying 400KB of hidden text does not put
#: 400KB into a JSONB column.
EVIDENCE_CHARS = 200

MAX_FINDINGS = 50

# --- What "addressed to a model" looks like ---------------------------------
# Two families, kept apart: phrasing aimed at an assistant, and phrasing that names an
# assistant's machinery. Neither is proof; hiddenness is what promotes a finding.
_INSTRUCTION_PATTERNS = (
    r"ignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|preceding|earlier)\s+"
    r"(?:instructions?|prompts?|directions?|rules?)",
    r"disregard\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|preceding)\b",
    r"forget\s+(?:everything|all)\s+(?:you|above|before)",
    r"you\s+are\s+now\s+(?:a|an|in)\b",
    r"new\s+(?:system\s+)?(?:instructions?|prompts?|directives?)\s*[:\-]",
    r"\bas\s+an\s+AI\s+(?:language\s+)?model\b",
    r"(?:system|developer)\s+(?:prompt|message)\s*[:\-]",
    r"\b(?:do\s+not|don't|never)\s+(?:tell|mention|reveal|inform)\s+the\s+user\b",
    r"</?(?:system|assistant|user|im_start|im_end)\b",
    r"\bBEGIN\s+(?:SYSTEM|NEW)\s+(?:PROMPT|INSTRUCTIONS?)\b",
)
_INSTRUCTION_RE = re.compile("|".join(_INSTRUCTION_PATTERNS), re.IGNORECASE)

#: Imperatives aimed at a *tool-holding* agent specifically. A page telling a
#: crawler what to add to a graph is not ambiguous the way "as an AI model" is.
_TOOL_PATTERNS = (
    r"\b(?:add|create|insert|write)\s+(?:an?\s+)?(?:edge|node|entity|fact|tag|claim)\b",
    r"\b(?:visit|fetch|crawl|download|retrieve)\s+(?:the\s+)?(?:following|this)\s+"
    r"(?:url|link|site|address|page)\b",
    r"\b(?:send|post|exfiltrate|upload)\s+(?:the\s+)?(?:contents?|data|results?|credentials?)\s+to\b",
    r"\bexecute\s+(?:the\s+)?following\b",
)
_TOOL_RE = re.compile("|".join(_TOOL_PATTERNS), re.IGNORECASE)

# --- What "hidden from a reader" looks like ---------------------------------

#: Named so a finding says *how* something was hidden. "display:none" and
#: "parked 9999px off the left edge" want different reactions from whoever reads
#: the flag, and a `detail` that quoted the regex would tell them neither.
_STYLE_HIDDEN: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("display:none", re.compile(r"display\s*:\s*none", re.IGNORECASE)),
    ("visibility:hidden", re.compile(r"visibility\s*:\s*hidden", re.IGNORECASE)),
    ("zero opacity", re.compile(r"opacity\s*:\s*0(?:\.0+)?\s*(?:;|$)", re.IGNORECASE)),
    # 0px, 0pt, .1px — anything a reader cannot see.
    (
        "zero font size",
        re.compile(r"font-size\s*:\s*0*(?:\.\d+)?\s*(?:px|pt|em|rem|%)?\s*(?:;|$)", re.IGNORECASE),
    ),
    # Parked far off the canvas. The classic.
    (
        "positioned offscreen",
        re.compile(r"(?:left|top|right|bottom|text-indent)\s*:\s*-\s*\d{3,}", re.IGNORECASE),
    ),
    ("clipped to nothing", re.compile(r"clip\s*:\s*rect\s*\(\s*0", re.IGNORECASE)),
    (
        "zero size",
        re.compile(r"(?:width|height)\s*:\s*0(?:px|pt|em|%)?\s*(?:;|$)", re.IGNORECASE),
    ),
)

#: Colour pairs that render text invisible. Only the unambiguous cases —
#: guessing at contrast ratios from inline styles is a losing game, and the
#: attack that matters uses exact white-on-white rather than a subtle one.
_INVISIBLE_COLOUR = re.compile(
    r"color\s*:\s*(?:#f{3,8}\b|white|rgba?\(\s*255\s*,\s*255\s*,\s*255|transparent"
    r"|rgba\([^)]*,\s*0(?:\.0+)?\s*\))",
    re.IGNORECASE,
)
_WHITE_BACKGROUND = re.compile(
    r"background(?:-color)?\s*:\s*(?:#f{3,8}\b|white|rgba?\(\s*255\s*,\s*255\s*,\s*255)",
    re.IGNORECASE,
)


@dataclasses.dataclass(frozen=True)
class Finding:
    """One thing the screen noticed, and enough of it to recognise.

    ``kind`` is what tripped, ``evidence`` is the snippet, and ``detail`` says how
    it was hidden, when it was.
    """

    kind: str
    evidence: str
    detail: str = ""


@dataclasses.dataclass(frozen=True)
class Screening:
    """What one page's screen found.

    ``suspicious`` is deliberately narrow: *hidden* instructions or tool-directed
    imperatives were found, not merely that the page mentioned an AI.
    """

    findings: tuple[Finding, ...] = ()

    @property
    def kinds(self) -> dict[str, int]:
        return dict(Counter(finding.kind for finding in self.findings))

    @property
    def suspicious(self) -> bool:
        return any(finding.kind in SUSPICIOUS_KINDS for finding in self.findings)

    def as_record(self) -> dict[str, object]:
        """The shape written to `sources.extra["injection"]`.

        Findings are capped and truncated on the way in, so this is bounded
        however hostile the page was.
        """
        return {
            "suspicious": self.suspicious,
            "kinds": self.kinds,
            "findings": [
                {"kind": f.kind, "evidence": f.evidence, "detail": f.detail} for f in self.findings
            ],
        }


#: Kinds that make a page suspicious rather than merely noted. Every one of them
#: requires the content to have been hidden from a reader, or to be addressed at
#: a tool-holding agent — the two cases with no innocent explanation.
SUSPICIOUS_KINDS = frozenset({"hidden_instructions", "comment_instructions", "tool_directive"})


def screen(html_text: str, extracted_text: str = "") -> Screening:
    """Screen one page for injection attempts. Never raises.

    ``html_text`` is the source as fetched — needed because hiddenness is a DOM
    property that extraction has already thrown away. ``extracted_text`` is what
    actually reaches a model, and is screened separately because an instruction
    that survived extraction is the one that would be read.
    """
    findings: list[Finding] = []
    try:
        findings.extend(_hidden_findings(html_text))
    except Exception:
        # A page that will not parse is not a page that gets to skip screening
        # silently; the text-level checks below still run.
        log.warning("could not parse HTML for injection screening", exc_info=True)

    findings.extend(_comment_findings(html_text))
    findings.extend(_text_findings(extracted_text))

    return Screening(findings=tuple(findings[:MAX_FINDINGS]))


# --------------------------------------------------------------------------
# Hidden content
# --------------------------------------------------------------------------


def _hidden_findings(html_text: str) -> list[Finding]:
    """Elements a reader cannot see that nonetheless carry text."""
    if not html_text.strip():
        return []
    try:
        tree = lxml_html.fromstring(html_text)
    except (etree.ParserError, ValueError):
        return []

    findings: list[Finding] = []
    for element in tree.iter():
        if not isinstance(element.tag, str):
            continue  # comments and processing instructions, handled separately
        reason = _hidden_reason(element)
        if reason is None:
            continue
        text = " ".join((element.text_content() or "").split())
        if not text:
            continue

        # Hidden *and* imperative is the case with no innocent explanation.
        # Checked first so a page gets the stronger finding rather than both.
        if _INSTRUCTION_RE.search(text) or _TOOL_RE.search(text):
            findings.append(
                Finding(kind="hidden_instructions", evidence=_snippet(text), detail=reason)
            )
        elif len(text) >= HIDDEN_TEXT_FLOOR:
            findings.append(Finding(kind="hidden_text", evidence=_snippet(text), detail=reason))
    return findings


def _hidden_reason(element: object) -> str | None:
    """Why this element is invisible to a reader, or None if it is not.

    Inline styles and attributes only; stylesheets are not resolved.
    """
    attrib = element.attrib  # type: ignore[attr-defined]
    if "hidden" in attrib:
        return "hidden attribute"
    if attrib.get("aria-hidden", "").lower() == "true":
        return "aria-hidden"
    if element.tag in {"template", "script", "style", "noscript"}:  # type: ignore[attr-defined]
        return None  # not rendered, but also not extracted — no path to a model

    style = attrib.get("style", "")
    if not style:
        return None
    for name, pattern in _STYLE_HIDDEN:
        if pattern.search(style):
            return name
    if _INVISIBLE_COLOUR.search(style) and (
        _WHITE_BACKGROUND.search(style) or _inherits_white(element)
    ):
        return "invisible text colour"
    return None


def _inherits_white(element: object) -> bool:
    """Whether an ancestor sets a white background this element sits on.

    White-on-white is usually written as white text inside a container that was
    already white, not as both on one tag, so checking only the element itself
    would miss the ordinary form of the attack.
    """
    for ancestor in element.iterancestors():  # type: ignore[attr-defined]
        style = ancestor.attrib.get("style", "") if isinstance(ancestor.tag, str) else ""
        if style and _WHITE_BACKGROUND.search(style):
            return True
    # A page with no declared background renders white by default, so white
    # text with no stated background is invisible in the common case.
    return True


# --------------------------------------------------------------------------
# Comments and visible text
# --------------------------------------------------------------------------


def _comment_findings(html_text: str) -> list[Finding]:
    """Instructions in HTML comments.

    Checked in the raw source: a comment has no honest audience, and survives naive
    extractors.
    """
    findings: list[Finding] = []
    for match in re.finditer(r"<!--(.*?)-->", html_text, re.DOTALL):
        body = " ".join(match.group(1).split())
        if _INSTRUCTION_RE.search(body) or _TOOL_RE.search(body):
            findings.append(Finding(kind="comment_instructions", evidence=_snippet(body)))
    return findings


def _text_findings(extracted_text: str) -> list[Finding]:
    """Imperative phrasing in the text that actually reaches a model.

    `visible_instructions` is *not* suspicious on its own; a tool directive is, even
    when plainly visible. See docs/features/source-quality.md#injection-screening.
    """
    if not extracted_text:
        return []
    findings: list[Finding] = []
    for match in _TOOL_RE.finditer(extracted_text):
        findings.append(Finding(kind="tool_directive", evidence=_around(extracted_text, match)))
    for match in _INSTRUCTION_RE.finditer(extracted_text):
        findings.append(
            Finding(kind="visible_instructions", evidence=_around(extracted_text, match))
        )
    return findings


def _around(text: str, match: re.Match[str]) -> str:
    """The match plus a little context, so a reader can judge it."""
    start = max(0, match.start() - 40)
    return _snippet(" ".join(text[start : match.end() + 40].split()))


def _snippet(text: str) -> str:
    return text[:EVIDENCE_CHARS]
