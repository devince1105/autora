"""Reading SEC Form 13F filings and comparing two quarters (D-037). No model, no database.

A 13F-HR is what an investment manager with over US$100 million files each quarter: every
US-listed long position at the quarter's end, with shares and value. The newsroom's readers want
the difference between two of them — what was bought, sold, added to, cut — and that difference
is arithmetic, so it is done here, in code, where it can be tested, rather than by a model that
might round, misread or invent.

What comes in:

- the **full submission text** of a filing (``<accession>.txt`` in the filing's folder): the
  cover page (``primary_doc.xml``) and the information table, each in an ``<XML>`` block;
- the filer's **submissions index** (``data.sec.gov/submissions/CIK##########.json``), which
  lists its filings with form, accession, filing date and the period each one reports.

What goes out is text: the comparison, one position per line, every figure written out. It is
stored as evidence (``tools/filings.py``) so that claims quote it and fact-check compares the
numbers, like any page. It says at the top that Autora computed it, from which two filings.

Rules, and why:

- a position is (CUSIP, put/call, share-or-principal); rows for the same one are **added up** —
  Berkshire reports Apple once per subsidiary that holds it;
- the previous quarter is the latest **original** 13F-HR for an earlier period; an amendment is
  not a quarter. When a manager's holdings moved to another filer, the previous quarter **adds up**
  what each of them filed for it (Pershing Square: Capital Management's filing and Pershing
  Square Inc.'s for 2026-03-31, reported by Inc. alone from 2026-06-30) — comparing Inc. with
  only its own last filing would show twelve "new" positions that were bought long before;
- values are US dollars — but some filers still report **thousands** (Duquesne's 2026-06-30
  filing values 300,200 Skeena shares at "8,003"). A filing whose shares mostly imply a price
  under a dollar is read as thousands, multiplied by 1,000, and the text says so;
- an amendment (13F-HR/A) or a notice (13F-NT) is **not compared**: a restatement replaces a
  filing, a "new holdings" amendment adds to one, and a notice has no holdings at all — each
  needs its own reading, and a wrong comparison is worse than none;

Feeds are untrusted input: XML that declares a DTD or entities is refused, as in ``feeds.py``.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:0>10}.json"

_FILING_URL = re.compile(r"sec\.gov/Archives/edgar/data/(\d+)/(\d{18})(?:/|$)")
_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_XML_BLOCK = re.compile(rb"<XML>\s*(.*?)\s*</XML>", re.S | re.I)
_UNSAFE = re.compile(rb"<!DOCTYPE|<!ENTITY", re.I)


class FilingError(Exception):
    """Not a filing this module can read or compare. The message says why, for the model."""

    retryable = False


# --- where things are ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilingRef:
    cik: str
    """Without leading zeros, as in the Archives path."""
    accession: str
    """``0001193125-26-352200``."""

    @property
    def folder(self) -> str:
        return f"{ARCHIVES}/{self.cik}/{self.accession.replace('-', '')}"

    @property
    def text_url(self) -> str:
        """The full submission: cover page and information table in one document."""
        return f"{self.folder}/{self.accession}.txt"

    @property
    def index_url(self) -> str:
        """The page a reader opens: SEC's index of the filing."""
        return f"{self.folder}/{self.accession}-index.htm"


def filing_ref(url: str) -> FilingRef:
    """The filing an SEC Archives URL points into (its index page, its folder, any document)."""
    match = _FILING_URL.search(url)
    if match is None:
        raise FilingError(
            f"not an SEC filing URL: {url!r}. Give the filing's address on sec.gov, e.g. "
            "https://www.sec.gov/Archives/edgar/data/1067983/000119312526352200/"
            "0001193125-26-352200-index.htm"
        )
    cik, digits = match.group(1).lstrip("0") or "0", match.group(2)
    return FilingRef(cik=cik, accession=f"{digits[:10]}-{digits[10:12]}-{digits[12:]}")


def submissions_url(cik: str) -> str:
    return SUBMISSIONS.format(cik=cik.lstrip("0") or "0")


