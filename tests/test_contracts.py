from datetime import datetime

import pytest

from imw.contracts import ContractViolation, load_contracts, parse_timestamp


@pytest.fixture(scope="module")
def contracts():
    _, c = load_contracts()
    return c


def wallet_payload(**kw):
    p = {
        "event_id": "TX-1",
        "player_id": "P000001",
        "tx_ts": "2025-03-01T10:00:00Z",
        "tx_type": "deposit",
        "amount": 25.5,
        "currency": "EUR",
        "payment_method": "card",
        "reference_id": None,
    }
    p.update(kw)
    return p


def test_all_entities_compiled(contracts):
    assert {
        "players",
        "wallet_transactions",
        "bets",
        "bet_legs",
        "game_rounds",
        "bonus_events",
        "rg_events",
        "attribution_events",
        "experiment_assignments",
    } <= set(contracts)


def test_valid_payload_normalised_to_naive_utc(contracts):
    rec, tz_adj = contracts["wallet_transactions"].validate(
        wallet_payload(tx_ts="2025-03-01T12:00:00+02:00"), "wallet-service"
    )
    assert rec["tx_ts"] == datetime(2025, 3, 1, 10, 0, 0)
    assert tz_adj is True


@pytest.mark.parametrize(
    "change,needle",
    [
        ({"amount": -5.0}, "amount"),
        ({"currency": "XXX"}, "currency"),
        ({"tx_type": "DEPOSIT"}, "tx_type"),
        ({"player_id": "player-7"}, "player_id"),
        ({"amount": "25.5"}, "amount"),  # strict: numeric strings are not numbers
        ({"tx_ts": 1740823200000}, "tx_ts"),  # epoch millis are rejected
        ({"tx_ts": "2025-03-01T10:00:00"}, "without UTC offset"),
    ],
)
def test_violations_are_rejected(contracts, change, needle):
    with pytest.raises(ContractViolation) as exc:
        contracts["wallet_transactions"].validate(wallet_payload(**change), "wallet-service")
    assert needle in str(exc.value)


def test_missing_required_field(contracts):
    p = wallet_payload()
    p.pop("reference_id")  # nullable but required to be present
    with pytest.raises(ContractViolation):
        contracts["wallet_transactions"].validate(p, "wallet-service")


def test_extra_fields_are_dropped(contracts):
    rec, _ = contracts["wallet_transactions"].validate(wallet_payload(new_field=1), "wallet-service")
    assert "new_field" not in rec


def test_legacy_source_local_time_is_converted(contracts):
    p = {
        "event_id": "R1",
        "player_id": "P000001",
        "round_ts": "2025-07-01T01:30:00",
        "provider_id": "PRV_LEGACY",
        "game_id": "g",
        "game_type": "slots",
        "stake": 1.0,
        "win": 0.0,
        "currency": "EUR",
    }
    rec, tz_adj = contracts["game_rounds"].validate(p, "legacy-casino-gw")
    assert rec["round_ts"] == datetime(2025, 6, 30, 23, 30)  # Malta is UTC+2 in summer
    assert tz_adj
    with pytest.raises(ContractViolation):  # same payload from a source without the override
        contracts["game_rounds"].validate(p, "casino-aggregator")


def test_dst_gap_and_fold():
    # 2025-03-30 02:30 does not exist in Europe/Malta (clocks jump 02:00 -> 03:00): shifted forward.
    assert parse_timestamp("2025-03-30T02:30:00", "Europe/Malta")[0] == datetime(2025, 3, 30, 1, 30)
    # 2025-10-26 02:30 happens twice: first occurrence (CEST, UTC+2) is used.
    assert parse_timestamp("2025-10-26T02:30:00", "Europe/Malta")[0] == datetime(2025, 10, 26, 0, 30)
    assert parse_timestamp("2025-01-15T12:00:00", "Europe/Malta")[0] == datetime(2025, 1, 15, 11, 0)


def test_garbage_timestamp():
    with pytest.raises(ValueError):
        parse_timestamp("31/12/2025 10:00")
