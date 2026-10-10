"""answer(question, facts) -> ChatAnswer. Grounded only in computed facts and tool results; cite-or-drop."""
import json
import logging
import re

from . import llm
from .explain import _days_text, _parse
from .grounding import Pool, claim_ok, covered_ok, find_brands, kg, merge, sentences, text_ok
from .schemas import ChatAnswer, Claim

log = logging.getLogger(__name__)

SYSTEM = (
    "You answer a rice mill owner's question in plain, short English (2-4 sentences). "
    "Use ONLY the numbers in FACTS (and numbers the owner typed). Never calculate or invent a number, and never "
    "promise what a price change or discount will do to sales; if the facts cannot answer, say so and say what the "
    "numbers do show. Write numbers as digits with no 'k', 'lakh' or 'crore'. Do not mention dates. "
    "Say 'sold per day' for daily sales. When a brand has days_left_low and days_left_high, say 'about LOW to HIGH days' (same as the screen), not one number. For 'how much do I need' questions use the needed.* facts, never your own sums. "
    "You cannot send, order or request anything; at most say a note is ready for the owner to copy. "
    "Never use these words: forecast, model, algorithm, API, CSV, database, dataset, anomaly, KPI, regression, token. "
    'Reply as JSON: {"answer": str, "claims": [{"text": str (one sentence containing exactly one number), '
    '"fact_id": str (a key from FACTS)}]}. Every number in the answer must also be in a claim.'
)
KNOWN = re.compile(r"run|out|last|order|buy|restock|stock|pil|slow|much|many|need|enough|day|week|month|kg|sell|sold|"
                   r"trend|what if|discount|price|total|which|brand|low|left|fast|rice|how|why|when|what|better|worst|best|"
                   r"reorder|load|supply|hello|hi\b|help", re.I)
UNCLEAR = ("I did not understand that. Try asking about reordering, or how long a brand will last, "
           "for example: which brand runs out first?")
LIMIT = "I can only answer from the stock and sales you have entered."


def unclear(question: str, facts) -> bool:
    return not find_brands(question, [b.name for b in facts.brands]) and not KNOWN.search(question)


def _template(question: str, facts, pool: Pool):
    q = question.lower()
    names = [b.name for b in facts.brands]
    byname = {b.name: b for b in facts.brands}
    mentioned = find_brands(question, names)
    if not mentioned:  # what-if without a brand: cover the brand closest to running out first
        mentioned = sorted((n for n in names if f"projection.{n}.extra_out_per_day" in pool.values),
                           key=lambda n: (byname[n].days_left is None, byname[n].days_left or 0))
    sents: list[str] = []
    claims: list[Claim] = []

    def add(text, fid):
        if fid in pool.values:
            claims.append(Claim(text=text, fact_id=fid))

    for n in mentioned[:2]:
        b = byname[n]
        proj = f"projection.{n}.days_left"
        nk = f"needed.{n}.need_kg"
        if nk in pool.values:
            nd = pool.values[f"needed.{n}.days"]
            short = pool.values[f"needed.{n}.shortfall_kg"]
            sents.append(f"For {nd:g} days you need about {kg(pool.values[nk])} kg.")
            add(f"{n} needs about {kg(pool.values[nk])} kg for {nd:g} days.", nk)
            if short > 0:
                sents.append(f"You have {kg(b.stock_kg)} kg, so about {kg(short)} kg more.")
                add(f"{n} needs about {kg(short)} kg more.", f"needed.{n}.shortfall_kg")
            else:
                sents.append(f"You have {kg(b.stock_kg)} kg, which already covers it.")
            add(f"{n} has {kg(b.stock_kg)} kg in stock.", f"{n}.stock_kg")
            rate = b.avg_daily_out_kg
            if rate is not None:
                sents.append(f"That is based on {rate:,.1f} kg sold per day.")
                add(f"{n} sells {rate:,.1f} kg per day.", f"{n}.avg_daily_out_kg")
        elif proj in pool.values and f"projection.{n}.extra_out_per_day" in pool.values:
            ex = pool.values[f"projection.{n}.extra_out_per_day"]
            exs = f"{ex:g}"
            s = f"If {exs} kg more goes out each day, {n} would last about {pool.values[proj]} days"
            if b.days_left is not None:
                s += f", instead of {_days_text(b)[0]} now"
            sents.append(s + ".")
            add(f"{n} would last about {pool.values[proj]} days.", proj)
            if b.days_left is not None:
                for t, k in _days_text(b)[1]:
                    add(t, k)
        elif b.status == "no_data":
            sents.append(f"I do not know how fast {n} sells yet. Tell me what you sold for a few days and I will work it out.")
        else:
            sents.append(f"{n} has {kg(b.stock_kg)} kg in stock.")
            add(f"{n} has {kg(b.stock_kg)} kg in stock.", f"{n}.stock_kg")
            if b.days_left is not None:
                ph, dc = _days_text(b)
                sents.append(f"At the current pace it lasts {ph}.")
                for t, k in dc:
                    add(t, k)
            if f"trend.{n}.total_out_kg" in pool.values:
                t = pool.values[f"trend.{n}.total_out_kg"]
                d = pool.values.get(f"trend.{n}.days")
                sents.append(f"{kg(t)} kg went out in the last {d:g} days.")
                add(f"{n} went out {kg(t)} kg in the last {d:g} days.", f"trend.{n}.total_out_kg")
        rk = f"reorder.{n}.kg"
        if rk in pool.values:
            sents.append(f"A fair order for {n} is about {kg(pool.values[rk])} kg.")
            add(f"A fair order for {n} is about {kg(pool.values[rk])} kg.", rk)
            txt = pool.values.get(f"reorder.{n}.text")
            if isinstance(txt, str):
                sents.append("Here is a note you can copy: " + txt)
        if re.search(r"discount|price|offer|cheaper|sale", q) and f"projection.{n}.extra_out_per_day" not in pool.values:
            sents.append("I cannot tell what a discount would do to your sales. "
                         "If you tell me how many extra kg a day you expect to sell, I can show how long the stock would last.")
    if not mentioned:
        if re.search(r"discount|price|offer|cheaper", q):
            sents.append("Tell me which brand you mean, and how many extra kg a day you expect to sell, "
                         "and I will show how long it would last. I cannot tell what a discount does to sales.")
        elif re.search(r"piling|too much|slow|extra stock|dead", q) and facts.piling_up:
            for n in facts.piling_up[:3]:
                w = byname[n].weeks_of_stock if n in byname else None
                if w is not None:
                    sents.append(f"{n} is piling up, with about {w:.1f} weeks of stock.")
                    add(f"{n} has about {w:.1f} weeks of stock.", f"{n}.weeks_of_stock")
        elif facts.running_out:
            low = sorted((byname[n] for n in facts.running_out if n in byname),
                         key=lambda b: (b.days_left is None, b.days_left or 0))
            for b in low[:3]:
                if b.days_left is not None:
                    ph, dc = _days_text(b)
                    sents.append(f"{b.name} has {kg(b.stock_kg)} kg left, {ph}.")
                    for t, k in dc:
                        add(t, k)
                else:
                    sents.append(f"{b.name} has {kg(b.stock_kg)} kg left, which is at or below your reorder level.")
                    add(f"{b.name} has {kg(b.stock_kg)} kg left.", f"{b.name}.stock_kg")
            for k, v in pool.values.items():
                if k.startswith("reorder.") and k.endswith(".text") and isinstance(v, str):
                    sents.append("Here is a note you can copy: " + v)
                    break
        elif facts.brands:
            sents.append("Nothing is running low right now.")
            sents.append(f"In all you have {kg(facts.total_stock_kg)} kg in stock.")
            add(f"In all you have {kg(facts.total_stock_kg)} kg in stock.", "total_stock_kg")
        else:
            sents.append("You have not added any brands yet. Add a brand and its stock, and I can start helping.")
    if not sents:
        sents.append(LIMIT)
    return sents, claims