# --- the filer's list of filings ----------------------------------------------------------------


@dataclass(frozen=True)
class Listed:
    cik: str
    accession: str
    form: str
    filed: date
    period: date | None

    @property
    def ref(self) -> FilingRef:
        return FilingRef(cik=self.cik, accession=self.accession)


@dataclass(frozen=True)
class Filer:
    cik: str
    name: str
    filings: tuple[Listed, ...]


def parse_submissions(body: bytes) -> Filer:
    """``data.sec.gov/submissions/CIK….json``: who the filer is and its recent filings."""
    try:
        data = json.loads(body)
        recent = data["filings"]["recent"]
        cik = str(int(data["cik"]))
        rows = zip(
            recent["accessionNumber"],
            recent["form"],
            recent["filingDate"],
            recent["reportDate"],
            strict=True,
        )
        filings = tuple(
            Listed(
                cik=cik,
                accession=accession,
                form=form,
                filed=date.fromisoformat(filed),
                period=date.fromisoformat(period) if period else None,
            )
            for accession, form, filed, period in rows
            if _ACCESSION.match(accession)
        )
        return Filer(cik=cik, name=str(data["name"]), filings=filings)
    except (KeyError, TypeError, ValueError) as exc:
        raise FilingError(f"SEC's list of filings could not be read: {exc}") from exc


def previous_quarter(current: Listed, filers: list[Filer]) -> list[Listed]:
    """The filings that make up the quarter before ``current``: the latest earlier period any of
    these filers reported in an original 13F-HR, and each one's latest original for it."""
    if current.period is None:
        return []
    earlier = [
        f
        for filer in filers
        for f in filer.filings
        if f.form == "13F-HR" and f.period is not None and f.period < current.period
    ]
    if not earlier:
        return []
    period = max(f.period for f in earlier if f.period is not None)
    chosen: dict[str, Listed] = {}
    for f in earlier:
        if f.period == period and (f.cik not in chosen or f.filed > chosen[f.cik].filed):
            chosen[f.cik] = f
    return sorted(chosen.values(), key=lambda f: f.cik)


# --- one filing's holdings ----------------------------------------------------------------------


@dataclass
class Position:
    name: str
    title_of_class: str
    cusip: str
    put_call: str
    """``PUT``, ``CALL`` or empty."""
    kind: str
    """``SH`` (shares) or ``PRN`` (principal amount)."""
    amount: int = 0
    value: int = 0

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.cusip, self.put_call, self.kind)

    @property
    def label(self) -> str:
        option = f" {self.put_call}" if self.put_call else ""
        return f"{self.name} ({self.title_of_class}{option}, CUSIP {self.cusip})"


@dataclass
class Holdings:
    manager: str
    report_type: str
    positions: dict[tuple[str, str, str], Position] = field(default_factory=dict)
    in_thousands: bool = False
    """The filing's values were read as thousands of dollars and multiplied by 1,000."""

    @property
    def total_value(self) -> int:
        return sum(p.value for p in self.positions.values())


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, name: str) -> str:
    for child in element.iter():
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _parse_xml(block: bytes) -> ET.Element:
    if _UNSAFE.search(block):
        raise FilingError("the filing declares a DTD or entities; refused")
    try:
        return ET.fromstring(block)
    except ET.ParseError as exc:
        raise FilingError(f"the filing's XML could not be read: {exc}") from exc


def _int(text: str) -> int:
    try:
        return int(Decimal(text.replace(",", "")))
    except ArithmeticError as exc:
        raise FilingError(f"not a number in the information table: {text!r}") from exc


