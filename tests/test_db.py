from datetime import date
from app import db

def test_stock_is_derived_from_movements(tmp_path):
    path = str(tmp_path / 'mill.sqlite')
    brand = db.create_brand('Sona Masoori', 'Raw', 500, 100, path=path, created='2026-10-10')
    db.add_movement(brand['id'], 'in', 200, '2026-10-10', path=path)
    db.add_movement(brand['id'], 'out', 125, '2026-10-10', path=path)
    assert db.get_brand(brand['id'], path)['stock_kg'] == 575

def test_cannot_dispatch_more_than_available(tmp_path):
    path = str(tmp_path / 'mill.sqlite')
    brand = db.create_brand('Matta', 'Parboiled', 10, None, path=path)
    try:
        db.add_movement(brand['id'], 'out', 11, '2026-10-10', path=path)
    except ValueError as exc:
        assert 'not enough stock' in str(exc)
    else:
        raise AssertionError('over-dispatch should be rejected')

def test_delete_cascades_movements(tmp_path):
    path = str(tmp_path / 'mill.sqlite')
    brand = db.create_brand('Ponni', 'Parboiled', 500, None, path=path)
    db.add_movement(brand['id'], 'out', 30, '2026-10-10', path=path)
    assert db.delete_brand(brand['id'], path)
    assert db.list_movements(path=path) == []


def test_duplicate_names_are_rejected_after_trimming_case_insensitively(tmp_path):
    path = str(tmp_path / 'mill.sqlite')
    db.create_brand('  Sona Masoori  ', 'Raw', 500, None, path=path)
    try:
        db.create_brand('sona masoori', 'Raw', 200, None, path=path)
    except ValueError as exc:
        assert str(exc) == 'You already have a brand with this name.'
    else:
        raise AssertionError('case-insensitive duplicate should be rejected')

def test_rename_rejects_duplicate_name_after_trimming(tmp_path):
    path = str(tmp_path / 'mill.sqlite')
    first = db.create_brand('Sona Masoori', 'Raw', 500, None, path=path)
    second = db.create_brand('Basmati', 'Basmati', 300, None, path=path)
    try:
        db.update_brand(second['id'], {'name': '  sona MASOORI  '}, path=path)
    except ValueError as exc:
        assert str(exc) == 'You already have a brand with this name.'
    else:
        raise AssertionError('duplicate rename should be rejected')
    assert db.get_brand(first['id'], path)['name'] == 'Sona Masoori'
