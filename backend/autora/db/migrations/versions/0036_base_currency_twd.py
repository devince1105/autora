"""the company's money is in TWD; the meter stays in USD (D-023)

The ledger gets one currency. A row that arrived in another keeps what arrived
(``source_amount``, ``source_currency``) and the rate it was converted at (``fx_rate``).

Existing money is converted once, here, at 32 TWD to the dollar — the rate written into
``FX_RATES`` on the day of the change, and recorded on every converted ledger row so the
conversion can be checked and undone:

- ``transactions`` in USD become TWD with their USD kept as the source. The table is
  append-only; the trigger is lifted for this statement only, and put back.
- ``budgets`` in USD are multiplied and relabelled.
- The core's metric names lose their currency (``cost_usd`` -> ``cost``, and so on), in stored
  KPI snapshots, kill criteria and goals, with money values multiplied; ``views_per_usd``
  becomes ``views_per_cost_unit`` and is divided, because it is per unit of money.
- Governance limits in ``company_policies`` lose ``_usd`` and are multiplied.

Not converted: ``model_calls``, reservations, run and task caps (the meter, still USD);
``payments`` and ``prices`` (they record what the provider charged, in its currency); frozen
``business_proposals`` (an approval points at the exact document that was approved) and the
``cycles.plan`` / ``review`` documents (what was written that day).
"""

import json
from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RATE = Decimal("32")
MONEY_METRICS = {
    "cost_usd": "cost",
    "model_cost_usd": "model_cost",
    "revenue_usd": "revenue",
    "profit_usd": "profit",
}
PER_MONEY_METRICS = {"views_per_usd": "views_per_cost_unit"}
POLICY_LIMITS = (
    "governance.ceo_budget_allocation_limit",
    "governance.exploration_budget_limit",
    "governance.ceo_scale_limit",
)


def _rename(name: str, forward: bool) -> tuple[str, str | None]:
    """(new name, 'money' | 'per_money' | None) for a metric name, prefixed or bare."""
    prefix, _, bare = name.rpartition(".")
    head = f"{prefix}." if prefix else ""
    money = MONEY_METRICS if forward else {v: k for k, v in MONEY_METRICS.items()}
    per = PER_MONEY_METRICS if forward else {v: k for k, v in PER_MONEY_METRICS.items()}
    if bare in money:
        return head + money[bare], "money"
    if bare in per:
        return head + per[bare], "per_money"
    return name, None


def _scale(value, kind: str | None, forward: bool):
    if kind is None or value is None or isinstance(value, bool):
        return value
    try:
        number = Decimal(str(value))
    except ArithmeticError:
        return value
    factor = RATE if (kind == "money") == forward else 1 / RATE
    scaled = (number * factor).quantize(Decimal("0.000001")).normalize()
    return str(scaled) if isinstance(value, str) else float(scaled)


def _criteria(node, forward: bool):
    """Walk a kill-criteria document; rename every metric and scale its threshold."""
    if isinstance(node, list):
        return [_criteria(item, forward) for item in node]
    if not isinstance(node, dict):
        return node
    out = {key: _criteria(value, forward) for key, value in node.items()}
    if isinstance(out.get("metric"), str):
        out["metric"], kind = _rename(out["metric"], forward)
        if "value" in out:
            out["value"] = _scale(out["value"], kind, forward)
    return out


def _convert_json(table: str, column: str, transform, forward: bool) -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL")
    ).all()
    for row_id, document in rows:
        changed = transform(document, forward)
        if changed != document:
            conn.execute(
                sa.text(f"UPDATE {table} SET {column} = CAST(:doc AS jsonb) WHERE id = :id"),
                {"doc": json.dumps(changed), "id": row_id},
            )


def _metrics(document, forward: bool):
    out = {}
    for key, value in document.items():
        name, kind = _rename(key, forward)
        out[name] = _scale(value, kind, forward)
    return out


