"""The stock advisor agent: observe -> plan (LLM tool calling, or rules) -> act (read-only tools) -> write
-> verify (cite-or-drop, inside explain/chat) -> report. One run = one trace. Nothing is ever sent or ordered."""
import inspect
import json
import logging
import math
import re
import time
from datetime import date, timedelta

from . import chat as chat_mod
from . import explain as explain_mod
from . import llm
from .errors import FriendlyException
from .grounding import find_brands
from .schemas import Advice, AgentStep, ChatAnswer
from .tracing import traced

log = logging.getLogger(__name__)
MAX_STEPS = 6          # tool calls per run
PLAN_STEPS = 5         # the LLM planner stops here; one is kept for a reorder note
BUDGET_S = 25.0
FIELDS = ("stock_kg", "avg_daily_out_kg", "days_left", "days_left_low", "days_left_high", "weeks_of_stock",
          "out_last_7d_kg", "out_prior_7d_kg", "change_pct")


class Run:
    def __init__(self, facts, movements, today: date):
        self.facts, self.movements, self.today = facts, list(movements or []), today
        self.extra: dict = {}          # numbers produced by tools: fact_id -> value
        self.notes: list[str] = []     # plain summaries of what each tool found
        self.steps: list[AgentStep] = []
        self.cache: dict = {}
        self.deadline = time.monotonic() + BUDGET_S
        self.by_name = {b.name: b for b in facts.brands}

    def left(self) -> float:
        return self.deadline - time.monotonic()

    def find(self, name):
        name = str(name or "").strip().lower()
        for n, b in self.by_name.items():
            if n.lower() == name:
                return b
        hits = [b for n, b in self.by_name.items() if name and (name in n.lower() or n.lower() in name)]
        return hits[0] if len(hits) == 1 else None

    def call(self, tool: str, **args) -> dict:
        fn = TOOLS.get(tool)
        if fn is None:
            return {"ok": False, "summary": "I do not have that check."}
        keep = set(inspect.signature(fn).parameters) - {"run"}
        args = {k: v for k, v in args.items() if k in keep}
        key = (tool, json.dumps(args, sort_keys=True, default=str))
        if key in self.cache:
            return self.cache[key]
        if len(self.steps) >= MAX_STEPS:
            return {"ok": False, "summary": "I have already done enough checks for one answer."}
        try:
            res = fn(self, **args)
        except Exception:  # noqa: BLE001  a failing tool must not stop the run
            log.exception("tool %s failed", tool)
            res = {"ok": False, "summary": "I could not check that."}
        for k, v in (res.get("facts") or {}).items():
            if isinstance(v, (int, float, str)) and not isinstance(v, bool):
                self.extra.setdefault(k, v)
        self.notes.append(res.get("summary", ""))
        prim = {k: v for k, v in args.items() if isinstance(v, (str, int, float, bool)) or v is None}
        self.steps.append(AgentStep(step=len(self.steps) + 1, tool=tool, args=prim,
                                    result_summary=res.get("summary", "")))
        self.cache[key] = res
        return res


# ---------------- tools (read-only; draft_reorder only writes a note, never sends) ----------------
def _missing(name):
    return {"ok": False, "summary": f"I could not find a brand called {name}."}


@traced("TOOL", "get_state")
def get_state(run: Run) -> dict:
    f = run.facts
    n_data = sum(1 for b in f.brands if b.status != "no_data")
    s = f"Looked at {len(f.brands)} brands with {n_data} having sales history."
    if f.brands:
        s += f" {len(f.running_out)} running out, {len(f.piling_up)} piling up."
    return {"ok": True, "summary": s, "brands": [{"name": b.name, "status": b.status} for b in f.brands],
            "facts": {k: v for k, v in f.fact_ids.items()}}


@traced("TOOL", "get_brand")
def get_brand(run: Run, name: str) -> dict:
    b = run.find(name)
    if not b:
        return _missing(name)
    facts = {f"{b.name}.{f}": getattr(b, f) for f in FIELDS if getattr(b, f) is not None}
    s = f"{b.name}: {b.stock_kg:,.0f} kg in stock"
    s += f", about {b.days_left} days left." if b.days_left is not None else ", not enough sales yet to say how long it lasts."
    return {"ok": True, "summary": s, "status": b.status, "facts": facts}


