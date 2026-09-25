"""D-051: public officials' transaction reports — found in OGE's index, transcribed page by page,
checked by a person, and only then on the stock pages."""

import io
from datetime import date

import httpx
import pytest
from pypdf import PdfWriter
from sqlalchemy import func, select

from autora.db.models import Approval
from autora.domains.newsroom import official_trades as ot
from autora.domains.newsroom.holdings import STOCKS
from autora.domains.newsroom.models import OfficialReport, OfficialTrade
from autora.runtime.actor import Actor
from autora.runtime.approvals import ApprovalService
from tests.conftest import unique_company

REPORT = (
    "https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/X/$FILE/Donald-J-Trump-09.8.2026-278T.pdf"
)
OLDER = (
    "https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/Y/$FILE/Donald-J-Trump-08.12.2026-278T.pdf"
)
INDEX = {
    "data": [
        {
            "name": "Trump, Donald J",
            "type": f"<a href='{REPORT}'>278 Transaction</a>",
            "docDate": "2026-09-22T04:18:47",
        },
        {  # somebody else
            "name": "Hegseth, Pete",
            "type": "<a href='https://x.test/h.pdf'>278 Transaction</a>",
            "docDate": "2026-09-21T00:00:00",
        },
        {  # no PDF to read
            "name": "Trump, Donald J",
            "type": "Presidential Candidate (<a href='https://x.test/request'>Request</a>)",
            "docDate": "2024-08-23T00:00:00",
        },
        {
            "name": "Trump, Donald J",
            "type": f"<a href='{OLDER}'>278 Transaction</a>",
            "docDate": "2026-08-22T00:00:00",
        },
    ]
}


def pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def row(number, description, amount="$1,001 - $15,000", kind="purchase", day="2/5/2026"):
    return {
        "number": number,
        "description": description,
        "type": kind,
        "date": day,
        "late": "yes",
        "amount": amount,
    }


class Fetch:
    def __init__(self, pages=2):
        self.pages = pages
        self.asked: list[str] = []

    async def json(self, url, params):
        assert url == ot.OGE_INDEX and params["length"] == ot.RECENT
        return INDEX

    async def pdf(self, url):
        self.asked.append(url)
        return pdf(self.pages)


class Transcriber:
    model = "gemini-test"

    def __init__(self, pages: list[list[dict]], fail: bool = False):
        self.pages = pages
        self.calls = 0
        self.fail = fail

    async def transcribe(self, page_pdf):
        assert page_pdf.startswith(b"%PDF")  # one page, as a PDF
        if self.fail:
            raise ot.TranscribeError("HTTP 503 from the model")
        rows = self.pages[self.calls % len(self.pages)]
        self.calls += 1
        return rows


def test_the_index_gives_the_figures_transaction_reports_with_a_pdf():
    listed = ot.parse_index(INDEX)
    assert [(item.person, item.url, item.received_on) for item in listed] == [
        ("川普", REPORT, date(2026, 9, 22)),
        ("川普", OLDER, date(2026, 8, 22)),
    ]


@pytest.mark.parametrize(
    ("text", "parsed"),
    [
        ("$15,001 - $50,000", (15001, 50000, True)),
        ("$1,000,001 - $5,000,000", (1000001, 5000000, True)),
        ("Over $50,000,000", (50000001, None, True)),
        ("$15,001 - $50,00", (None, None, False)),  # misread: not one of the form's ranges
        ("$1,001 - $50,000", (None, None, False)),
        ("", (None, None, False)),
    ],
)
def test_an_amount_is_one_of_the_forms_ranges_or_nothing(text, parsed):
    assert ot.parse_amount(text) == parsed


