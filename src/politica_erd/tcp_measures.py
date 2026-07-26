"""Interpret the AEC House TCP column whose heading is historically ambiguous."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable


PAIR_TOTAL_TOLERANCE = Decimal("0.02")
REPORTED_SHARE_TOLERANCE = Decimal("0.011")


@dataclass(frozen=True)
class TcpReportedPercentage:
    """One candidate's reported TCP percentage and total TCP votes."""

    reported: Decimal
    votes: int


def classify_tcp_reported_percentages(
    values: Iterable[TcpReportedPercentage],
    *,
    context: str,
) -> str:
    """Classify one complete two-candidate AEC ``Swing`` pair.

    Comparable contests contain a signed pair that sums to zero. The same AEC
    column contains current TCP percentages in non-comparable contests; those
    values sum to 100 and must reconcile to the candidates' TCP votes. Any
    other pattern is rejected instead of being guessed.
    """

    pair = tuple(values)
    if len(pair) != 2:
        raise ValueError(
            f"{context} must contain exactly two reported TCP percentage values; "
            f"found {len(pair)}"
        )
    if any(item.votes < 0 for item in pair):
        raise ValueError(f"{context} contains a negative TCP vote total")

    reported_total = sum((item.reported for item in pair), Decimal(0))
    if abs(reported_total) <= PAIR_TOTAL_TOLERANCE:
        return "swing"

    if abs(reported_total - Decimal(100)) <= PAIR_TOTAL_TOLERANCE:
        vote_total = sum(item.votes for item in pair)
        if vote_total <= 0:
            raise ValueError(
                f"{context} looks like a TCP vote-share pair but has no positive vote total"
            )
        for item in pair:
            expected = Decimal(item.votes) * Decimal(100) / Decimal(vote_total)
            if abs(expected - item.reported) > REPORTED_SHARE_TOLERANCE:
                raise ValueError(
                    f"{context} sums to 100, but the reported values do not reconcile "
                    "to the candidates' TCP votes"
                )
        return "vote_share"

    raise ValueError(
        f"{context} has an ambiguous TCP percentage pair totalling {reported_total}; "
        "expected a zero-sum swing pair or a 100-sum vote-share pair"
    )
