"""Finding a quote in an evidence text (T-505, fact-check layer 1: "the quote is a substring of
extracted_text, whitespace normalised").

Only typography may differ between the quote a model wrote and the evidence: runs of whitespace
count as one space, curly quotes as straight ones, dashes as hyphens, and full-width spaces and
non-breaking spaces as spaces. Whitespace next to a CJK character counts as nothing (Chinese is
written without spaces, so a line break or a space there is layout). Everything else (words,
digits, punctuation, case) must match exactly: a quote that says something the page does not is
not found.

The match is reported as offsets into the **original** text, and what gets stored is the
original text between them, never the model's version.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_QUOTE = 4
"""Characters: short enough for a Chinese phrase, long enough to mean something."""
MAX_QUOTE = 500
"""Characters (platform/05 §8: no long reproduction of other people's work)."""

_EQUIVALENT = {
    "“": '"', "”": '"', "„": '"', "″": '"', "＂": '"',
    "‘": "'", "’": "'", "‚": "'", "′": "'", "＇": "'",
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
}  # fmt: skip


@dataclass(frozen=True)
class Located:
    start: int
    end: int
    text: str
    """The original text between start and end: what is stored as the quote."""


def _cjk(char: str) -> bool:
    code = ord(char)
    return (
        0x3000 <= code <= 0x303F  # CJK punctuation
        or 0x3400 <= code <= 0x9FFF  # ideographs
        or 0xF900 <= code <= 0xFAFF
        or 0xFF00 <= code <= 0xFFEF  # full-width forms
    )


def _normalize(text: str) -> tuple[str, list[int]]:
    """Normalized text and, for each of its characters, the index in ``text`` it came from."""
    out: list[str] = []
    origin: list[int] = []
    pending: int | None = None  # index of a whitespace run not yet written
    for index, char in enumerate(text):
        if char.isspace():
            if pending is None:
                pending = index
            continue
        if pending is not None and out and not (_cjk(out[-1]) or _cjk(char)):
            out.append(" ")
            origin.append(pending)
        pending = None
        out.append(_EQUIVALENT.get(char, char))
        origin.append(index)
    return "".join(out), origin


def locate(quote: str, text: str) -> Located | None:
    """The first place ``quote`` appears in ``text`` (up to typography), or None."""
    needle, _ = _normalize(quote)
    if not needle:
        return None
    haystack, origin = _normalize(text)
    at = haystack.find(needle)
    if at < 0:
        return None
    start = origin[at]
    end = origin[at + len(needle) - 1] + 1
    return Located(start=start, end=end, text=text[start:end])


def check_length(quote: str) -> str | None:
    """Why ``quote`` has the wrong length, or None."""
    length = len(_normalize(quote)[0])
    if length < MIN_QUOTE:
        return f"quote is too short ({length} characters; at least {MIN_QUOTE})"
    if length > MAX_QUOTE:
        return (
            f"quote is too long ({length} characters; at most {MAX_QUOTE}): quote the sentence "
            "that supports the claim, not the article"
        )
    return None
