from pathlib import Path
import sys

# Keep plain pytest working without requiring an installed project package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import date, timedelta
import re

from app.analytics import analyze
from app.recommend import recommend
from app.schemas import Story

TODAY = date(2026, 10, 10)
STORY = Story(headline='Stock today', summary='Check your stock.', claims=[])


def make_facts(stocks):
    brands = [dict(id=id, name=id, rice_type='Raw', stock_kg=stock,
                   reorder_level_kg=None, created='2026-09-01') for id, stock in stocks]
    movements = [dict(id=f'{id}-{day}', brand_id=id, kind='out', kg=10,
                      date=(TODAY - timedelta(days=day)).isoformat(), note='')
                 for id, _ in stocks for day in range(14)]
    return analyze(brands, movements, TODAY)


def test_priority_is_shortage_then_excess_then_ok():
    f = make_facts([('normal', 200), ('excess', 500), ('low', 50), ('lowest', 10)])
    cards = recommend(f, STORY)
    assert len(cards) == 3
    assert 'lowest' in cards[0].title
    assert 'low' in cards[1].title
    assert 'excess' in cards[2].title


def test_piling_up_sort_largest_weeks_first_and_ties_stable():
    f = make_facts([('a', 500), ('c', 1000), ('b', 500)])
    titles = [a.title for a in recommend(f, STORY)]
    assert titles == ['Hold off on more c', 'Hold off on more a', 'Hold off on more b']


def test_single_brand_padded_to_three_distinct_cards():
    actions = recommend(make_facts([('rice', 50)]), STORY)
    assert len(actions) == 3
    assert len({a.title for a in actions}) == 3
    assert actions[1].title == 'Check the loads already on the way'
    assert actions[2].title == 'Record what went out each day'


def test_two_brands_padded_to_three():
    actions = recommend(make_facts([('a', 50), ('b', 500)]), STORY)
    assert len(actions) == 3
    assert actions[2].title == 'Check the loads already on the way'


def test_no_data_returns_only_learning_card():
    f = analyze([], [], TODAY)
    actions = recommend(f, STORY)
    assert len(actions) == 1
    assert actions[0].title == 'Add what you sold so I can learn'


def test_every_card_cites_existing_numbers():
    f = make_facts([('low', 20), ('high', 700), ('normal', 200)])
    for action in recommend(f, STORY):
        assert action.proof
        for proof in action.proof:
            key = re.search(r'\[fact: (.+)\]', proof).group(1)
            assert key in f.fact_ids
            assert str(f.fact_ids[key]) in proof
        for value in re.findall(r'\d+(?:\.\d+)?', action.why):
            assert any(float(value) == v for v in f.fact_ids.values() if isinstance(v, (int, float)))


def test_plain_words_no_llm_dependency_and_story_not_used_as_evidence():
    f = make_facts([('rice', 100)])
    unsafe_story = Story(headline='Buy 99999 kg', summary='Wrong', claims=[])
    assert recommend(f, unsafe_story) == recommend(f, STORY)
    banned = r'\b(forecast|model|algorithm|API|CSV|database|dataset|anomaly|KPI|regression|token)\b'
    for action in recommend(f, STORY):
        assert not re.search(banned, ' '.join([action.title, action.why, action.expected_effect] + action.proof), re.I)


def test_stock_card_and_action_share_canonical_days_and_rate_after_today_dispatch():
    # Recent week is faster than the prior week: range endpoints intentionally
    # differ, but the action and stock card must show the same canonical count.
    brand = dict(id='rice', name='India Gate Basmati', rice_type='Basmati',
                 stock_kg=1050, reorder_level_kg=None, created='2026-09-01')
    movements = [dict(id=f'm-{n}', brand_id='rice', kind='out',
                      kg=250 if n < 7 else 210,
                      date=(TODAY - timedelta(days=n)).isoformat(), note='')
                 for n in range(14)]
    facts = analyze([brand], movements, TODAY)
    snapshot = facts.model_dump()
    current = facts.brands[0]
    assert current.avg_daily_out_kg == 230
    assert current.days_left == 4
    assert current.days_left_high == 4
    action = recommend(facts, STORY)[0]
    assert action.why == 'India Gate Basmati has about 4 days left at the recent pace.'
    assert f'days left = {current.days_left}' in ' '.join(action.proof)
    assert f'each day in kg = {current.avg_daily_out_kg}' in ' '.join(action.proof)
    assert 'faster pace' not in ' '.join(action.proof)
    assert 'slower pace' not in ' '.join(action.proof)
    assert facts.model_dump() == snapshot
    # Earlier snapshot really does differ. Advice must use the current full
    # movement list, never a stale or differently dated state.
    before = analyze([brand], movements[1:], TODAY)
    assert before.brands[0].avg_daily_out_kg != current.avg_daily_out_kg


def test_range_does_not_replace_canonical_days_in_action():
    brand = dict(id='rice', name='Rice', rice_type='Raw', stock_kg=60,
                 reorder_level_kg=None, created='2026-09-01')
    movements = [dict(id=str(n), brand_id='rice', kind='out',
                      kg=20 if n < 7 else 10,
                      date=(TODAY - timedelta(days=n)).isoformat(), note='')
                 for n in range(14)]
    facts = analyze([brand], movements, TODAY)
    current = facts.brands[0]
    assert (current.days_left, current.days_left_low, current.days_left_high) == (4, 3, 4)
    assert recommend(facts, STORY)[0].why == 'Rice has about 4 days left at the recent pace.'