def parse_filing(body: bytes) -> Holdings:
    """A full submission text: the cover page's manager and report type, and the positions."""
    cover: ET.Element | None = None
    table: ET.Element | None = None
    for block in _XML_BLOCK.findall(body):
        root = _parse_xml(block)
        if _local(root.tag) == "edgarSubmission":
            cover = root
        elif _local(root.tag) == "informationTable":
            table = root
    if cover is None:
        raise FilingError("no 13F cover page in this filing")
    holdings = Holdings(
        manager=_child_text(cover, "name"), report_type=_child_text(cover, "reportType")
    )
    if table is None:
        return holdings  # a notice: somebody else reports these holdings
    for row in table:
        if _local(row.tag) != "infoTable":
            continue
        position = Position(
            name=_child_text(row, "nameOfIssuer"),
            title_of_class=_child_text(row, "titleOfClass"),
            cusip=_child_text(row, "cusip").upper(),
            put_call=_child_text(row, "putCall").upper(),
            kind=_child_text(row, "sshPrnamtType").upper() or "SH",
        )
        held = holdings.positions.setdefault(position.key, position)
        held.amount += _int(_child_text(row, "sshPrnamt"))
        held.value += _int(_child_text(row, "value"))
    if _looks_like_thousands(holdings):
        holdings.in_thousands = True
        for held in holdings.positions.values():
            held.value *= 1000
    return holdings


