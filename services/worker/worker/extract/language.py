"""A page's language from its text, when the page does not declare one (task `B-153`).

PDFs and many pages carry no `lang` attribute, and a page with no language is scored as
English (`B-53`). py3langid is small, fast and needs only numpy; on pages whose language was
declared it agrees with the declaration about 98% of the time at this confidence. See
docs/features/extraction.md#language.
"""

from __future__ import annotations

import functools

#: Characters of text below which no guess is made: headings and tables mislead it.
MIN_CHARS = 200

#: Share of the opening's non-space characters that must be letters: a statistics table can
#: have 200 letters and still be mostly numbers, and is then read as some small language.
MIN_LETTER_SHARE = 0.6

#: How much of the text is read; the opening is enough and keeps it under a millisecond.
SAMPLE_CHARS = 4000

#: Normalised probability below which the answer is "unknown" rather than a guess.
CONFIDENCE = 0.9

#: Labels that are not a language.
NOT_A_LANGUAGE = frozenset({"zxx", "und"})


@functools.cache
def _identifier():
    from py3langid import langid

    return langid.LanguageIdentifier.from_model_file(langid.MODEL_FILE, norm_probs=True)


def detect_language(text: str | None) -> str | None:
    """The ISO 639-1 code of ``text``'s language, or None when too short or unsure."""
    if not text:
        return None
    sample = text[:SAMPLE_CHARS]
    letters = sum(ch.isalpha() for ch in sample)
    visible = sum(not ch.isspace() for ch in sample)
    if letters < MIN_CHARS or letters < MIN_LETTER_SHARE * visible:
        return None
    language, probability = _identifier().classify(sample)
    if probability < CONFIDENCE or language in NOT_A_LANGUAGE:
        return None
    return language
