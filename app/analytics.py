"""Deterministic stock arithmetic. Calendar windows include the supplied day.

Inputs may be dictionaries or schema instances. Stock comes from the storage
layer's derived Brand.stock_kg; movements are used only to measure usage.
Citations use '<brand name>.<field>'. Names must be unique.
"""
from datetime import date, datetime, timedelta
import math
from typing import Mapping

from .schemas import BrandFacts, Facts


NUMERIC_FIELDS = (
    'stock_kg', 'avg_daily_out_kg', 'days_left', 'days_left_low',
    'days_left_high', 'weeks_of_stock', 'out_last_7d_kg',
    'out_prior_7d_kg', 'change_pct',
)


def _get(item, key, default=None):
    return item.get(key, default) if isinstance(item, Mapping) else getattr(item, key, default)


def _day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _number(value, label):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f'{label} must be a finite, nonnegative number')
    return number


def _rounded(value):
    return round(value, 1)


def analyze(brands, movements, today):
    """Return Facts without changing inputs or reading the system clock.

    Three separate positive-out dates in the recent 14 days are required.
    A zero recent rate cannot supply a finite upper days-left bound.
    """
    as_of = _day(today)
    brands = list(brands)
    ids = [str(_get(b, 'id')) for b in brands]
    if len(set(ids)) != len(ids):
        raise ValueError('Brand identifiers must be unique')
    names = [str(_get(b, 'name')).strip().casefold() for b in brands]
    if len(set(names)) != len(names):
        raise ValueError('Brand names must be unique')
    grouped = {brand_id: [] for brand_id in ids}
    notes = []
    ignored_future = False
    ignored_unknown = False
    for movement in movements:
        brand_id = str(_get(movement, 'brand_id'))
        if brand_id not in grouped:
            ignored_unknown = True
            continue
        day = _day(_get(movement, 'date'))
        kind = _get(movement, 'kind')
        if kind not in ('in', 'out'):
            raise ValueError('Movement kind must be in or out')
        kg = _number(_get(movement, 'kg'), 'Movement weight')
        if day > as_of:
            ignored_future = True
            continue
        grouped[brand_id].append((day, kind, kg))
    if ignored_future:
        notes.append('Entries dated after this day were not used to work out how fast stock moves.')
    if ignored_unknown:
        notes.append('Entries for removed brands were not used.')

    result = []
    facts = {}
    for brand in brands:
        brand_id = str(_get(brand, 'id'))
        name = str(_get(brand, 'name'))
        stock = _number(_get(brand, 'stock_kg'), 'Stock weight')
        reorder = _get(brand, 'reorder_level_kg')
        if reorder is not None:
            reorder = _number(reorder, 'Reorder level')
        entries = grouped[brand_id]
        last = math.fsum(kg for day, kind, kg in entries
                         if kind == 'out' and as_of - timedelta(days=6) <= day <= as_of)
        prior = math.fsum(kg for day, kind, kg in entries
                          if kind == 'out' and as_of - timedelta(days=13) <= day < as_of - timedelta(days=6))
        out_dates = {day for day, kind, kg in entries if kind == 'out' and kg > 0
                     and as_of - timedelta(days=13) <= day <= as_of}
        change = _rounded((last - prior) / prior * 100) if prior > 0 else None
        avg = days = low = high = weeks = None
        status = 'no_data'
        if len(out_dates) >= 3 and last + prior > 0:
            first = min(day for day, _, _ in entries) if entries else _day(_get(brand, 'created'))
            covered = max(1, min(14, (as_of - first).days + 1))
            seven_covered = min(7, covered)
            raw_avg = (last + prior) / covered
            seven_rate = last / seven_covered
            avg = _rounded(raw_avg)
            days = math.floor(stock / raw_avg)
            weeks = _rounded(stock / raw_avg / 7)
            fastest = max(raw_avg, seven_rate)
            slowest = min(raw_avg, seven_rate)
            low = math.floor(stock / fastest)
            high = math.floor(stock / slowest) if slowest > 0 else None
            if days <= 7 or (reorder is not None and stock <= reorder):
                status = 'running_out'
            elif stock / raw_avg / 7 >= 6:
                status = 'piling_up'
            else:
                status = 'ok'
            if high is None:
                notes.append(f'{name}: no recent stock went out, so there is no firm upper limit on days left.')
        else:
            notes.append(f'{name}: add what went out on more separate days before relying on days left.')
        item = BrandFacts(
            brand_id=brand_id, name=name, rice_type=str(_get(brand, 'rice_type', 'Other')),
            stock_kg=_rounded(stock), avg_daily_out_kg=avg, days_left=days,
            days_left_low=low, days_left_high=high, weeks_of_stock=weeks,
            status=status, out_last_7d_kg=_rounded(last), out_prior_7d_kg=_rounded(prior),
            change_pct=change,
        )
        result.append(item)
        for field in NUMERIC_FIELDS:
            value = getattr(item, field)
            if value is not None:
                facts[f'{name}.{field}'] = value
    total = _rounded(math.fsum(_number(_get(b, 'stock_kg'), 'Stock weight') for b in brands))
    facts['total_stock_kg'] = total
    return Facts(
        as_of=as_of.isoformat(), brands=result, total_stock_kg=total,
        running_out=[item.name for item in result if item.status == 'running_out'],
        piling_up=[item.name for item in result if item.status == 'piling_up'],
        data_notes=notes, fact_ids=facts,
    )
