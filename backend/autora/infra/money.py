"""Currencies and the one conversion between them (D-023).

The company keeps its money in one currency, the **base** (``BASE_CURRENCY``, TWD): the
ledger, budgets, reports and every threshold a person or the CEO reads. The **meter** — what
each model call and tool call cost — stays in USD, because that is what the providers charge,
and converting every call would only add rounding to a fact.

The two meet in a few places, and each goes through here:

- the ledger converts anything not in the base on the way in, and keeps the original amount
  and the rate on the same row (``transactions.source_amount``, ``fx_rate``);
- reports convert what the meter says into the base when they add it up;
- the cost guard converts a budget, written in the base, into the meter's currency before it
  compares.

Rates are fixed and set by hand (``FX_RATES``). A daily rate is an external dependency the
amounts do not yet justify; because every converted row records its own rate, switching later
changes nothing already written.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from autora.infra.settings import get_settings

MONEY = Decimal("0.000001")
"""Six decimal places, the precision every money column keeps."""

METER_CURRENCY = "USD"
"""What ``model_calls.cost_usd``, reservations and per-run caps are counted in."""


class FxError(Exception):
    pass


@dataclass(frozen=True)
class Converted:
    """An amount in the base currency, and where it came from."""

    amount: Decimal
    rate: Decimal | None
    """Base units per one source unit; None when nothing was converted."""
    source_amount: Decimal | None
    source_currency: str | None


@dataclass(frozen=True)
class Fx:
    base: str
    rates: dict[str, Decimal]
    """Base units per one unit of each other currency."""

    @classmethod
    def from_settings(cls) -> Fx:
        settings = get_settings()
        return cls(base=settings.base_currency, rates=dict(settings.fx_rates))

    def rate(self, currency: str) -> Decimal:
        if currency == self.base:
            return Decimal(1)
        rate = self.rates.get(currency)
        if rate is None or rate <= 0:
            raise FxError(
                f"no rate from {currency} to {self.base}; add it to FX_RATES "
                f'(e.g. {{"{currency}": "32"}})'
            )
        return Decimal(rate)

    def to_base(self, amount: Decimal, currency: str) -> Converted:
        amount = Decimal(amount)
        if currency == self.base:
            return Converted(amount.quantize(MONEY), None, None, None)
        rate = self.rate(currency)
        return Converted((amount * rate).quantize(MONEY), rate, amount.quantize(MONEY), currency)

    def convert(self, amount: Decimal, *, source: str, target: str) -> Decimal:
        """Any currency to any other, through the base."""
        if source == target:
            return Decimal(amount).quantize(MONEY)
        in_base = Decimal(amount) * self.rate(source)
        return (in_base / self.rate(target)).quantize(MONEY)

    def metered(self, amount_usd: Decimal | int | None) -> Decimal:
        """What the meter says, in the base. The one conversion reports make."""
        return self.to_base(Decimal(amount_usd or 0), METER_CURRENCY).amount


def fx() -> Fx:
    return Fx.from_settings()


def base_currency() -> str:
    return get_settings().base_currency


SYMBOLS = {"TWD": "NT$", "USD": "$"}


def format_money(amount: Decimal | float | int | str, currency: str | None = None) -> str:
    """``NT$160`` — how an amount reads to a person or an agent, in the base unless told."""
    currency = currency or base_currency()
    value = Decimal(str(amount)).normalize()
    text = f"{value:f}" if value == value.to_integral() else f"{value}"
    symbol = SYMBOLS.get(currency)
    return f"{symbol}{text}" if symbol else f"{currency} {text}"
