"""Rice mill stock advisor web service."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.errors import FriendlyException
from app.schemas import Advice, Brand, BrandCreate, BrandPatch, ChatAnswer, ChatRequest, Facts, FriendlyError, MovementCreate, State

app = FastAPI(title="Rice Mill Stock Advisor", docs_url=None, redoc_url=None)
_STATIC = Path(__file__).resolve().parent.parent / "static"
if _STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
_advice_cache: dict[str, Advice] = {}

@app.on_event("startup")
def startup() -> None:
    db.initialize()
    from . import tracing
    tracing.init()

@app.exception_handler(FriendlyException)
async def friendly_exception_handler(request: Request, exc: FriendlyException):
    return JSONResponse(status_code=422, content=FriendlyError(message=exc.message, fix=exc.fix).dict())

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"message":"I could not use those details.","fix":"Check the fields and try again."})

@app.exception_handler(Exception)
async def safe_exception_handler(request: Request, exc: Exception):
    # Keep internal error details out of the mill owner's view.
    return JSONResponse(status_code=500, content={"message":"Something went wrong while saving that.","fix":"Try again. If it keeps happening, reload the page."})

@app.get("/", include_in_schema=False)
def home():
    _run_watch()
    page = _STATIC / "index.html"
    if not page.exists():
        raise FriendlyException("The mill page is not ready yet.", "Try again in a little while.")
    return FileResponse(page)

@app.get("/healthz")
def health():
    return {"ok": True}

def _today() -> date:
    return date.today()

def _clean_brand_rows() -> list[dict[str, Any]]:
    return [Brand(**row).dict() for row in db.list_brands()]

def _movement_rows(limit: int = 50) -> list[dict[str, Any]]:
    return db.list_movements(limit=limit)

def _facts(brands: list[dict], movements: list[dict], today: date) -> Facts:
    from app.analytics import analyze
    return analyze(brands, movements, today)

def _state_dict() -> dict[str, Any]:
    brands = _clean_brand_rows()
    movements = _movement_rows(50)
    today = _today()
    facts = _facts(brands, movements, today)
    return State(brands=brands, movements=movements, facts=facts).dict()

def _signature(brands: list[dict], movements: list[dict], today: date) -> str:
    raw = json.dumps([brands, movements, today.isoformat()], sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()

def _run_watch(refresh: bool = False) -> Advice:
    brands = _clean_brand_rows()
    movements = db.list_movements(limit=10000)
    today = _today()
    key = _signature(brands, movements, today)
    if not refresh and key in _advice_cache:
        return _advice_cache[key]
    from app.agent import run_watch
    answer = run_watch(brands, movements, today)
    if not isinstance(answer, Advice):
        answer = Advice.parse_obj(answer)
    # One current result is enough; old data signatures cannot be reused.
    _advice_cache.clear()
    _advice_cache[key] = answer
    return answer

def _after_change() -> None:
    _advice_cache.clear()
    _run_watch(refresh=True)

@app.post("/api/brands", response_model=Brand)
def add_brand(payload: BrandCreate):
    name = payload.name.strip()
    if not name:
        raise FriendlyException("A brand name is needed.", "Enter the name printed on the rice bag.")
    try:
        row = db.create_brand(name, payload.rice_type.strip() or "Other", payload.stock_kg, payload.reorder_level_kg)
    except ValueError as exc:
        raise FriendlyException("You already have a brand with this name.", "Use a different name, for example add the pack size.") from exc
    _after_change()
    return Brand(**row)

@app.patch("/api/brands/{brand_id}", response_model=Brand)
def edit_brand(brand_id: str, payload: BrandPatch):
    changes = payload.dict(exclude_unset=True)
    for key in ("name", "rice_type"):
        if key in changes and changes[key] is not None:
            changes[key] = changes[key].strip()
            if not changes[key]:
                raise FriendlyException("That detail cannot be blank.", "Enter a name or rice type.")
    try:
        row = db.update_brand(brand_id, changes)
    except ValueError as exc:
        raise FriendlyException("You already have a brand with this name.", "Use a different name, for example add the pack size.") from exc
    if row is None:
        raise FriendlyException("I could not find that brand.", "Reload the page and choose a brand from the list.")
    _after_change()
    return Brand(**row)

@app.delete("/api/brands/{brand_id}")
def remove_brand(brand_id: str):
    if not db.delete_brand(brand_id):
        raise FriendlyException("I could not find that brand.", "Reload the page and choose a brand from the list.")
    _after_change()
    return {"ok": True}

@app.post("/api/movements", response_model=Brand)
def record_movement(payload: MovementCreate):
    movement_date = payload.date or _today().isoformat()
    try:
        datetime.strptime(movement_date, "%Y-%m-%d")
    except ValueError as exc:
        raise FriendlyException("That date does not look right.", "Use year-month-day, such as 2026-10-10.") from exc
    if payload.kg <= 0:
        raise FriendlyException("The amount must be above zero.", "Enter the amount of rice that came in or went out.")
    try:
        db.add_movement(payload.brand_id, payload.kind, payload.kg, movement_date, payload.note.strip())
    except KeyError as exc:
        raise FriendlyException("I could not find that brand.", "Reload the page and choose a brand from the list.") from exc
    except ValueError as exc:
        raise FriendlyException(str(exc), "Check the amount against the stock shown, then try again.") from exc
    _after_change()
    row = db.get_brand(payload.brand_id)
    return Brand(**row)

@app.get("/api/state", response_model=State)
def get_state():
    return _state_dict()

@app.get("/api/advice", response_model=Advice)
def get_advice():
    return _run_watch()

@app.post("/api/chat", response_model=ChatAnswer)
def ask_question(payload: ChatRequest):
    question = payload.question.strip()
    if not question:
        raise FriendlyException("I did not get a question.", "Type a question about your rice stock.")
    brands = _clean_brand_rows()
    movements = db.list_movements(limit=10000)
    from app.agent import run_chat
    result = run_chat(question, brands, movements, _today())
    return result if isinstance(result, ChatAnswer) else ChatAnswer.parse_obj(result)

@app.post("/api/demo", response_model=State)
def load_demo():
    from app.demo_data import build_demo
    brands, movements = build_demo(_today())
    db.replace_all(brands, movements)
    _after_change()
    return _state_dict()
