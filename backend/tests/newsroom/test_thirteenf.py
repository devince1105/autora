"""D-037: reading 13F filings, comparing two quarters, and ``compare_13f``.

The comparison is checked against Berkshire Hathaway's real filings for the quarters ended
2026-06-30 and 2026-03-31 (``fixtures/sec``); the edge cases against small made-up filings.
"""

import itertools
import uuid
from datetime import date
from pathlib import Path

import pytest

from autora.app import build_embedder
from autora.db.models import Company
from autora.domains.newsroom import thirteenf
from autora.domains.newsroom.agents.researcher import from_a_record
from autora.domains.newsroom.models import Evidence, Source, SourceItem, Story, StoryItem
from autora.domains.newsroom.policy import ACTIONS, RULES
from autora.domains.newsroom.tools import filings as filing_tools
from autora.infra.blobstore import LocalFSBlobStore
from autora.infra.http import FixtureFetcher
from autora.runtime.actor import Actor
from autora.runtime.tools import ToolRegistry
from tests.conftest import running_agent_run, unique_company

SEC = Path(__file__).parent / "fixtures" / "sec"
Q2, Q1 = "0001193125-26-352200", "0001193125-26-226661"
INDEX = f"https://www.sec.gov/Archives/edgar/data/1067983/000119312526352200/{Q2}-index.htm"
AMENDMENT = "0000950123-25-008361"


def read(name: str) -> bytes:
    return (SEC / name).read_bytes()


def berkshire() -> thirteenf.Filer:
    return thirteenf.parse_submissions(read("CIK0001067983.json"))


# --- where things are ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        INDEX,
        "https://www.sec.gov/Archives/edgar/data/1067983/000119312526352200/",
        f"https://www.sec.gov/Archives/edgar/data/0001067983/000119312526352200/{Q2}.txt",
    ],
)
def test_any_address_inside_a_filing_names_it(url):
    ref = thirteenf.filing_ref(url)
    assert (ref.cik, ref.accession) == ("1067983", Q2)
    assert ref.text_url.endswith(f"/1067983/000119312526352200/{Q2}.txt")
    assert ref.index_url == INDEX


def test_other_addresses_are_refused_with_an_example():
    with pytest.raises(thirteenf.FilingError, match="Archives/edgar/data"):
        thirteenf.filing_ref("https://www.berkshirehathaway.com/letters/2026.pdf")


def test_the_previous_quarter_is_the_last_original_filing_before():
    filer = berkshire()
    now = next(f for f in filer.filings if f.accession == Q2)
    [before] = thirteenf.previous_quarter(now, [filer])
    assert (before.accession, before.period) == (Q1, date(2026, 3, 31))

    # 2025-03-31 was filed, then amended: the original is the quarter, not the amendment
    q2_2025 = next(f for f in filer.filings if f.period == date(2025, 6, 30))
    [original] = thirteenf.previous_quarter(q2_2025, [filer])
    assert original.form == "13F-HR" and original.accession != AMENDMENT


def _listed(cik, accession, period, filed, form="13F-HR"):
    return thirteenf.Listed(
        cik, accession, form, date.fromisoformat(filed), date.fromisoformat(period)
    )


def test_a_manager_that_moved_filers_has_both_filings_in_its_previous_quarter():
    """Pershing Square, 2026: Capital Management filed a notice for Q2, Inc. files for both."""
    inc = thirteenf.Filer("2026053", "PERSHING SQUARE INC.", (
        _listed("2026053", "0000000001-26-000002", "2026-06-30", "2026-08-14"),
        _listed("2026053", "0000000001-26-000001", "2026-03-31", "2026-05-15"),
    ))  # fmt: skip
    capital = thirteenf.Filer("1336528", "Pershing Square Capital Management, L.P.", (
        _listed("1336528", "0000000002-26-000002", "2026-06-30", "2026-08-14", form="13F-NT"),
        _listed("1336528", "0000000002-26-000001", "2026-03-31", "2026-05-15"),
        _listed("1336528", "0000000002-25-000009", "2025-12-31", "2026-02-17"),
    ))  # fmt: skip
    now = inc.filings[0]
    before = thirteenf.previous_quarter(now, [inc, capital])
    assert [(b.cik, str(b.period)) for b in before] == [
        ("1336528", "2026-03-31"),
        ("2026053", "2026-03-31"),
    ]
    assert thirteenf.previous_quarter(now, [inc]) == [inc.filings[1]], "without the predecessor"


# --- one filing ---------------------------------------------------------------------------------


def test_berkshire_s_rows_add_up_to_its_own_total():
    holdings = thirteenf.parse_filing(read(f"{Q2}.txt"))
    assert holdings.manager == "Berkshire Hathaway Inc"
    assert holdings.report_type == "13F HOLDINGS REPORT"
    assert len(holdings.positions) == 29, "89 rows, one per subsidiary; 29 positions"
    assert holdings.total_value == 299_253_556_246, "the cover page's tableValueTotal"