@traced("TOOL", "project_stockout")
def project_stockout(run: Run, name: str, extra_out_per_day: float = 0) -> dict:
    b = run.find(name)
    if not b:
        return _missing(name)
    try:
        extra = min(max(float(extra_out_per_day or 0), 0.0), 1e6)
    except (TypeError, ValueError):
        extra = 0.0
    rate = b.avg_daily_out_kg
    if rate is None or rate + extra <= 0:
        return {"ok": False, "summary": f"I do not have enough sales for {b.name} to say when it runs out."}
    days = b.days_left if (extra == 0 and b.days_left is not None) else int(math.floor(b.stock_kg / (rate + extra)))
    when = (run.today + timedelta(days=days)).isoformat()
    facts = {f"projection.{b.name}.days_left": days, f"projection.{b.name}.date": when}
    if extra:
        facts[f"projection.{b.name}.extra_out_per_day"] = round(extra, 1)
        s = f"If {extra:g} kg more goes out each day, {b.name} lasts about {days} days."
    else:
        s = f"At the usual pace, {b.name} lasts about {days} days."
    return {"ok": True, "summary": s, "days_left": days, "date": when, "facts": facts}


@traced("TOOL", "compare_brands")
def compare_brands(run: Run) -> dict:
    bs = sorted(run.facts.brands, key=lambda b: (b.days_left is None, b.days_left if b.days_left is not None else 0))
    rank = [{"rank": i + 1, "name": b.name, "status": b.status,
             "days_left_fact": f"{b.name}.days_left" if b.days_left is not None else None} for i, b in enumerate(bs)]
    if not bs:
        return {"ok": True, "summary": "There are no brands to compare yet.", "ranking": []}
    with_days = [b for b in bs if b.days_left is not None]
    s = (f"Soonest to run out: {with_days[0].name}, about {with_days[0].days_left} days." if with_days
         else "Not enough sales yet to rank the brands.")
    return {"ok": True, "summary": s, "ranking": rank, "facts": {}}


@traced("TOOL", "usage_trend")
def usage_trend(run: Run, name: str, days: int = 14) -> dict:
    b = run.find(name)
    if not b:
        return _missing(name)
    try:
        days = min(max(int(days), 1), 60)
    except (TypeError, ValueError):
        days = 14
    start = run.today - timedelta(days=days - 1)
    series: dict[str, float] = {}
    for m in run.movements:
        if m.brand_id == b.brand_id and m.kind == "out":
            try:
                d = date.fromisoformat(m.date[:10])
            except ValueError:
                continue
            if start <= d <= run.today:
                series[d.isoformat()] = series.get(d.isoformat(), 0.0) + m.kg
    if not series:
        return {"ok": True, "summary": f"No sales of {b.name} in the last {days} days.", "series": {}, "facts": {}}
    total = round(sum(series.values()), 1)
    day, top = max(series.items(), key=lambda kv: kv[1])
    facts = {f"trend.{b.name}.total_out_kg": total, f"trend.{b.name}.days": days,
             f"trend.{b.name}.busiest_day_kg": round(top, 1), f"trend.{b.name}.busiest_day": day}
    return {"ok": True, "summary": f"{b.name}: {total:,.0f} kg went out in the last {days} days.",
            "series": {k: round(v, 1) for k, v in sorted(series.items())}, "facts": facts}


@traced("TOOL", "needed_stock")
def needed_stock(run: Run, name: str, days: int = 30) -> dict:
    b = run.find(name)
    if not b:
        return _missing(name)
    try:
        days = min(max(int(float(days)), 1), 365)
    except (TypeError, ValueError):
        days = 30
    rate = b.avg_daily_out_kg
    if rate is None or rate <= 0:
        return {"ok": False, "summary": f"I do not have enough sales for {b.name} to say how much you need."}
    need = round(rate * days, 1)
    short = round(max(need - b.stock_kg, 0.0), 1)
    facts = {f"needed.{b.name}.days": days, f"needed.{b.name}.need_kg": need, f"needed.{b.name}.shortfall_kg": short}
    s = f"{b.name} needs about {need:,.0f} kg for {days} days; you have {b.stock_kg:,.0f} kg, so about {short:,.0f} kg more."
    return {"ok": True, "summary": s, "facts": facts}


def reorder_data(run: Run, b, kg_hint=None):
    """Order size: enough for 3 weeks at the current pace, rounded up to 10 kg. Computed here, never by the AI."""
    rate = b.avg_daily_out_kg
    if rate and rate > 0:
        need = rate * 21 - b.stock_kg
        qty = need if need > 0 else rate * 14
    else:
        try:
            qty = float(kg_hint)
        except (TypeError, ValueError):
            return None
        if qty <= 0:
            return None
    return max(10, int(math.ceil(qty / 10.0)) * 10)


