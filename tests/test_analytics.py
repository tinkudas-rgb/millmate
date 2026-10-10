from pathlib import Path
import sys

# Keep plain pytest working without requiring an installed project package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import date, timedelta
from copy import deepcopy
import math

import pytest

from app.analytics import analyze, NUMERIC_FIELDS

TODAY = date(2026, 10, 10)


def brand(id='rice', stock=100, reorder=None, name='Sona Masoori'):
    return dict(id=id, name=name, rice_type='Raw', stock_kg=stock,
                reorder_level_kg=reorder, created='2026-09-01')


def out(id='rice', days_ago=0, kg=10, kind='out'):
    return dict(id=f'{id}-{days_ago}-{kind}', brand_id=id, kind=kind, kg=kg,
                date=(TODAY - timedelta(days=days_ago)).isoformat(), note='')


def daily(id='rice', days=14, kg=10):
    return [out(id, n, kg) for n in range(days)]


def test_regular_daily_sales_and_full_registration():
    f = analyze([brand(stock=140)], daily(), TODAY)
    b = f.brands[0]
    assert b.avg_daily_out_kg == 10
    assert (b.days_left, b.days_left_low, b.days_left_high) == (14, 14, 14)
    assert b.weeks_of_stock == 2
    assert b.status == 'ok'
    assert b.out_last_7d_kg == b.out_prior_7d_kg == 70
    assert b.change_pct == 0
    assert f.total_stock_kg == 140
    for field in NUMERIC_FIELDS:
        value = getattr(b, field)
        if value is not None:
            assert f.fact_ids[f'Sona Masoori.{field}'] == value
    assert f.fact_ids['total_stock_kg'] == f.total_stock_kg


def test_recent_and_prior_windows_exact_boundaries():
    movements = [out(days_ago=n, kg=20) for n in range(7)]
    movements += [out(days_ago=n, kg=10) for n in range(7, 14)]
    movements += [out(days_ago=14, kg=999)]
    b = analyze([brand(stock=300)], movements, TODAY).brands[0]
    assert b.out_last_7d_kg == 140
    assert b.out_prior_7d_kg == 70
    assert b.change_pct == 100
    assert b.avg_daily_out_kg == 15
    assert (b.days_left, b.days_left_low, b.days_left_high) == (20, 15, 20)


def test_short_history_uses_calendar_days_not_sale_count():
    b = analyze([brand(stock=100)], [out(days_ago=n) for n in (0, 2, 4)], TODAY).brands[0]
    assert b.avg_daily_out_kg == 6
    assert b.days_left == 16
    assert b.change_pct is None
    assert (b.days_left_low, b.days_left_high) == (16, 16)


def test_first_in_movement_extends_covered_days():
    movements = [out(days_ago=n) for n in range(3)] + [out(days_ago=9, kind='in', kg=100)]
    b = analyze([brand(stock=90)], movements, TODAY).brands[0]
    assert b.avg_daily_out_kg == 3
    assert b.days_left == 30
    assert (b.days_left_low, b.days_left_high) == (21, 30)


def test_three_entries_on_same_day_are_not_three_days():
    f = analyze([brand()], [out() for _ in range(3)], TODAY)
    b = f.brands[0]
    assert b.status == 'no_data'
    assert b.avg_daily_out_kg is None
    assert b.days_left is None
    assert b.out_last_7d_kg == 30
    assert 'Sona Masoori.days_left' not in f.fact_ids


def test_zero_weight_and_in_entries_do_not_count_as_out_days():
    movements = [out(days_ago=0), out(days_ago=1), out(days_ago=2, kg=0), out(days_ago=3, kind='in')]
    assert analyze([brand()], movements, TODAY).brands[0].status == 'no_data'


def test_zero_recent_rate_has_no_finite_upper_bound():
    b = analyze([brand(stock=70)], [out(days_ago=n) for n in (7, 8, 13)], TODAY).brands[0]
    assert b.avg_daily_out_kg == 2.1
    assert b.days_left_low == 32
    assert b.days_left_high is None
    assert b.change_pct == -100


def test_stale_history_is_no_data():
    b = analyze([brand()], [out(days_ago=n) for n in (15, 16, 17)], TODAY).brands[0]
    assert b.status == 'no_data'
    assert b.avg_daily_out_kg is None


@pytest.mark.parametrize('stock,reorder,status', [
    (0, None, 'running_out'), (79, None, 'running_out'),
    (80, None, 'ok'), (420, None, 'piling_up'),
    (419, None, 'ok'), (500, 500, 'running_out'),
])
def test_status_boundaries_and_reorder_precedence(stock, reorder, status):
    f = analyze([brand(stock=stock, reorder=reorder)], daily(), TODAY)
    assert f.brands[0].status == status
    assert f.running_out == (['Sona Masoori'] if status == 'running_out' else [])
    assert f.piling_up == (['Sona Masoori'] if status == 'piling_up' else [])


def test_duplicate_names_rejected_to_protect_citations():
    with pytest.raises(ValueError):
        analyze([brand('a', 100), brand('b', 200)], daily('a') + daily('b'), TODAY)


def test_future_and_removed_brand_entries_are_ignored_with_notes():
    f = analyze([brand()], daily() + [out(days_ago=-1, kg=1000), out('gone')], TODAY)
    assert f.brands[0].avg_daily_out_kg == 10
    assert len(f.data_notes) == 2


def test_empty_mill_and_inputs_not_changed():
    assert analyze([], [], TODAY).total_stock_kg == 0
    brands, movements = [brand()], daily()
    saved = deepcopy((brands, movements))
    first = analyze(brands, movements, TODAY)
    assert (brands, movements) == saved
    assert first == analyze(brands, list(reversed(movements)), TODAY.isoformat())


@pytest.mark.parametrize('stock', [-1, math.nan, math.inf])
def test_invalid_weight_rejected(stock):
    with pytest.raises(ValueError):
        analyze([brand(stock=stock)], daily(), TODAY)


def test_duplicate_identifiers_rejected():
    with pytest.raises(ValueError):
        analyze([brand(), brand()], [], TODAY)


def test_one_decimal_display_but_day_counts_use_unrounded_rate():
    b = analyze([brand(stock=10.04)], [out(days_ago=n, kg=1.04) for n in range(3)], TODAY).brands[0]
    assert b.stock_kg == 10.0
    assert b.avg_daily_out_kg == 1.0
    assert b.days_left == 9  # 10.04 / 1.04, not the rounded displays
    assert b.out_last_7d_kg == 3.1


def test_no_data_precedes_low_stock_and_reorder():
    f = analyze([brand(stock=0, reorder=100)], [], TODAY)
    assert f.brands[0].status == 'no_data'
    assert f.running_out == []


def test_schema_instances_and_citation_keys_match_contract_exactly():
    from app.schemas import Brand, Movement
    f = analyze([Brand(**brand())], [Movement(**m) for m in daily()], TODAY)
    expected = {f'Sona Masoori.{field}' for field in NUMERIC_FIELDS}
    assert set(f.fact_ids) == expected | {'total_stock_kg'}
    assert f.as_of == '2026-10-10'
    assert f.model_dump()['brands'][0]['brand_id'] == 'rice'


def test_names_are_unique_after_trim_and_casefold():
    with pytest.raises(ValueError):
        analyze([brand('a', name='Rice'), brand('b', name=' rice ')], [], TODAY)
