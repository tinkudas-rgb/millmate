"""explain(facts) -> (Story, used_ai). The LLM only phrases computed facts; unverified text is replaced."""
import json
import logging

from . import llm
from .grounding import Pool, claim_ok, kg, merge, sentences, text_ok
from .schemas import Claim, Story

log = logging.getLogger(__name__)

SYSTEM = (
    "You write a short stock update for a rice mill owner in plain, warm, simple English. "
    "Use ONLY the numbers in FACTS. Never calculate, round differently, or invent a number. "
    "Write numbers as digits with no 'k', 'lakh' or 'crore' (say 1200 kg, not 1.2k). Do not mention dates. "
    "Never use these words: forecast, model, algorithm, API, CSV, database, dataset, anomaly, KPI, regression, token. "
    "Say 'about X to Y days' for ranges. "
    'Reply as JSON: {"headline": str, "summary": str (2-4 short sentences), '
    '"claims": [{"text": str (one sentence containing exactly one number), "fact_id": str (a key from FACTS)}]}. '
    "Every number in the headline and summary must also be in a claim."
)


def _days_text(b) -> tuple[str, list[tuple[str, str]]]:
    """(phrase, [(claim sentence, fact key)]) for how long a brand lasts."""
    n = b.name
    lo, hi = b.days_left_low, b.days_left_high
    if lo is not None and hi is not None and lo != hi:
        return (f"about {lo} to {hi} days",
                [(f"{n} could last as little as {lo} days.", f"{n}.days_left_low"),
                 (f"{n} could last as long as {hi} days.", f"{n}.days_left_high")])
    d = b.days_left if b.days_left is not None else lo
    key = f"{n}.days_left" if b.days_left is not None else f"{n}.days_left_low"
    return f"about {d} days", [(f"{n} will last about {d} days.", key)]


def template_story_parts(facts, pool: Pool):
    """Returns (headline, summary sentences, claims). Deterministic; only uses facts."""
    claims: list[Claim] = []
    sents: list[str] = []

    def add(text, fid):
        if fid in pool.values:
            claims.append(Claim(text=text, fact_id=fid))

    data = [b for b in facts.brands if b.status != "no_data"]
    if not facts.brands:
        return ("Add your rice brands to get started",
                ["Add each brand with the kg you have, then tell me what you sold each day."], [])
    if not data:
        return ("I need to see what you sold",
                ["Tell me what you sold each day. After a few days I can say what will run out."], [])
    byname = {b.name: b for b in facts.brands}
    low = [byname[n] for n in facts.running_out if n in byname]
    low.sort(key=lambda b: (b.days_left is None, b.days_left if b.days_left is not None else 0))
    if low:
        first = low[0]
        if len(low) == 1:
            phrase, _ = _days_text(first)
            headline = f"{first.name} will run out first"
        else:
            headline = f"{len(low)} brands are running low"
            add(f"{len(low)} brands are running low.", "running_out_count")
        for b in low[:3]:
            phrase, dc = _days_text(b)
            sents.append(f"{b.name} has {kg(b.stock_kg)} kg left, enough for {phrase}.")
            add(f"{b.name} has {kg(b.stock_kg)} kg left.", f"{b.name}.stock_kg")
            for t, k in dc:
                add(t, k)
        if len(low) == 1:
            # the headline names no number; keep it simple
            pass
    else:
        headline = "Your stock looks fine for now"
    for b in [byname[n] for n in facts.piling_up if n in byname][:2]:
        if b.weeks_of_stock is not None:
            sents.append(f"{b.name} is piling up, with about {b.weeks_of_stock:.1f} weeks of stock.")
            add(f"{b.name} has about {b.weeks_of_stock:.1f} weeks of stock.", f"{b.name}.weeks_of_stock")
    movers = [b for b in data if b.change_pct is not None and abs(b.change_pct) >= 20]
    if movers:
        b = max(movers, key=lambda x: abs(x.change_pct))
        way = "up" if b.change_pct > 0 else "down"
        sents.append(f"Sales of {b.name} are {way} {abs(b.change_pct):.1f}% compared with the week before.")
        add(f"Sales of {b.name} are {way} {abs(b.change_pct):.1f}% compared with the week before.", f"{b.name}.change_pct")
    sents.append(f"In all you have {kg(facts.total_stock_kg)} kg in stock.")
    add(f"In all you have {kg(facts.total_stock_kg)} kg in stock.", "total_stock_kg")
    return headline, sents, claims


def explain(facts, extra: dict | None = None, notes: list[str] | None = None,
            timeout: float = 20.0) -> tuple[Story, bool]:
    """extra: numbers produced by tools during the run (fact_id -> value). notes: plain tool summaries."""
    pool = Pool(facts)
    for k, v in (extra or {}).items():
        pool.add(k, v)
    headline, tsents, tclaims = template_story_parts(facts, pool)
    template = Story(headline=headline, summary=" ".join(tsents), claims=tclaims)
    if not llm.available():
        return template, False
    try:
        user = "FACTS:\n" + json.dumps({"facts": pool.values}, default=str)
        if notes:
            user += "\nWHAT I CHECKED:\n" + "\n".join(notes)
        d = _parse(llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                            json=True, timeout=timeout)["content"])
        head = str(d.get("headline", "")).strip()
        sents = sentences(str(d.get("summary", "")))
        good = [s for s in sents if text_ok(s, pool)]
        if not sents or len(good) * 2 < len(sents):
            log.info("story dropped: too many sentences did not match the facts")
            return template, False
        clean = True
        if not head or not text_ok(head, pool):
            head, clean = headline, False
        if len(good) < len(sents):
            clean = False
            good = merge(good, tsents, [b.name for b in facts.brands])
        claims = []
        for c in d.get("claims", []) if isinstance(d.get("claims"), list) else []:
            try:
                t, f = str(c["text"]).strip(), str(c["fact_id"])
            except (KeyError, TypeError):
                continue
            if claim_ok(t, f, pool):
                claims.append(Claim(text=t, fact_id=f))
        if not claims:
            claims, clean = tclaims, False
        return Story(headline=head, summary=" ".join(good), claims=claims), clean
    except llm.LLMUnavailable:
        pass
    except Exception:  # noqa: BLE001  never surface errors
        log.exception("story writing failed")
    return template, False


def _parse(raw: str) -> dict:
    import re
    m = re.search(r"\{.*\}", raw.strip(), re.S)
    if not m:
        raise ValueError("no json")
    return json.loads(m.group(0))