@traced("TOOL", "draft_reorder")
def draft_reorder(run: Run, name: str, kg: float | None = None) -> dict:
    b = run.find(name)
    if not b:
        return _missing(name)
    qty = reorder_data(run, b, kg)
    if qty is None:
        return {"ok": False, "summary": f"I need some sales of {b.name} before I can suggest how much to order."}
    text = (f"Hello, I would like to order {qty} kg of {b.name}. "
            "Please tell me the price and when you can deliver. Thank you.")
    return {"ok": True, "summary": f"Wrote a note to order {qty} kg of {b.name}. Nothing was sent.", "text": text,
            "facts": {f"reorder.{b.name}.kg": qty, f"reorder.{b.name}.text": text}}


TOOLS = {"get_state": get_state, "get_brand": get_brand, "project_stockout": project_stockout,
         "compare_brands": compare_brands, "needed_stock": needed_stock, "usage_trend": usage_trend, "draft_reorder": draft_reorder}


def _spec(name, desc, props, req):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": req}}}


_N = {"name": {"type": "string", "description": "brand name"}}
TOOL_SPECS = [
    _spec("get_brand", "Stock and sales facts for one brand.", _N, ["name"]),
    _spec("project_stockout", "How many days a brand lasts, optionally if extra kg go out each day.",
          {**_N, "extra_out_per_day": {"type": "number"}}, ["name"]),
    _spec("needed_stock", "How many kg a brand needs over N days at the current pace, and how many more kg than in stock.",
          {**_N, "days": {"type": "integer"}}, ["name"]),
    _spec("compare_brands", "Rank all brands by which runs out first.", {}, []),
    _spec("usage_trend", "Daily kg sold for one brand over recent days.", {**_N, "days": {"type": "integer"}}, ["name"]),
    _spec("draft_reorder", "Write a reorder note for the owner to copy. Never sent.", _N, ["name"]),
]
PLAN_SYSTEM = (
    "You are a careful stock assistant for a rice mill owner. Use the tools to check what you need, then reply with "
    "the single word DONE. Do not write numbers yourself. Only call tools that help; stop early when you know enough."
)


# ---------------- planning ----------------
def _worst(run: Run):
    low = [run.by_name[n] for n in run.facts.running_out if n in run.by_name]
    low.sort(key=lambda b: (b.days_left is None, b.days_left if b.days_left is not None else 0))
    return low[0] if low else None


def _rule_plan_watch(run: Run):
    run.call("compare_brands")
    for n in run.facts.running_out[:3]:
        run.call("project_stockout", name=n)
    w = _worst(run)
    if w:
        run.call("draft_reorder", name=w.name)


def _rule_plan_chat(run: Run, question: str):
    q = question.lower()
    names = [b.name for b in run.facts.brands]
    brands = find_brands(question, names)
    nums = re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", question)
    extra = float(nums[0].replace(",", "")) if nums else 0
    if brands:
        n = brands[0]
        dm = re.search(r"(\d+)\s*(day|week|month)", q)
        if dm and re.search(r"need|enough|require|how (much|many)|last|cover|stock up", q):
            mult = {"day": 1, "week": 7, "month": 30}[dm.group(2)]
            run.call("needed_stock", name=n, days=int(dm.group(1)) * mult)
        elif extra and re.search(r"what if|more|extra|increase|sell|dispatch|per day|a day|daily|discount|offer", q):
            run.call("project_stockout", name=n, extra_out_per_day=extra)
        elif re.search(r"order|buy|need|restock|reload", q):
            run.call("draft_reorder", name=n)
        elif re.search(r"how fast|trend|moving|selling|week|lately|last", q):
            run.call("usage_trend", name=n)
        else:
            run.call("get_brand", name=n)
        run.call("get_brand", name=n)
    else:
        run.call("compare_brands")
        if re.search(r"order|buy|restock", q):
            w = _worst(run)
            if w:
                run.call("draft_reorder", name=w.name)
        elif not re.search(r"piling|too much|slow|extra stock", q):
            for n in run.facts.running_out[:2]:
                run.call("project_stockout", name=n)