def _filing(rows: str, report_type: str = "13F HOLDINGS REPORT") -> bytes:
    cover = (
        "<edgarSubmission><formData><coverPage><filingManager><name>Test Capital</name>"
        f"</filingManager><reportType>{report_type}</reportType></coverPage></formData>"
        "</edgarSubmission>"
    )
    table = (
        f'<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">'
        f"{rows}</informationTable>"
        if rows
        else ""
    )
    blocks = f"<XML>\n{cover}\n</XML>" + (f"<XML>\n{table}\n</XML>" if table else "")
    return f"<SEC-DOCUMENT>{blocks}</SEC-DOCUMENT>".encode()


def _row(name, cusip, amount, value, kind="SH", put_call=""):
    option = f"<putCall>{put_call}</putCall>" if put_call else ""
    return (
        f"<infoTable><nameOfIssuer>{name}</nameOfIssuer><titleOfClass>COM</titleOfClass>"
        f"<cusip>{cusip}</cusip><value>{value}</value><shrsOrPrnAmt><sshPrnamt>{amount}"
        f"</sshPrnamt><sshPrnamtType>{kind}</sshPrnamtType></shrsOrPrnAmt>{option}</infoTable>"
    )


def test_shares_options_and_principal_are_separate_positions():
    holdings = thirteenf.parse_filing(
        _filing(
            _row("ACME", "000000001", 100, 1000)
            + _row("ACME", "000000001", 50, 500)
            + _row("ACME", "000000001", 10, 70, put_call="Call")
            + _row("ACME", "000000001", 2000, 1900, kind="PRN")
        )
    )
    shares = holdings.positions[("000000001", "", "SH")]
    assert (shares.amount, shares.value) == (150, 1500), "two rows, one position"
    assert holdings.positions[("000000001", "CALL", "SH")].label.endswith(
        "COM CALL, CUSIP 000000001)"
    )
    assert len(holdings.positions) == 3


def test_values_filed_in_thousands_are_read_as_thousands():
    """Duquesne's 2026-06-30 filing: 300,200 Skeena shares valued at "8,003"."""
    holdings = thirteenf.parse_filing(
        _filing(_row("SKEENA", "000000001", 300200, 8003) + _row("OTHER", "000000002", 1000, 50))
    )
    assert holdings.in_thousands
    assert holdings.positions[("000000001", "", "SH")].value == 8_003_000
    dollars = thirteenf.parse_filing(_filing(_row("SKEENA", "000000001", 300200, 8_003_000)))
    assert not dollars.in_thousands


def test_two_filers_holdings_add_up():
    a = thirteenf.parse_filing(_filing(_row("HHH", "000000001", 9_000_000, 600)))
    b = thirteenf.parse_filing(
        _filing(_row("HHH", "000000001", 18_852_064, 1200) + _row("GOOG", "000000002", 10, 2))
    )
    combined = thirteenf.combine([a, b])
    assert combined.positions[("000000001", "", "SH")].amount == 27_852_064
    assert len(combined.positions) == 2


def test_a_notice_has_no_holdings():
    assert thirteenf.parse_filing(_filing("", report_type="13F NOTICE")).positions == {}


def test_xml_that_declares_entities_is_refused():
    bomb = b'<XML><!DOCTYPE x [<!ENTITY a "aaaa">]><edgarSubmission/></XML>'
    with pytest.raises(thirteenf.FilingError, match="DTD"):
        thirteenf.parse_filing(bomb)


# --- two quarters -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def comparison() -> str:
    filer = berkshire()
    now = next(f for f in filer.filings if f.accession == Q2)
    before = next(f for f in filer.filings if f.accession == Q1)
    return thirteenf.render(
        filer=filer.name,
        cik=filer.cik,
        now=now,
        before=[(filer.name, before)],
        holdings_now=thirteenf.parse_filing(read(f"{Q2}.txt")),
        holdings_before=thirteenf.parse_filing(read(f"{Q1}.txt")),
    )


def test_it_says_who_computed_it_and_from_which_filings(comparison):
    assert "Computed by this newsroom's code from the 2 SEC filings below" in comparison
    assert "adds up the filings" not in comparison and "thousands" not in comparison
    assert "the text is not SEC's own" in comparison
    assert INDEX in comparison and Q1 in comparison
    assert "Reported value this quarter: US$299,253,556,246 in 29 positions." in comparison


def test_what_was_bought_added_to_cut_and_sold(comparison):
    assert (
        "- ALPHABET INC (CAP STK CL A, CUSIP 02079K305): 78,791,167 shares, up from 54,249,798 "
        "(+24,541,369 shares, +45.2%); value US$28,157,599,351, 9.4% of reported value."
    ) in comparison
    assert (
        "- BANK OF AMER CORP (COM, CUSIP 060505104): 483,394,015 shares, down from 513,624,165 "
        "(-30,230,150 shares, -5.9%)"
    ) in comparison
    assert (
        "- CONSTELLATION BRANDS INC (CL A, CUSIP 21036P108): all 632,890 shares sold" in comparison
    )
    assert (
        "D R HORTON INC (COM, CUSIP 23331A109): 3,564 shares, value US$580,504, under 0.1%"
        in comparison
    )
    assert (
        "- APPLE INC (COM, CUSIP 037833100): 227,917,808 shares; value US$65,950,296,923"
        in comparison
    )


