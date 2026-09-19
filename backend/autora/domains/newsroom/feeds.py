"""Reading feeds (T-501): RSS 2.0, RSS 1.0 (RDF) and Atom, with the standard library only.

Feeds are untrusted input. Documents that declare a DTD or entities are refused (entity
expansion is the classic XML bomb); everything else is plain ElementTree parsing. An entry
needs a link to be kept: a source item points at a page ``fetch_url`` can snapshot later.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

_ATOM = "{http://www.w3.org/2005/Atom}"
_RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
_RSS1 = "{http://purl.org/rss/1.0/}"
_DC = "{http://purl.org/dc/elements/1.1/}"
_CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
SUMMARY_LIMIT = 1000


class FeedError(Exception):
    """Not a feed we can read. Not worth retrying until the source changes."""

    retryable = False


@dataclass(frozen=True)
class FeedEntry:
    external_id: str
    url: str
    title: str
    summary: str | None
    published_at: datetime | None


def parse_feed(body: bytes) -> list[FeedEntry]:
    head = body[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in body.upper():
        raise FeedError("feeds with a DTD or entity declarations are not accepted")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise FeedError(f"not well-formed XML: {exc}") from None

    if root.tag == "rss":
        channel = root.find("channel")
        items = channel.findall("item") if channel is not None else []
        entries = [_rss_item(item, "") for item in items]
    elif root.tag == f"{_RDF}RDF":
        entries = [_rss_item(item, _RSS1) for item in root.findall(f"{_RSS1}item")]
    elif root.tag == f"{_ATOM}feed":
        entries = [_atom_entry(entry) for entry in root.findall(f"{_ATOM}entry")]
    else:
        raise FeedError(f"unknown feed format: <{root.tag}>")
    return [e for e in entries if e is not None]


def _text(element: ET.Element | None) -> str:
    return (element.text or "").strip() if element is not None else ""


def _clean(text: str) -> str:
    """Feed summaries are often HTML: strip tags, unescape, collapse whitespace."""
    return _SPACE.sub(" ", html.unescape(_TAGS.sub(" ", text))).strip()


def _date(value: str) -> datetime | None:
    if not value:
        return None
    for parse in (parsedate_to_datetime, datetime.fromisoformat):
        try:
            parsed = parse(
                value.replace("Z", "+00:00") if parse is datetime.fromisoformat else value
            )
        except (TypeError, ValueError, IndexError):
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _entry(
    external_id: str, url: str, title: str, summary: str, published: str
) -> FeedEntry | None:
    if not url.startswith(("http://", "https://")):
        return None
    summary = _clean(summary)
    return FeedEntry(
        external_id=(external_id or url)[:500],
        url=url,
        title=_clean(title)[:500] or url,
        summary=summary[:SUMMARY_LIMIT] or None,
        published_at=_date(published),
    )


def _rss_item(item: ET.Element, ns: str) -> FeedEntry | None:
    link = _text(item.find(f"{ns}link"))
    guid = item.find("guid")
    # a guid that is not a permalink is still a stable id; a permalink guid can stand in for link
    if not link and guid is not None and guid.get("isPermaLink", "true") != "false":
        link = _text(guid)
    about = item.get(f"{_RDF}about", "")
    return _entry(
        external_id=_text(guid) or about or link,
        url=link or about,
        title=_text(item.find(f"{ns}title")),
        summary=_text(item.find(f"{ns}description")) or _text(item.find(f"{_CONTENT}encoded")),
        published=_text(item.find("pubDate")) or _text(item.find(f"{_DC}date")),
    )


def _atom_entry(entry: ET.Element) -> FeedEntry | None:
    links = entry.findall(f"{_ATOM}link")
    alternate = [a for a in links if a.get("rel", "alternate") == "alternate"]
    href = next((a.get("href", "") for a in alternate + links if a.get("href")), "")
    return _entry(
        external_id=_text(entry.find(f"{_ATOM}id")),
        url=href,
        title=_text(entry.find(f"{_ATOM}title")),
        summary=_text(entry.find(f"{_ATOM}summary")) or _text(entry.find(f"{_ATOM}content")),
        published=_text(entry.find(f"{_ATOM}published")) or _text(entry.find(f"{_ATOM}updated")),
    )
