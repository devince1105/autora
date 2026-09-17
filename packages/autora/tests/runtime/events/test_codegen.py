"""T-108: event schema codegen (pydantic -> zod)."""

import importlib.util
from pathlib import Path

import pytest

from autora.app import load_event_catalogs
from autora.runtime.events import EventPayload
from autora.runtime.events.codegen import CodegenError, _Emitter, generate
from autora.runtime.events.schema import registered_event_types

REPO = Path(__file__).resolve().parents[5]


def _script():
    spec = importlib.util.spec_from_file_location("gen", REPO / "scripts/gen_event_schema.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _production_registry():
    load_event_catalogs()
    # Tests may register throwaway types (e.g. TEST_ONLY_*); the committed output excludes them.
    return {k: v for k, v in registered_event_types().items() if not k[0].startswith("TEST_ONLY")}


def test_committed_output_is_up_to_date():
    source, catalog = generate(_production_registry())
    ts_path = REPO / "packages/event-schema/src/generated.ts"
    assert ts_path.read_text() == source, "run `make gen-schema` and commit the result"


def test_output_is_deterministic():
    registry = _production_registry()
    assert generate(registry) == generate(dict(reversed(list(registry.items()))))


def test_every_event_type_is_in_the_union():
    registry = _production_registry()
    source, catalog = generate(registry)
    for event_type, _version in registry:
        assert f'event_type: z.literal("{event_type}")' in source
    assert set(catalog["events"]) == {t for t, _ in registry}


def test_multiple_versions_become_nested_union():
    class V1(EventPayload):
        a: int

    class V2(EventPayload):
        b: int

    for cls, version in ((V1, 1), (V2, 2)):
        cls.event_type, cls.schema_version = "X_THING", version
        cls.persistence = registered_event_types()[("AGENT_IDLE", 1)].persistence
    source, _ = generate({("X_THING", 1): V1, ("X_THING", 2): V2})
    assert 'z.discriminatedUnion("schema_version", [XThingV1Event, XThingV2Event])' in source


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        ({"type": "string", "format": "uuid"}, "z.uuid()"),
        ({"type": "string", "format": "date-time"}, "z.iso.datetime({ offset: true })"),
        ({"type": "integer", "minimum": 0}, "z.number().int().min(0)"),
        ({"type": "number", "exclusiveMinimum": 0}, "z.number().gt(0)"),
        ({"enum": ["a", "b"], "type": "string"}, 'z.enum(["a", "b"])'),
        ({"anyOf": [{"type": "string"}, {"type": "null"}]}, "z.string().nullable()"),
        ({"type": "object", "additionalProperties": True}, "z.record(z.string(), z.unknown())"),
        ({}, "z.unknown()"),
    ],
)
def test_node_conversion(node, expected):
    assert _Emitter().zod(node) == expected


@pytest.mark.parametrize(
    "node",
    [
        {"type": "string", "format": "email"},
        {"type": "string", "oneOf": []},
        {"type": "tuple"},
    ],
)
def test_unsupported_schema_fails_loudly(node):
    with pytest.raises(CodegenError):
        _Emitter().zod(node)


def test_check_mode_detects_stale_output(tmp_path, monkeypatch):
    gen = _script()
    stale = tmp_path / "generated.ts"
    stale.write_text("// old\n")
    monkeypatch.setattr(gen, "TS_OUT", stale)
    monkeypatch.setattr(gen, "JSON_OUT", tmp_path / "catalog.json")
    monkeypatch.setattr("sys.argv", ["gen", "--check"])
    assert gen.main() == 1
