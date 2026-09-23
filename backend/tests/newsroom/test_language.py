"""D-002: a version is written in the language it says it is.

The draft rules already ask for both languages and the same claims in each. They did not ask
that the English one be in English — and the simulated writer, given a Chinese story, put the
Chinese headline in it. A real model can do the same on a bad day; a reader then gets a page in
a language they did not choose.
"""

import pytest

from autora.domains.newsroom.language import han_share, script_problems, wants_han

ZH_TITLE = "流明市港區社區微電網啟用"
EN_TITLE = "Lumen City switches on its first microgrid"
ZH_BODY = "微電網串連 1,200 組屋頂太陽能板與 4 MWh 儲能電池，停電時可維持約 3,000 戶供電六小時。"
EN_BODY = "The pilot links 1,200 rooftop solar panels with a 4 MWh battery, the city says."


@pytest.mark.parametrize(
    ("lang", "han"),
    [("zh-TW", True), ("zh", True), ("zh-Hant", True), ("en", False), ("ja", False)],
)
def test_which_languages_are_written_in_han(lang, han):
    assert wants_han(lang) is han


def test_counting_letters_not_punctuation_or_digits():
    assert han_share("") == 0.0
    assert han_share("1,200 — 4 MWh") == 0.0, "numbers and punctuation say nothing about language"
    assert han_share(ZH_BODY) > 0.9
    assert han_share(EN_BODY) == 0.0


def test_each_language_written_in_its_own_script_passes():
    assert script_problems("zh-TW", title=ZH_TITLE, summary="重點數字。") == []
    assert script_problems("en", title=EN_TITLE, summary="The key numbers.") == []


def test_the_other_language_s_headline_copied_across_is_refused():
    """The failure that was actually seen: the Chinese headline in the English version."""
    problems = script_problems("en", title=ZH_TITLE, summary="The key numbers.")
    assert problems and "not en" in problems[0]


def test_a_chinese_name_in_an_english_headline_is_ordinary():
    problems = script_problems("en", title="Inside 港區: a microgrid that runs for six hours")
    assert problems == []


def test_latin_words_and_numbers_in_a_chinese_headline_are_ordinary():
    assert script_problems("zh-TW", title="Lumen City 微電網啟用：4 MWh 儲能") == []


def test_a_chinese_version_written_in_english_is_refused():
    problems = script_problems("zh-TW", title=EN_TITLE, summary="The key numbers.")
    assert problems and "not in Chinese" in problems[0]


def test_the_body_is_not_judged_here():
    """An article may quote its sources at length in their language; the signature says so."""
    assert "body" not in script_problems.__code__.co_varnames