def answer(question: str, facts, extra: dict | None = None, notes: list[str] | None = None,
           timeout: float = 20.0) -> ChatAnswer:
    """extra: tool-produced numbers (fact_id -> value); notes: plain tool summaries."""
    pool = Pool(facts)
    for k, v in (extra or {}).items():
        pool.add(k, v)
    for i, s in enumerate(re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", question)):
        pool.add(f"question.number_{i + 1}", float(s.replace(",", "")))  # numbers the owner typed
    if unclear(question, facts):
        return ChatAnswer(answer=UNCLEAR, claims=[], used_ai=False)
    tsents, tclaims = _template(question, facts, pool)
    template = ChatAnswer(answer=" ".join(tsents), claims=tclaims, used_ai=False)
    if re.search(r"what if|percent|%", question.lower()) and any(k.endswith(".extra_out_per_day") for k in pool.values):
        return template  # a what-if with a worked number: use the checked answer, not a rewrite
    if not llm.available():
        return template
    try:
        user = f"QUESTION: {question}\nFACTS:\n" + json.dumps({"facts": pool.values}, default=str)
        if notes:
            user += "\nWHAT I CHECKED:\n" + "\n".join(notes)
        d = _parse(llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                            json=True, timeout=timeout)["content"])
        sents = sentences(str(d.get("answer", "")))
        claims = []
        for c in d.get("claims", []) if isinstance(d.get("claims"), list) else []:
            try:
                t, f = str(c["text"]).strip(), str(c["fact_id"])
            except (KeyError, TypeError):
                continue
            if claim_ok(t, f, pool):
                claims.append(Claim(text=t, fact_id=f))
        good = [x for x in sents if text_ok(x, pool) and covered_ok(x, pool, claims)]
        if not sents or len(good) * 2 < len(sents):
            return template
        clean = len(good) == len(sents)
        if not clean:
            good = merge(good, tsents, [b.name for b in facts.brands])
        if not claims and re.search(r"\d", " ".join(good)):
            return template
        return ChatAnswer(answer=" ".join(good), claims=claims, used_ai=clean)
    except llm.LLMUnavailable:
        pass
    except Exception:  # noqa: BLE001
        log.exception("chat writing failed")
    return template