def test_a_row_is_checked_and_a_stock_found_by_the_ticker_after_its_name():
    stock = ot.parse_row(row(65, "BANK OF AMERICA CORPORATION - BAC", "$15,001 - $50,000"))
    assert (stock.ticker, stock.kind, stock.traded_on, stock.late) == (
        "BAC",
        "purchase",
        date(2026, 2, 5),
        True,
    )
    assert (stock.amount_min, stock.amount_max, stock.readable) == (15001, 50000, True)
    bond = ot.parse_row(row(34, "GEORGIA ST PORT AUTH REV B/E 5.00 % Due Jul 1, 2026"))
    assert bond.ticker is None
    blurred = ot.parse_row(row(7, "NVIDIA CORPORATION - NVDA", "$1,0O1 - $15,000", day="2/3l/2026"))
    assert (blurred.ticker, blurred.readable, blurred.amount_text) == (
        "NVDA",
        False,
        "$1,0O1 - $15,000",
    )
    assert ot.parse_row(row(8, "X", kind="Sale (Partial)")).kind == "partial sale"
    assert ot.parse_row({"number": "", "description": "header"}) is None


@pytest.mark.parametrize(
    ("description", "ticker"),
    [
        ("AMAZON.COM INC", "AMZN"),  # named by issuer only, as most reports do
        ("AMAZON INC", "AMZN"),  # the same report spells it three ways
        ("ALPHABET INC CL A", "GOOGL"),
        ("NVIDIA CORPORATION", "NVDA"),
        ("TAIWAN SEMICONDUCTOR MFG CO LTD SPONSORED ADS", "TSM"),
        ("APPLE INC SENIOR NOTES DUE 05/11/2029 04.000%", None),  # its debt, not its stock
        ("BANK OF AMERICA CORPORATION - BAC", "BAC"),
        ("PALANTIR TECHNOLOGIES INC CL A", None),  # no page on the site: not tagged
        ("METAL SKY STAR ACQUISITION", None),
    ],
)
def test_a_stock_the_site_follows_is_found_by_its_issuer_s_name(description, ticker):
    assert ot.ticker_of(description) == ticker


def test_a_report_is_split_into_single_pages():
    pages = ot.split_pages(pdf(3))
    assert len(pages) == 3 and all(p.startswith(b"%PDF") for p in pages)


@pytest.fixture
async def company(db_session):
    return await unique_company(db_session)


def approvals() -> ApprovalService:
    service = ApprovalService(None)  # a standalone approval needs no task manager
    service.on_decided(ot.APPROVAL_ACTION, ot.on_report_decided)
    return service


async def test_a_new_report_is_transcribed_stored_pending_and_put_to_a_person(db_session, company):
    fetch = Fetch(pages=2)
    transcriber = Transcriber(
        [
            [],  # the cover page has no table
            [
                row(65, "BANK OF AMERICA CORPORATION - BAC", "$15,001 - $50,000"),
                row(66, "NVIDIA CORPORATION - NVDA", "$250,001 - $500,000", kind="sale"),
                row(67, "GEORGIA ST PORT AUTH REV B/E 5.00 % Due Jul 1, 2026"),
            ],
        ]
    )
    service = approvals()
    stored = await ot.refresh_official_trades(db_session, company.id, fetch, transcriber, service)
    assert stored == 2 and fetch.asked == [REPORT, OLDER]  # newest first
    reports = (
        await db_session.scalars(
            select(OfficialReport)
            .where(OfficialReport.company_id == company.id)
            .order_by(OfficialReport.received_on.desc())
        )
    ).all()
    newest = reports[0]
    assert (newest.status, newest.pages, newest.model) == ("pending", 2, "gemini-test")
    trades = (
        await db_session.scalars(
            select(OfficialTrade)
            .where(OfficialTrade.report_id == newest.id)
            .order_by(OfficialTrade.number)
        )
    ).all()
    assert [(t.page, t.number, t.ticker, t.kind) for t in trades] == [
        (2, 65, "BAC", "purchase"),
        (2, 66, "NVDA", "sale"),
        (2, 67, None, "purchase"),
    ]
    approval = await db_session.get(Approval, newest.approval_id)
    assert (approval.kind, approval.action, approval.state) == (
        ot.APPROVAL_KIND,
        ot.APPROVAL_ACTION,
        "PENDING",
    )
    assert "3 筆，其中 2 筆有股票代號" in approval.summary
    assert approval.payload["stocks"][1] == "p.2 #66 NVDA sale 2026-02-05 $250,001 - $500,000"

    # not shown until a person has checked it
    assert await ot.trades_for(db_session, ("NVDA",), company_id=company.id) == []
    await service.decide(db_session, approval.id, outcome="approve", actor=Actor.human("vince"))
    assert newest.status == "approved"
    [nvda] = await ot.trades_for(db_session, STOCKS["NVDA"].tickers, company_id=company.id)
    assert (nvda.person, nvda.kind, nvda.amount_min, nvda.amount_max) == (
        "川普",
        "sale",
        250001,
        500000,
    )
    assert nvda.report_url == f"{REPORT}#page=2"

    # read once: the next run asks for the index, and nothing else
    fetch.asked.clear()
    assert (
        await ot.refresh_official_trades(db_session, company.id, fetch, transcriber, service) == 0
    )
    assert fetch.asked == []


