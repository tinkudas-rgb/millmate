from datetime import date
from app.demo_data import build_demo

def test_demo_has_six_brands_and_thirty_days_of_history():
    brands, movements = build_demo(date(2026, 10, 10))
    assert len(brands) == 6
    assert len(movements) == 6 * 31
    assert {b['rice_type'] for b in brands} >= {'Basmati', 'Raw', 'Parboiled', 'Non-basmati'}
    assert len({m['date'] for m in movements}) == 30

def test_demo_basmati_has_a_running_out_story():
    brands, movements = build_demo(date(2026, 10, 10))
    basmati = next(b for b in brands if b['name'] == 'India Gate Basmati')
    out_14d = sum(m['kg'] for m in movements if m['brand_id'] == basmati['id'] and m['kind'] == 'out' and m['date'] >= '2026-09-27')
    average = out_14d / 14
    stock = basmati['stock_kg'] + sum(m['kg'] if m['kind']=='in' else -m['kg'] for m in movements if m['brand_id']==basmati['id'])
    assert stock < 7 * average
