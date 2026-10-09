from decimal import Decimal

import pytest

from fbc.ledger.model import INR, Floor, Leg, Posting, UnbalancedPosting, money


def _posting(legs, **kw):
    return Posting("DEPOSIT", kw.pop("key", "k1"), "2026-10-01", "2026-10-01T09:00:00", legs, **kw)


def test_balanced_posting_validates():
    _posting(
        [Leg("CUST#1:CASH", INR, Decimal(5)), Leg("EXT:UPI_PAYIN", INR, Decimal(-5))]
    ).validate()


def test_unbalanced_posting_is_refused():
    with pytest.raises(UnbalancedPosting, match="do not balance"):
        _posting(
            [Leg("CUST#1:CASH", INR, Decimal(5)), Leg("EXT:UPI_PAYIN", INR, Decimal(-4))]
        ).validate()


def test_each_unit_must_balance_separately():
    legs = [
        Leg("CUST#1:CASH", INR, Decimal(-10)),
        Leg("CCP:CASH_PAYABLE", INR, Decimal(10)),
        Leg("CUST#1:SEC_PENDING", "INE0001", Decimal(1)),
    ]
    with pytest.raises(UnbalancedPosting):
        _posting(legs).validate()


def test_duplicate_leg_and_orphan_constraint_are_refused():
    with pytest.raises(UnbalancedPosting, match="duplicate"):
        _posting([Leg("A:X", INR, Decimal(1)), Leg("A:X", INR, Decimal(-1))]).validate()
    with pytest.raises(UnbalancedPosting, match="constraint"):
        _posting(
            [Leg("A:X", INR, Decimal(1)), Leg("B:Y", INR, Decimal(-1))],
            constraints={("C:Z", INR): Floor(min_after=Decimal(0))},
        ).validate()


def test_txn_id_is_deterministic_per_idempotency_key():
    legs = [Leg("A:X", INR, Decimal(1)), Leg("B:Y", INR, Decimal(-1))]
    assert _posting(legs, key="same").txn_id == _posting(legs, key="same").txn_id
    assert _posting(legs, key="a").txn_id != _posting(legs, key="b").txn_id


def test_request_hash_changes_with_body():
    a = _posting([Leg("A:X", INR, Decimal(1)), Leg("B:Y", INR, Decimal(-1))])
    b = _posting([Leg("A:X", INR, Decimal(2)), Leg("B:Y", INR, Decimal(-2))])
    assert a.request_hash() != b.request_hash()


def test_money_rounds_half_up():
    assert money("1.005") == Decimal("1.01")
    assert money(2) == Decimal("2.00")