def _llm_plan(run: Run, user: str):
    """LLM chooses tools. Raises llm.LLMUnavailable on any failure (caller falls back to rules)."""
    msgs = [{"role": "system", "content": PLAN_SYSTEM},
            {"role": "user", "content": user + "\nBRANDS: " + json.dumps(
                [{"name": b.name, "status": b.status} for b in run.facts.brands])}]
    while len(run.steps) < PLAN_STEPS:
        if run.left() < 8:
            raise llm.LLMUnavailable("out of time")
        reply = llm.chat(msgs, tools=TOOL_SPECS, timeout=run.left() - 5)
        if not reply["tool_calls"]:
            return
        calls_out = []
        for c in reply["tool_calls"]:
            tc = {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
            if c.get("extra_content"):
                tc["extra_content"] = c["extra_content"]   # thought_signature: send back unchanged
            calls_out.append(tc)
        msgs.append({"role": "assistant", "content": reply["content"], "tool_calls": calls_out})
        for c in reply["tool_calls"]:
            try:
                args = json.loads(c["arguments"] or "{}")
                if not isinstance(args, dict):
                    args = {}
            except ValueError:
                args = {}
            if c["name"] in TOOLS and c["name"] != "get_state" and len(run.steps) < PLAN_STEPS:
                res = run.call(c["name"], **args)
            else:
                res = {"ok": False, "summary": "That check is not available."}
            msgs.append({"role": "tool", "tool_call_id": c["id"],
                         "content": json.dumps({k: v for k, v in res.items() if k != "text"}, default=str)})


def _plan(run: Run, user: str, rules):
    if llm.available():
        try:
            _llm_plan(run, user)
            return
        except llm.LLMUnavailable:
            log.info("planner unavailable; using rules")
        except Exception:  # noqa: BLE001
            log.exception("planner failed; using rules")
    rules()


# ---------------- runs ----------------
@traced("WORKFLOW", "watch_run")
def run_watch(brands, movements, today: date) -> Advice:
    from . import analytics, recommend  # owner C
    facts = analytics.analyze(brands, movements, today)
    run = Run(facts, movements, today)
    run.call("get_state")                                   # 1. observe
    _plan(run, "The owner's stock changed or they opened the page. Check what is running out, what is piling up, "
               "and what to reorder.", lambda: _rule_plan_watch(run))   # 2-3. plan + act
    story, used_ai = explain_mod.explain(facts, extra=run.extra, notes=run.notes, timeout=max(2.0, run.left() - 1))  # 4. verify
    actions = recommend.recommend(facts, story)
    _attach_notes(run, actions)
    return Advice(story=story, actions=actions, used_ai=used_ai, agent_steps=run.steps, as_of=facts.as_of)


ORDER_WORDS = re.compile(r"order|reorder|new load|load of|restock|refill|top up|buy|stock up", re.I)


def _attach_notes(run: Run, actions):
    """Put a reorder note on the card(s) about a running-out brand. Works the same for the rule and LLM paths."""
    low = [n for n in run.facts.running_out if n in run.by_name]
    done = False
    for a in actions:
        if a.draft_text:
            done = True
            continue
        hay = f"{a.title} {a.why}"
        if ORDER_WORDS.search(a.title) or (ORDER_WORDS.search(hay) and any(n.lower() in hay.lower() for n in low)):
            a.draft_text = _note_for(run, a)
            done = done or bool(a.draft_text)
    if not done and low:  # no card looks like a reorder card: use the first card about a running-out brand
        for a in actions:
            if any(n.lower() in f"{a.title} {a.why}".lower() for n in low):
                a.draft_text = _note_for(run, a)
                break


def _note_for(run: Run, action):
    hay = f"{action.title} {action.why}".lower()
    name = next((n for n in sorted(run.by_name, key=len, reverse=True) if n.lower() in hay), None)
    b = run.by_name.get(name) if name else _worst(run)
    if not b:
        return None
    res = run.call("draft_reorder", name=b.name)
    if res.get("ok"):
        return res["text"]
    key = f"reorder.{b.name}.text"
    if key in run.extra:
        return run.extra[key]
    qty = reorder_data(run, b)
    if qty is None:
        return None
    return (f"Hello, I would like to order {qty} kg of {b.name}. "
            "Please tell me the price and when you can deliver. Thank you.")


@traced("WORKFLOW", "chat_run")
def run_chat(question: str, brands, movements, today: date) -> ChatAnswer:
    q = (question or "").strip()
    if not q:
        raise FriendlyException("I did not catch a question.",
                                "Type what you want to know, for example: which brand runs out first?")
    q = q[:500]
    from . import analytics  # owner C
    facts = analytics.analyze(brands, movements, today)
    run = Run(facts, movements, today)
    run.call("get_state")
    _plan(run, "The owner asks: " + q, lambda: _rule_plan_chat(run, q))
    ans = chat_mod.answer(q, facts, extra=run.extra, notes=run.notes, timeout=max(2.0, run.left() - 1))
    ans.agent_steps = run.steps
    return ans