def _looks_like_thousands(holdings: Holdings) -> bool:
    """Most shares worth under a dollar each: the values were written in thousands. (A 13F
    filer whose typical holding really trades under a dollar does not exist; one reporting in
    thousands would need its typical holding over US$1,000 a share to escape this.)"""
    prices = sorted(
        Decimal(p.value) / p.amount
        for p in holdings.positions.values()
        if p.kind == "SH" and not p.put_call and p.amount > 0
    )
    return bool(prices) and prices[len(prices) // 2] < 1


def combine(parts: list[Holdings]) -> Holdings:
    """Several filers' holdings for one quarter, as one manager's: positions added up."""
    combined = Holdings(
        manager=" + ".join(h.manager for h in parts),
        report_type=parts[0].report_type if parts else "",
        in_thousands=any(h.in_thousands for h in parts),
    )
    for part in parts:
        for key, p in part.positions.items():
            held = combined.positions.setdefault(
                key, Position(p.name, p.title_of_class, p.cusip, p.put_call, p.kind)
            )
            held.amount += p.amount
            held.value += p.value
    return combined


# --- two quarters -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    now: Position | None
    before: Position | None

    @property
    def position(self) -> Position:
        held = self.now or self.before
        assert held is not None
        return held

    @property
    def delta(self) -> int:
        return (self.now.amount if self.now else 0) - (self.before.amount if self.before else 0)


@dataclass(frozen=True)
class Comparison:
    new: list[Change]
    sold_out: list[Change]
    increased: list[Change]
    decreased: list[Change]
    unchanged: list[Change]


def compare(now: Holdings, before: Holdings) -> Comparison:
    """Every position, sorted into what happened to it; largest first within each group."""
    groups: dict[str, list[Change]] = {k: [] for k in Comparison.__dataclass_fields__}
    for key in now.positions.keys() | before.positions.keys():
        change = Change(now=now.positions.get(key), before=before.positions.get(key))
        if change.before is None:
            groups["new"].append(change)
        elif change.now is None:
            groups["sold_out"].append(change)
        elif change.delta > 0:
            groups["increased"].append(change)
        elif change.delta < 0:
            groups["decreased"].append(change)
        else:
            groups["unchanged"].append(change)
    for name, changes in groups.items():
        side = "before" if name == "sold_out" else "now"
        changes.sort(key=lambda c: (-getattr(c, side).value, c.position.name))
    return Comparison(**groups)


# --- the text that becomes evidence -------------------------------------------------------------


def _n(value: int) -> str:
    return f"{value:,}"


def _usd(value: int) -> str:
    return f"US${value:,}"


def _pct(part: int, whole: int, *, signed: bool = False) -> str:
    if whole == 0:
        return "n/a"
    value = (Decimal(part) * 100 / Decimal(whole)).quantize(Decimal("0.1"), ROUND_HALF_UP)
    if value == 0 and part != 0:
        return "under 0.1%"  # "0.0%" of something held reads as nothing held
    return f"{value:+}%" if signed else f"{value}%"


def _units(position: Position) -> str:
    return "shares" if position.kind == "SH" else "principal"


def render(
    *,
    filer: str,
    cik: str,
    now: Listed,
    before: list[tuple[str, Listed]],
    holdings_now: Holdings,
    holdings_before: Holdings,
) -> str:
    """The comparison as plain text, one line per position, every number written out.

    ``before``: the previous quarter's filings, each with its filer's name — one, or several
    when the holdings were filed by more than one entity then (see ``previous_quarter``)."""
    diff = compare(holdings_now, holdings_before)
    total_now, total_before = holdings_now.total_value, holdings_before.total_value
    period_before = before[0][1].period
    lines = [
        f"{filer}: 13F holdings, quarter ended {now.period} compared with quarter ended "
        f"{period_before}.",
        f"Computed by this newsroom's code from the {len(before) + 1} SEC filings below. Every "
        "figure is copied "
        "from them or calculated from them (sums of each filing's rows, differences, "
        "percentages); the text is not SEC's own.",
        f"Filer: {filer} (CIK {cik}).",
        f"This quarter: period ended {now.period}, filed {now.filed}, accession {now.accession}, "
        f"{now.ref.index_url}",
    ]
    for name, listed in before:
        lines.append(
            f"Previous quarter: {name} (CIK {listed.cik}), period ended {listed.period}, filed "
            f"{listed.filed}, accession {listed.accession}, {listed.ref.index_url}"
        )
    if len(before) > 1:
        lines.append(
            "The previous quarter adds up the filings of "
            + " and ".join(name for name, _ in before)
            + f": holdings they reported separately are reported by {filer} from this quarter."
        )
    for holdings, when in ((holdings_now, now.period), (holdings_before, period_before)):
        if holdings.in_thousands:
            lines.append(
                f"Values for the quarter ended {when} were filed in thousands of dollars (read "
                "as dollars, most holdings would be worth under a dollar a share); they are shown "
                "here multiplied by 1,000."
            )
    lines += [
        f"Reported value this quarter: {_usd(total_now)} in {len(holdings_now.positions)} "
        f"positions. Previous quarter: {_usd(total_before)} in "
        f"{len(holdings_before.positions)} positions.",
        "What a 13F shows: long positions in US-listed securities at the end of the quarter, "
        "reported up to 45 days later. It does not show short positions, non-US holdings, or when "
        "during the quarter a trade was made.",
    ]

    def section(title: str, changes: list[Change], line) -> None:
        lines.append("")
        lines.append(f"{title} ({len(changes)}):")
        lines.extend(line(c) for c in changes)
        if not changes:
            lines.append("- none")

    section(
        "New positions",
        diff.new,
        lambda c: (
            f"- {c.position.label}: {_n(c.now.amount)} {_units(c.position)}, value "
            f"{_usd(c.now.value)}, {_pct(c.now.value, total_now)} of reported value."
        ),
    )
    section(
        "Sold out",
        diff.sold_out,
        lambda c: (
            f"- {c.position.label}: all {_n(c.before.amount)} {_units(c.position)} sold; value "
            f"at the previous quarter's end {_usd(c.before.value)}."
        ),
    )
    for title, changes, verb in (
        ("Increased", diff.increased, "up"),
        ("Decreased", diff.decreased, "down"),
    ):
        section(
            title,
            changes,
            lambda c, verb=verb: (
                f"- {c.position.label}: {_n(c.now.amount)} {_units(c.position)}, {verb} from "
                f"{_n(c.before.amount)} ({c.delta:+,} {_units(c.position)}, "
                f"{_pct(c.delta, c.before.amount, signed=True)}); value {_usd(c.now.value)}, "
                f"{_pct(c.now.value, total_now)} of reported value."
            ),
        )
    section(
        "Unchanged",
        diff.unchanged,
        lambda c: (
            f"- {c.position.label}: {_n(c.now.amount)} {_units(c.position)}; value "
            f"{_usd(c.now.value)}, {_pct(c.now.value, total_now)} of reported value."
        ),
    )
    return "\n".join(lines) + "\n"
