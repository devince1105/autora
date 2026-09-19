"""Splitting evidence text into chunks for retrieval (T-503).

Chunks follow paragraphs: paragraphs are packed together up to ``TARGET`` characters, and a
paragraph longer than ``MAX`` is split at sentence ends (。！？.!?), or hard-cut if it has none.
Every chunk records where it lies in the evidence text (``start``/``end``), so a hit can be
shown in context and quoted from the evidence itself, never from the chunk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TARGET = 900
MAX = 1400
_SENTENCE_END = re.compile(r"(?<=[。！？!?.])\s*")


@dataclass(frozen=True)
class Chunk:
    seq: int
    start: int
    end: int
    text: str


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for match in re.finditer(r"[^\n]+", text):
        start, end = match.span()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if end > start:
            spans.append((start, end))
    return spans


def _split_long(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Sentence-sized pieces of text[start:end], each at most MAX long."""
    pieces: list[tuple[int, int]] = []
    cursor = start
    for match in _SENTENCE_END.finditer(text, start, end):
        cut = match.end()
        if cut <= cursor or cut > end:
            continue
        pieces.append((cursor, cut))
        cursor = cut
    if cursor < end:
        pieces.append((cursor, end))
    out: list[tuple[int, int]] = []
    for a, b in pieces:
        while b - a > MAX:
            out.append((a, a + MAX))
            a += MAX
        if b > a:
            out.append((a, b))
    return out


def chunk_text(text: str) -> list[Chunk]:
    units: list[tuple[int, int]] = []
    for start, end in _paragraph_spans(text):
        units.extend(_split_long(text, start, end) if end - start > MAX else [(start, end)])
    chunks: list[Chunk] = []
    group_start: int | None = None
    group_end = 0
    for start, end in units:
        if group_start is not None and end - group_start > TARGET:
            chunks.append(
                Chunk(len(chunks), group_start, group_end, text[group_start:group_end].strip())
            )
            group_start = None
        if group_start is None:
            group_start = start
        group_end = end
    if group_start is not None:
        chunks.append(
            Chunk(len(chunks), group_start, group_end, text[group_start:group_end].strip())
        )
    return chunks
