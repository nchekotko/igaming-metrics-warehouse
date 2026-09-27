"""Integration checks against a built warehouse (skipped if `imw all` has not been run)."""

import duckdb
import pytest

from imw.config import db_path

pytestmark = pytest.mark.skipif(not db_path().exists(), reason="warehouse not built (run `uv run imw all`)")


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(str(db_path()), read_only=True)
    try:
        c.sql("select 1 from marts.fct_player_daily limit 1")
    except duckdb.Error:
        pytest.skip("marts not built")
    yield c
    c.close()


def test_reported_ngr_uses_version_valid_at_the_time(con):
    rows = con.sql("""select activity_month, metric_version from marts.mart_metric_restatement
                      where is_applicable_version order by 1""").fetchall()
    for month, version in rows:
        assert version == (1 if str(month) < "2025-06-01" else 2)


def test_v2_restatement_lowers_historic_ngr(con):
    v1, v2 = con.sql("""select sum(ngr_restated_eur) filter (where metric_version = 1),
                               sum(ngr_restated_eur) filter (where metric_version = 2)
                        from marts.mart_metric_restatement where activity_month < '2025-06-01'""").fetchone()
    assert v2 < v1  # v2 additionally deducts provider fees and chargebacks


def test_scd2_attribution_has_history(con):
    n_multi = con.sql("select count(*) from marts.dim_affiliate_attribution where version_no > 1").fetchone()[0]
    assert n_multi > 0
    reattributed = con.sql("select sum(ftd_reattributed_later) from marts.mart_affiliate_performance").fetchone()[0]
    assert reattributed > 0


def test_legacy_timezone_normalisation_matches_wallet(con):
    mismatches = con.sql("""select count(*) from staging.stg_game_rounds r
                            join staging.stg_wallet_transactions w
                              on w.reference_id = r.round_id and w.tx_type = 'casino_stake'
                            where r.round_ts <> w.tx_ts""").fetchone()[0]
    assert mismatches == 0


def test_no_dq_failures(con):
    assert con.sql("select count(*) from dq.dq_summary where status = 'fail'").fetchone()[0] == 0