async def test_a_rejected_transcription_never_reaches_a_stock_page(db_session, company):
    service = approvals()
    transcriber = Transcriber([[row(1, "NVIDIA CORPORATION - NVDA")]])
    await ot.refresh_official_trades(db_session, company.id, Fetch(1), transcriber, service)
    for report in (
        await db_session.scalars(
            select(OfficialReport).where(OfficialReport.company_id == company.id)
        )
    ).all():
        await service.decide(
            db_session,
            report.approval_id,
            outcome="reject",
            actor=Actor.human("vince"),
            reason="misread",
        )
    assert await ot.trades_for(db_session, ("NVDA",), company_id=company.id) == []


async def test_a_report_that_cannot_be_read_whole_is_not_stored_and_is_tried_again(
    db_session, company
):
    service = approvals()
    await ot.refresh_official_trades(
        db_session, company.id, Fetch(2), Transcriber([[]], fail=True), service
    )
    count = (
        select(func.count())
        .select_from(OfficialReport)
        .where(OfficialReport.company_id == company.id)
    )
    assert await db_session.scalar(count) == 0
    await ot.refresh_official_trades(db_session, company.id, Fetch(2), Transcriber([[]]), service)
    assert await db_session.scalar(count) == 2


async def test_a_run_stops_at_its_page_budget(db_session, company):
    service = approvals()
    fetch = Fetch(pages=3)
    stored = await ot.refresh_official_trades(
        db_session, company.id, fetch, Transcriber([[]]), service, pages_per_run=4
    )
    assert stored == 1 and fetch.asked == [REPORT, OLDER]  # the second is left for next time
    stored = await ot.refresh_official_trades(
        db_session, company.id, fetch, Transcriber([[]]), service, pages_per_run=4
    )
    assert stored == 1


def test_a_stock_is_found_by_its_us_ticker():
    assert STOCKS["NVDA"].tickers == ("NVDA",)
    assert STOCKS["2330"].tickers == ("TSM",)  # its ADR
    assert STOCKS["2454"].tickers == ()


async def test_the_model_s_failure_is_a_transcribe_error(monkeypatch):
    transcriber = ot.GeminiTranscriber("k", "m", base_url="http://model.test")

    def reply(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "k" and "k" not in str(request.url)
        return httpx.Response(503)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        ot.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(reply), **kwargs),
    )
    with pytest.raises(ot.TranscribeError, match="HTTP 503"):
        await transcriber.transcribe(pdf(1))


async def test_retagging_brings_the_waiting_card_up_to_date(db_session, company, monkeypatch):
    service = approvals()
    transcriber = Transcriber([[row(1, "AMAZON INC", "$5,000,001 - $25,000,000", kind="sale")]])
    monkeypatch.setitem(ot.ISSUERS, "AMZN", ("AMAZON.COM INC",))  # before it knew "AMAZON INC"
    await ot.refresh_official_trades(db_session, company.id, Fetch(1), transcriber, service)
    reports = (
        await db_session.scalars(
            select(OfficialReport).where(OfficialReport.company_id == company.id)
        )
    ).all()
    approval = await db_session.get(Approval, reports[0].approval_id)
    assert approval.payload["stock_rows"] == 0
    monkeypatch.setitem(ot.ISSUERS, "AMZN", ("AMAZON.COM INC", "AMAZON INC"))
    assert await ot.retag(db_session, company.id) == 2  # one row in each of the two reports
    assert approval.payload["stock_rows"] == 1 and "1 筆有股票代號" in approval.summary
    assert approval.payload["stocks"] == ["p.1 #1 AMZN sale 2026-02-05 $5,000,001 - $25,000,000"]
