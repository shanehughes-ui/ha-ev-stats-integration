"""What a bucket holds, and what moves with energy when it is reattributed.

Pure, like `trapezoid.py`, `classify.py` and `rolling.py`.

A bucket is not one number. Energy filed against a place carries what it cost
and how much of it the panels covered, and those have to move together or not
at all. The YAML this replaces kept them apart - the energy in a utility meter,
the cost and the solar in two `input_number` helpers - and a correction made
anywhere other than the notification moved one of the three. The buckets stayed
balanced while free share, solar share and dollars per 100 km all drifted, and
nothing said so, because the one invariant being checked was the one that still
held.

Splitting is the part worth reading. Moving some of a bucket's energy has to
move a fair share of its cost, and when the caller knows the exact figures - a
correction against a logged session does - it passes them. When it does not,
the share is pro rata: half a bucket's kWh carries half its cost. That is an
approximation, and it is named as one, but it is bounded and self-correcting in
a way that moving nothing is not. Moving nothing leaves cost attributed to a
place the energy is no longer in, and nothing ever puts it back.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal(0)


@dataclass(frozen=True)
class Balance:
    """Energy, what it cost, and how much of it was solar.

    Frozen: every operation returns a new balance. A ledger that mutates its
    own records in place is one exception away from a half-applied move.
    """

    kwh: Decimal = ZERO
    cost: Decimal = ZERO
    solar_kwh: Decimal = ZERO

    def __add__(self, other: Balance) -> Balance:
        return Balance(
            self.kwh + other.kwh,
            self.cost + other.cost,
            self.solar_kwh + other.solar_kwh,
        )

    def __sub__(self, other: Balance) -> Balance:
        return Balance(
            self.kwh - other.kwh,
            self.cost - other.cost,
            self.solar_kwh - other.solar_kwh,
        )

    def is_empty(self) -> bool:
        return self.kwh == ZERO and self.cost == ZERO and self.solar_kwh == ZERO

    def as_dict(self) -> dict[str, str]:
        """Stored as strings so the Decimals survive the round trip.

        JSON has no decimal type; storing these as floats would reintroduce the
        drift the whole ledger uses Decimal to avoid.
        """
        return {
            "kwh": str(self.kwh),
            "cost": str(self.cost),
            "solar_kwh": str(self.solar_kwh),
        }

    @classmethod
    def from_dict(cls, data: object) -> Balance:
        """Read a stored balance, including one written before cost existed."""
        # A bare string or number is the version-1 format: energy only. Reading
        # it as energy with no cost is exactly right - there was none recorded.
        if isinstance(data, (str, int, float)):
            return cls(kwh=Decimal(str(data)))
        if not isinstance(data, dict):
            return cls()
        return cls(
            kwh=Decimal(str(data.get("kwh", "0"))),
            cost=Decimal(str(data.get("cost", "0"))),
            solar_kwh=Decimal(str(data.get("solar_kwh", "0"))),
        )


def share(balance: Balance, kwh: Decimal) -> Balance:
    """The cost and solar that `kwh` carries out of `balance`.

    Pro rata against the energy already there. Two edge cases matter and both
    return energy alone rather than raising:

      * an empty bucket has no cost to apportion, so there is nothing to carry;
      * a request for more than the bucket holds would scale the cost above
        100% and take out more money than went in. The ledger clamps the energy
        before calling this, so this is a second line rather than the first.
    """
    if balance.kwh <= ZERO or kwh <= ZERO:
        return Balance(kwh=max(ZERO, kwh))
    if kwh >= balance.kwh:
        # Everything, exactly. Computing this as a fraction would leave a
        # rounding crumb of cost behind in a bucket with no energy in it.
        return balance
    fraction = kwh / balance.kwh
    return Balance(
        kwh=kwh,
        cost=balance.cost * fraction,
        solar_kwh=balance.solar_kwh * fraction,
    )


def carried(
    balance: Balance,
    kwh: Decimal,
    cost: Decimal | None = None,
    solar_kwh: Decimal | None = None,
) -> Balance:
    """What a move of `kwh` should take with it.

    Explicit figures win: a correction against a logged session knows what that
    session actually cost, and the average across a bucket holding weeks of
    charges at different prices is not it. Anything not given falls back to the
    pro-rata share.
    """
    pro_rata = share(balance, kwh)
    return Balance(
        kwh=pro_rata.kwh,
        cost=pro_rata.cost if cost is None else cost,
        solar_kwh=pro_rata.solar_kwh if solar_kwh is None else solar_kwh,
    )
