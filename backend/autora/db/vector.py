"""pgvector's ``halfvec`` column type for SQLAlchemy, without the ``pgvector`` Python package.

Values travel as pgvector's text form (``[0.1,0.2,...]``) and are cast in SQL, so the asyncpg
driver needs no codec for the extension type. ``halfvec`` (16-bit floats) halves the storage of
``vector`` and can be indexed with HNSW up to 4,000 dimensions (``vector``: 2,000), which is what
2,048-dimensional embedding models need.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Text, cast, type_coerce
from sqlalchemy.types import TypeDecorator, UserDefinedType


def to_text(values: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


class _VectorText(TypeDecorator):
    """The bound value as it travels: pgvector's text form, sent as a text parameter."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else to_text(value)


class HalfVector(UserDefinedType):
    cache_ok = True

    def __init__(self, dim: int):
        self.dim = dim

    def get_col_spec(self, **kw) -> str:
        return f"halfvec({self.dim})"

    def bind_expression(self, bindvalue):
        return cast(type_coerce(bindvalue, _VectorText()), self)

    def column_expression(self, column):
        # read as text, but keep this type so result_processor parses it
        return type_coerce(cast(column, Text), self)

    def result_processor(self, dialect, coltype):
        def process(value):
            if value is None:
                return None
            return [float(v) for v in value.strip("[]").split(",") if v]

        return process
