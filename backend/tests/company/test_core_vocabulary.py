"""The company layer does not know what business it is in (ARCHITECTURE_V2_1 §9, §10).

``lint-imports`` already stops ``company`` importing ``domains``. It cannot stop a *string*,
and one string is exactly how the coupling got in the first time: ``reporting_min`` counted
``"ARTICLE_PUBLISHED"`` events, so the core knew what an article was without importing one.

This checks the vocabulary instead of the imports. It reads the code, not the prose: a comment
or a docstring may explain a rule with the newsroom as its example — that is how the design is
taught — but no identifier and no string the code actually uses may name a domain's things.

The same scan covers the runtime, which knows even less: three kinds of string (a role, a task
name, a tool name) and nothing about what they mean.
"""

import ast
import re
from pathlib import Path

import pytest

import autora

ROOT = Path(autora.__file__).parent
CLEAN = ("company", "realtime")
"""Layers that must name nothing of any domain. The company layer is the one §9 is about."""

BELOW = ("runtime", "db", "infra")
"""Layers that still have leaks, listed one by one below. The list may shrink, never grow."""

VOCABULARY = (
    "article",
    "story",
    "stories",
    "newsroom",
    "byline",
    "editorial",
    "editors",
    "journalist",
    "headline",
    "factcheck",
    "fact_check",
    "source_item",
)
"""Words whose meaning is a newsroom's. A company that moved into SaaS would have to redefine
every one of them, which is the test §3 gives for what belongs in a domain."""

KNOWN_LEAKS = {
    # An approval kind the runtime knows by name. Publishing is a domain's idea of a decision;
    # the runtime should carry the kind the domain registered. Changing it needs a migration
    # and every newsroom caller, so it is written down here rather than quietly tolerated.
    "runtime/events/catalog.py: 'article'",
    "db/models/runtime.py: 'ARTICLE'",
    "db/models/runtime.py: 'article'",
    # The company type taxonomy names the industries a company can be in. Arguably data rather
    # than vocabulary, but it does mean the core ships a list of businesses it knows about.
    "db/models/company.py: 'NEWSROOM'",
    "db/models/company.py: 'newsroom'",
    # A newsroom tuning knob in the core's settings, and the newsroom's name in the user agent
    # the fetcher sends. Both belong to the domain that uses them.
    "infra/settings.py: 'story_match_threshold'",
    "infra/http/__init__.py: 'AutoraNewsroom/0.1 (+https://github.com/vince115/autora)'",
}
"""What §9 found and this repository has not fixed yet (ARCHITECTURE_V2_1 §9, "耦合").

Every entry is a place where a layer below the domains names one. They are listed instead of
ignored so that the count can only go down: adding a new one fails this test, and fixing an
old one fails it too, which is the moment to delete the line.
"""

MIGRATIONS = "migrations"
"""Left out entirely: one database has one history, and a domain's tables are created in it.
A migration is a record of what happened, not code that decides anything."""

_WORD = re.compile(r"[a-z]+")


def _words(text: str) -> set[str]:
    """The words in an identifier or a string: snake_case, camelCase, dots and spaces.

    Whole words only — "history" contains "story" and means nothing of the sort.
    """
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return set(_WORD.findall(spaced.lower()))


def _sources(layer: str) -> list[Path]:
    return sorted(
        p
        for p in (ROOT / layer).rglob("*.py")
        if "__pycache__" not in p.parts and MIGRATIONS not in p.parts
    )


def _docstrings(tree: ast.AST) -> set[int]:
    """id() of every docstring constant, so prose can be skipped without skipping strings."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                out.add(id(body[0].value))
            # a bare string after an assignment is this codebase's field documentation
            for statement in body:
                if (
                    isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str)
                ):
                    out.add(id(statement.value))
    return out


def _names_and_strings(path: Path) -> list[tuple[int, str]]:
    """Every identifier and every string the code uses, with its line. Prose is left out."""
    tree = ast.parse(path.read_text("utf-8"))
    prose = _docstrings(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in prose:
                found.append((node.lineno, node.value))
        elif isinstance(node, ast.Name):
            found.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute):
            found.append((node.lineno, node.attr))
        elif isinstance(node, ast.arg):
            found.append((node.lineno, node.arg))
        elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            found.append((node.lineno, node.name))
        elif isinstance(node, ast.alias):
            found.append((getattr(node, "lineno", 0), node.name))
    return found


def _named(layer: str) -> set[str]:
    """``path: 'text'`` for every place this layer's code names a domain's things."""
    found = set()
    for path in _sources(layer):
        for _, text in _names_and_strings(path):
            if _words(text) & set(VOCABULARY):
                found.add(f"{path.relative_to(ROOT)}: {text!r}")
    return found


@pytest.mark.parametrize("layer", CLEAN)
def test_the_company_layer_does_not_know_what_business_it_is_in(layer):
    assert _named(layer) == set(), (
        "the core named a domain's things:\n  "
        + "\n  ".join(sorted(_named(layer))[:10])
        + "\n\nCore stores the number; the domain defines what it counts (ARCHITECTURE_V2_1 §9)."
    )


def test_what_the_layers_below_still_name_is_the_list_we_know_about():
    """A ratchet, not a pass: this fails when a leak is added *and* when one is fixed."""
    found = set().union(*(_named(layer) for layer in BELOW))
    new = found - KNOWN_LEAKS
    fixed = KNOWN_LEAKS - found
    assert not new, "a new domain word below the company layer:\n  " + "\n  ".join(sorted(new))
    assert not fixed, "these are fixed — delete them from KNOWN_LEAKS:\n  " + "\n  ".join(
        sorted(fixed)
    )


def test_the_scan_would_notice(tmp_path):
    """A test that cannot fail is not a check. This is the shape of the mistake it catches."""
    offender = tmp_path / "reporting_min.py"
    offender.write_text(
        '"""A docstring may mention an article freely."""\nPUBLISHED_EVENT = "ARTICLE_PUBLISHED"\n'
    )
    found = [text for _, text in _names_and_strings(offender) if _words(text) & set(VOCABULARY)]
    assert found == ["ARTICLE_PUBLISHED"]  # the docstring above it is left alone
