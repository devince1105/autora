"""Readable text from a fetched page (T-502): what Evidence stores as ``extracted_text``.

Standard library only (``html.parser``); no extraction service or model is involved, so the
same page always gives the same text, and a quote can later be located in it exactly (T-505).

- Scripts, styles, navigation, headers, footers, asides, forms and the like are dropped.
- If the page marks its main content (``<article>`` or ``<main>``), only that is kept; else
  the whole body.
- Block elements become paragraphs (a blank line between them); whitespace inside a
  paragraph is collapsed.
- The charset comes from the Content-Type header, else the page's ``<meta>``, else UTF-8.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

_SKIP = {
    "script", "style", "noscript", "template", "svg", "nav", "header", "footer", "aside",
    "form", "button", "iframe", "select", "canvas",
}  # fmt: skip
_BLOCK = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "section",
    "article", "main", "blockquote", "tr", "pre", "figcaption", "dt", "dd", "table", "hr",
}  # fmt: skip
_VOID = {"br", "hr", "img", "meta", "link", "input", "source", "wbr", "area", "base", "col"}
_MAIN = {"article", "main"}
_SPACE = re.compile(r"[ \t\r\f\v ]+")
_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([A-Za-z0-9_\-]+)""", re.IGNORECASE)
_HEADER_CHARSET = re.compile(r"charset\s*=\s*\"?([A-Za-z0-9_\-]+)", re.IGNORECASE)


@dataclass(frozen=True)
class Extracted:
    title: str | None
    text: str
    language: str | None


def decode(body: bytes, content_type: str) -> str:
    charset = None
    if match := _HEADER_CHARSET.search(content_type or ""):
        charset = match.group(1)
    elif match := _CHARSET.search(body[:4096]):
        charset = match.group(1).decode("ascii")
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:  # an unknown charset name
        return body.decode("utf-8", errors="replace")


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.main_depth = 0
        self.all: list[str] = []
        self.main: list[str] = []
        self.title: list[str] = []
        self.h1: list[str] = []
        self.og_title: str | None = None
        self.language: str | None = None
        self._in_title = False
        self._in_h1 = False

    def _break(self) -> None:
        self.all.append("\n")
        if self.main_depth:
            self.main.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: v or "" for k, v in attrs}
        if tag == "html" and values.get("lang"):
            self.language = values["lang"]
        elif tag == "meta" and values.get("property") == "og:title":
            self.og_title = values.get("content") or None
        elif tag == "title":
            self._in_title = True
        if tag in _VOID:
            if tag in _BLOCK and not self.skip_depth:
                self._break()
            return
        if tag in _SKIP:
            self.skip_depth += 1
            return
        if tag == "h1":
            self._in_h1 = True
        if tag in _MAIN:
            self.main_depth += 1
        if tag in _BLOCK and not self.skip_depth:
            self._break()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _VOID:
            return
        if tag in _SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag == "h1":
            self._in_h1 = False
        if tag in _BLOCK and not self.skip_depth:
            self._break()
        if tag in _MAIN:
            self.main_depth = max(0, self.main_depth - 1)

    def handle_data(self, data: str) -> None:
        # a line break in the HTML source is layout, not structure: blocks make paragraphs
        data = data.replace("\n", " ")
        if self._in_title:
            self.title.append(data)
            return
        if self.skip_depth:
            return
        if self._in_h1:
            self.h1.append(data)
        self.all.append(data)
        if self.main_depth:
            self.main.append(data)


def _paragraphs(chunks: list[str]) -> str:
    lines = (_SPACE.sub(" ", line).strip() for line in "".join(chunks).split("\n"))
    return "\n\n".join(line for line in lines if line)


def extract(body: bytes, content_type: str) -> Extracted:
    text = decode(body, content_type)
    if "html" not in (content_type or "").lower() and not text.lstrip()[:200].lower().startswith(
        ("<!doctype html", "<html")
    ):
        # plain text: keep its paragraphs
        return Extracted(title=None, text=_paragraphs([text]), language=None)
    reader = _Reader()
    reader.feed(text)
    reader.close()
    main = _paragraphs(reader.main)
    title = (
        _SPACE.sub(" ", "".join(reader.title)).strip()
        or reader.og_title
        or _SPACE.sub(" ", "".join(reader.h1)).strip()
    )
    return Extracted(
        title=title or None, text=main or _paragraphs(reader.all), language=reader.language
    )
