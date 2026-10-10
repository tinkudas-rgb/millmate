"""SQLite storage for one mill. Stock is calculated from opening stock and movements."""
import os
import sqlite3
import uuid
from datetime import date
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

DEFAULT_DB = Path(__file__).with_name("data.db")

def db_path() -> str:
    return os.environ.get("DATABASE_PATH", str(DEFAULT_DB))

def connect(path: str | None = None) -> sqlite3.Connection:
    target = path or db_path()
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

@contextmanager
def session(path: str | None = None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def initialize(path: str | None = None) -> None:
    with session(path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS brands (
            id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            rice_type TEXT NOT NULL, opening_stock_kg REAL NOT NULL CHECK(opening_stock_kg >= 0),
            reorder_level_kg REAL CHECK(reorder_level_kg IS NULL OR reorder_level_kg >= 0),
            created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS movements (
            id TEXT PRIMARY KEY,
            brand_id TEXT NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK(kind IN ('in','out')),
            kg REAL NOT NULL CHECK(kg > 0), date TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS movement_date_idx ON movements(date DESC);
        CREATE INDEX IF NOT EXISTS movement_brand_idx ON movements(brand_id,date);
        """)

def list_brands(path: str | None = None) -> list[dict]:
    initialize(path)
    with session(path) as conn:
        rows = conn.execute("""SELECT b.id,b.name,b.rice_type,b.opening_stock_kg,b.reorder_level_kg,b.created,
          COALESCE(SUM(CASE WHEN m.kind='in' THEN m.kg WHEN m.kind='out' THEN -m.kg ELSE 0 END),0) AS net
          FROM brands b LEFT JOIN movements m ON m.brand_id=b.id GROUP BY b.id ORDER BY b.created,b.name""").fetchall()
    return [dict(id=r['id'], name=r['name'], rice_type=r['rice_type'], stock_kg=round(r['opening_stock_kg']+r['net'],3), reorder_level_kg=r['reorder_level_kg'], created=r['created']) for r in rows]

def get_brand(brand_id: str, path: str | None = None) -> dict | None:
    return next((b for b in list_brands(path) if b['id'] == brand_id), None)

def create_brand(name: str, rice_type: str, stock_kg: float, reorder_level_kg: float | None, path: str | None = None, created: str | None = None) -> dict:
    initialize(path)
    record_id = str(uuid.uuid4())
    created = created or date.today().isoformat()
    try:
        with session(path) as conn:
            conn.execute("INSERT INTO brands(id,name,rice_type,opening_stock_kg,reorder_level_kg,created) VALUES(?,?,?,?,?,?)", (record_id,name.strip(),rice_type.strip(),float(stock_kg),reorder_level_kg,created))
    except sqlite3.IntegrityError as exc:
        raise ValueError("You already have a brand with this name.") from exc
    return get_brand(record_id, path)

def update_brand(brand_id: str, changes: dict, path: str | None = None) -> dict | None:
    initialize(path)
    allowed = {k:v for k,v in changes.items() if k in {'name','rice_type','reorder_level_kg'} and (v is not None or k == 'reorder_level_kg')}
    if 'name' in allowed:
        allowed['name'] = allowed['name'].strip()
    if 'rice_type' in allowed:
        allowed['rice_type'] = allowed['rice_type'].strip() or 'Other'
    if not allowed:
        return get_brand(brand_id, path)
    try:
        with session(path) as conn:
            cur = conn.execute('UPDATE brands SET '+', '.join(f'{k}=?' for k in allowed)+' WHERE id=?', [*allowed.values(), brand_id])
            if cur.rowcount == 0:
                return None
    except sqlite3.IntegrityError as exc:
        raise ValueError("You already have a brand with this name.") from exc
    return get_brand(brand_id, path)

def delete_brand(brand_id: str, path: str | None = None) -> bool:
    initialize(path)
    with session(path) as conn:
        cur = conn.execute("DELETE FROM brands WHERE id=?", (brand_id,))
        return cur.rowcount > 0

def list_movements(limit: int = 50, path: str | None = None) -> list[dict]:
    initialize(path)
    with session(path) as conn:
        rows = conn.execute("SELECT id,brand_id,kind,kg,date,note FROM movements ORDER BY date DESC,rowid DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]

def add_movement(brand_id: str, kind: str, kg: float, movement_date: str, note: str = '', path: str | None = None) -> dict:
    initialize(path)
    movement_id = str(uuid.uuid4())
    with session(path) as conn:
        brand = conn.execute("SELECT opening_stock_kg FROM brands WHERE id=?", (brand_id,)).fetchone()
        if not brand:
            raise KeyError("Brand not found.")
        current = float(brand['opening_stock_kg']) + float(conn.execute("SELECT COALESCE(SUM(CASE WHEN kind='in' THEN kg ELSE -kg END),0) FROM movements WHERE brand_id=?", (brand_id,)).fetchone()[0])
        if kind == 'out' and float(kg) > current:
            raise ValueError("There is not enough stock for that amount.")
        conn.execute("INSERT INTO movements(id,brand_id,kind,kg,date,note) VALUES(?,?,?,?,?,?)", (movement_id,brand_id,kind,float(kg),movement_date,note))
    return dict(id=movement_id,brand_id=brand_id,kind=kind,kg=float(kg),date=movement_date,note=note)

def replace_all(brands: Iterable[dict], movements: Iterable[dict], path: str | None = None) -> None:
    initialize(path)
    with session(path) as conn:
        conn.execute('DELETE FROM movements')
        conn.execute('DELETE FROM brands')
        for b in brands:
            conn.execute("INSERT INTO brands(id,name,rice_type,opening_stock_kg,reorder_level_kg,created) VALUES(?,?,?,?,?,?)", (b['id'],b['name'],b['rice_type'],b['stock_kg'],b.get('reorder_level_kg'),b['created']))
        for m in movements:
            conn.execute("INSERT INTO movements(id,brand_id,kind,kg,date,note) VALUES(?,?,?,?,?,?)", (m['id'],m['brand_id'],m['kind'],m['kg'],m['date'],m.get('note','')))
