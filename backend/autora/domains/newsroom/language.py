"""Is this version actually written in the language it claims (D-002, D-025 era)?

``check_draft`` already makes sure both languages are present and cite the same claims. Nothing
checked that the English one was English. A model under pressure — or a stub — will happily put
the Chinese headline in the English version, and the site then shows a reader a page in a
language they did not ask for, with a straight face.

This is a script check, not a language check: it counts Han characters. That is enough for the
two languages this company publishes in (D-002), and it is honest about what it cannot do — it
would not notice French sold as English.

**It checks the title and the summary, not the body.** Those are the writer's own words in every
case, and a headline in the wrong language is exactly the failure that was seen. A body is
different: an article may quote its sources at length in their language, and a story built on
English evidence can legitimately carry more English than Chinese. Drawing the line at the
title keeps a rule that is always true rather than one that is right most of the time and
insulting the rest.

A version that breaks it is refused at ``write_draft``: cheaper to write again than to publish
and find out from a reader.
"""

from __future__ import annotations

import re

HAN = re.compile("[\\u3400-\\u4dbf\\u4e00-\\u9fff]")
LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)

HAN_LANGS = ("zh", "yue", "wuu")
"""Languages written in Han characters. Anything else is expected not to be."""

MIN_HAN_IN_HAN_LANG = 0.25
"""How much of a Chinese title and summary must be Han. A quarter, not a half: a headline like
「Lumen City 微電網啟用：4 MWh 儲能」 is ordinary Chinese writing and is barely a third Han."""
MAX_HAN_IN_OTHER = 0.4
"""How much Han an English title may carry: a place, a person, a quoted term — not a headline."""


def han_share(text: str) -> float:
    """Han characters as a share of the letters in ``text``. 0 when it has no letters."""
    letters = LETTERS.findall(text)
    if not letters:
        return 0.0
    return sum(1 for character in letters if HAN.match(character)) / len(letters)


def wants_han(lang: str) -> bool:
    return lang.split("-")[0].lower() in HAN_LANGS


def script_problems(lang: str, *, title: str, summary: str | None = None) -> list[str]:
    """What is wrong with the script this version's own words are written in (empty: nothing).

    ``summary`` joins the title when there is one: both are the writer's, both are read before
    anybody decides to open the article.
    """
    own_words = " ".join(part for part in (title, summary or "") if part)
    share = han_share(own_words)
    if wants_han(lang):
        if share < MIN_HAN_IN_HAN_LANG:
            return [
                f"{lang}: the title and summary are not in Chinese "
                f"({share:.0%} of their letters are Han)"
            ]
        return []
    if share > MAX_HAN_IN_OTHER:
        return [
            f"{lang}: the title and summary are in Chinese, not {lang} — write them in {lang} "
            "rather than repeating the other version's"
        ]
    return []