def _goals(forward: bool) -> None:
    conn = op.get_bind()
    for goal_id, metric, target in conn.execute(
        sa.text("SELECT id, metric, target FROM company_goals")
    ).all():
        name, kind = _rename(metric, forward)
        if name != metric:
            conn.execute(
                sa.text("UPDATE company_goals SET metric = :m, target = :t WHERE id = :id"),
                {"m": name, "t": Decimal(str(_scale(str(target), kind, forward))), "id": goal_id},
            )


def _policies(forward: bool) -> None:
    conn = op.get_bind()
    for key in POLICY_LIMITS:
        old, new = (f"{key}_usd", key) if forward else (key, f"{key}_usd")
        for policy_id, value in conn.execute(
            sa.text("SELECT id, value FROM company_policies WHERE key = :k"), {"k": old}
        ).all():
            conn.execute(
                sa.text(
                    "UPDATE company_policies SET key = :k, value = CAST(:v AS jsonb) WHERE id = :id"
                ),
                {"k": new, "v": json.dumps(_scale(value, "money", forward)), "id": policy_id},
            )


def upgrade() -> None:
    op.add_column("transactions", sa.Column("source_amount", sa.Numeric(18, 6), nullable=True))
    op.add_column("transactions", sa.Column("source_currency", sa.Text(), nullable=True))
    op.add_column("transactions", sa.Column("fx_rate", sa.Numeric(18, 6), nullable=True))
    op.create_check_constraint(
        op.f("ck_transactions_conversion_complete"),
        "transactions",
        "(source_amount IS NULL) = (source_currency IS NULL) "
        "AND (source_amount IS NULL) = (fx_rate IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_transactions_fx_rate_positive"), "transactions", "fx_rate IS NULL OR fx_rate > 0"
    )
    for table in ("transactions", "budgets", "prices", "payments"):
        op.alter_column(table, "currency", server_default="TWD")

    op.execute("ALTER TABLE transactions DISABLE TRIGGER transactions_append_only")
    op.execute(
        f"""
        UPDATE transactions
           SET source_amount = amount, source_currency = 'USD', fx_rate = {RATE},
               amount = amount * {RATE}, currency = 'TWD'
         WHERE currency = 'USD'
        """
    )
    op.execute("ALTER TABLE transactions ENABLE TRIGGER transactions_append_only")
    op.execute(
        f"UPDATE budgets SET amount = amount * {RATE}, currency = 'TWD' WHERE currency = 'USD'"
    )

    _convert_json("kpi_snapshots", "metrics", _metrics, True)
    for table in ("projects", "business_units"):
        _convert_json(table, "kill_criteria", _criteria, True)
    _goals(True)
    _policies(True)


def downgrade() -> None:
    _policies(False)
    _goals(False)
    for table in ("projects", "business_units"):
        _convert_json(table, "kill_criteria", _criteria, False)
    _convert_json("kpi_snapshots", "metrics", _metrics, False)

    op.execute(
        f"UPDATE budgets SET amount = amount / {RATE}, currency = 'USD' WHERE currency = 'TWD'"
    )
    op.execute("ALTER TABLE transactions DISABLE TRIGGER transactions_append_only")
    op.execute(
        """
        UPDATE transactions
           SET amount = source_amount, currency = source_currency,
               source_amount = NULL, source_currency = NULL, fx_rate = NULL
         WHERE source_currency = 'USD'
        """
    )
    op.execute("ALTER TABLE transactions ENABLE TRIGGER transactions_append_only")

    for table in ("transactions", "budgets", "prices", "payments"):
        op.alter_column(table, "currency", server_default="USD")
    op.drop_constraint(op.f("ck_transactions_fx_rate_positive"), "transactions", type_="check")
    op.drop_constraint(op.f("ck_transactions_conversion_complete"), "transactions", type_="check")
    op.drop_column("transactions", "fx_rate")
    op.drop_column("transactions", "source_currency")
    op.drop_column("transactions", "source_amount")