def test_every_position_is_in_exactly_one_group(comparison):
    headers = [line for line in comparison.splitlines() if line.endswith("):")]
    counts = [int(h.rsplit("(", 1)[1].rstrip("):")) for h in headers]
    assert [h.split(" (")[0] for h in headers] == [
        "New positions", "Sold out", "Increased", "Decreased", "Unchanged",
    ]  # fmt: skip
    assert counts == [1, 1, 7, 6, 15]
    assert sum(counts) - counts[1] == 29, "every position held now, plus the one sold"


# --- the tool -----------------------------------------------------------------------------------


def _routes() -> dict[str, str]:
    base = "https://www.sec.gov/Archives/edgar/data/1067983"
    return {
        thirteenf.submissions_url("1067983"): "CIK0001067983.json",
        f"{base}/{Q2.replace('-', '')}/{Q2}.txt": f"{Q2}.txt",
        f"{base}/{Q1.replace('-', '')}/{Q1}.txt": f"{Q1}.txt",
    }


@pytest.fixture
async def desk(committed, tmp_path):
    async with committed() as session:
        run = await running_agent_run(session, "research")
        company = await session.get(Company, run.company_id)
        await session.commit()
    registry = ToolRegistry(committed)
    filing_tools.register(
        registry, FixtureFetcher(SEC, _routes()), LocalFSBlobStore(tmp_path), build_embedder(None)
    )
    steps = itertools.count(1)

    async def call(args):
        return await registry.invoke(
            "compare_13f",
            args,
            company_id=company.id,
            actor=Actor.system("test"),
            tool_call_id=f"call_{uuid.uuid4().hex[:8]}",
            run_id=run.id,
            task_id=run.task_id,
            agent_id=run.agent_id,
            step_seq=next(steps),
        )

    return {"company": company, "call": call, "committed": committed}


async def test_the_tool_captures_the_comparison_as_evidence_under_the_filing(desk):
    result = await desk["call"]({"filing_url": INDEX})
    assert result.ok, result.message
    out = result.output
    assert out["counts"] == {
        "new": 1,
        "sold_out": 1,
        "increased": 7,
        "decreased": 6,
        "unchanged": 15,
    }
    assert (out["period"], out["previous_period"]) == ("2026-06-30", "2026-03-31")
    assert [p.type for p in result.produced] == ["evidence"]

    async with desk["committed"]() as session:
        evidence = await session.get(Evidence, uuid.UUID(out["evidence_id"]))
        assert evidence.url == INDEX, "what a reader clicks, and the URL the story's item has"
        assert "+24,541,369 shares, +45.2%" in evidence.extracted_text

    again = await desk["call"]({"filing_url": INDEX})
    assert again.output["evidence_id"] == out["evidence_id"] and again.output["reused"] is True
    assert out["previous_filers"] == ["BERKSHIRE HATHAWAY INC"]


async def test_an_amendment_is_not_compared(desk):
    url = f"https://www.sec.gov/Archives/edgar/data/1067983/{AMENDMENT.replace('-', '')}/"
    result = await desk["call"]({"filing_url": url})
    assert not result.ok and "13F-HR/A" in result.message and "fetch_url" in result.message


async def test_the_first_filing_has_nothing_to_compare_with(desk):
    oldest = min(
        (f for f in berkshire().filings if f.form == "13F-HR"), key=lambda f: f.period
    ).accession
    url = f"https://www.sec.gov/Archives/edgar/data/1067983/{oldest.replace('-', '')}/"
    result = await desk["call"]({"filing_url": url})
    assert not result.ok and "no earlier 13F-HR" in result.message


def test_only_the_researcher_may_compare():
    assert ACTIONS["compare_13f"] == "write"
    assert {(r.role, r.outcome) for r in RULES if r.action == "compare_13f"} == {
        ("researcher", "allow")
    }


# --- a story from a filing needs one source ------------------------------------------------------


async def test_a_story_from_a_primary_source_is_a_record(db_session):
    company = await unique_company(db_session, "record")
    filings = Source(company_id=company.id, name="13F", kind="rss", url="https://s/13f",
                     config={"primary": True}, status="paused")  # fmt: skip
    news = Source(company_id=company.id, name="n", kind="rss", url="https://s/n", status="paused")
    db_session.add_all([filings, news])
    await db_session.flush()
    stories = []
    for source in (filings, news):
        item = SourceItem(
            company_id=company.id,
            source_id=source.id,
            external_id=source.name,
            url=f"https://s/{source.name}/1",
            title="t",
            content_hash=source.name,
        )
        story = Story(company_id=company.id, title=source.name)
        db_session.add_all([item, story])
        await db_session.flush()
        db_session.add(StoryItem(story_id=story.id, source_item_id=item.id, company_id=company.id))
        stories.append(story)
    await db_session.flush()
    assert await from_a_record(db_session, stories[0].id) is True
    assert await from_a_record(db_session, stories[1].id) is False
    assert await from_a_record(db_session, None) is False
