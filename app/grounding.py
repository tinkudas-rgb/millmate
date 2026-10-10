"""Cite-or-drop: every number in AI text must match a known fact (within rounding)."""
import re

BANNED = re.compile(
    r"\b(forecasts?|models?|algorithms?|apis?|csv|databases?|datasets?|anomal(?:y|ies)|kpis?|"
    r"regressions?|tokens?|confidence intervals?|yoy|wow)\b", re.I)
NUM = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
SCALED = re.compile(r"^\s*(?:k\b|m\b|lakhs?\b|crores?\b|thousand\b|million\b)", re.I)
WINDOW = re.compile(r"^\s*-?\s*days?\b", re.I)


class Pool:
    """Everything the AI is allowed to quote: fact_id -> number or string, plus brand names."""

    def __init__(self, facts=None):
        self.values: dict = {}
        self.names: list[str] = []
        if facts is not None:
            self.add_facts(facts)

    def add(self, fid: str, value):
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float, str)):
            self.values.setdefault(fid, value)

    def add_facts(self, facts):
        for k, v in facts.fact_ids.items():
            self.add(k, v)
        self.add("total_stock_kg", facts.total_stock_kg)
        self.add("running_out_count", len(facts.running_out))
        self.add("piling_up_count", len(facts.piling_up))
        self.add("brand_count", len(facts.brands))
        for b in facts.brands:
            self.names.append(b.name)
            for f in ("stock_kg", "avg_daily_out_kg", "days_left", "days_left_low", "days_left_high",
                      "weeks_of_stock", "out_last_7d_kg", "out_prior_7d_kg", "change_pct"):
                self.add(f"{b.name}.{f}", getattr(b, f))

    def numbers(self) -> list[float]:
        return [float(v) for v in self.values.values() if isinstance(v, (int, float))]

    def strings(self) -> set:
        return {v for v in self.values.values() if isinstance(v, str)}


def _dec(s: str) -> int:
    return len(s.split(".")[1]) if "." in s else 0


def _matches(s: str, candidates) -> bool:
    t = float(s.replace(",", ""))
    tol = 0.5 * 10 ** -_dec(s) + 1e-9
    return any(abs(t - abs(c)) <= tol for c in candidates)


def _numbers_ok(text: str, pool: Pool, candidates, strings) -> bool:
    for n in pool.names:
        if n:
            text = re.sub(re.escape(n), " ", text, flags=re.I)
    for d in ISO.findall(text):
        if d not in strings:
            return False
    text = ISO.sub(" ", text)
    for m in NUM.finditer(text):
        s = m.group(0)
        rest = text[m.end():]
        if SCALED.match(rest):
            return False
        if s in ("7", "14") and WINDOW.match(rest):
            continue
        if not _matches(s, candidates):
            return False
    return True


def text_ok(text: str, pool: Pool) -> bool:
    return not BANNED.search(text) and _numbers_ok(text, pool, pool.numbers(), pool.strings())


def claim_ok(text: str, fact_id: str, pool: Pool) -> bool:
    """The claim must contain the cited fact and no other number."""
    if fact_id not in pool.values or BANNED.search(text):
        return False
    v = pool.values[fact_id]
    if isinstance(v, str):
        return v in text and _numbers_ok(text.replace(v, " "), pool, [], set())
    if not _numbers_ok(text, pool, [float(v)], set()):
        return False
    probe = text
    for n in pool.names:
        if n:
            probe = re.sub(re.escape(n), " ", probe, flags=re.I)
    return bool(NUM.search(ISO.sub(" ", probe)))


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def kg(x: float) -> str:
    return f"{x:,.0f}"


def find_brands(question: str, names: list[str]) -> list[str]:
    """Brand names the question refers to: full name first, else a distinctive word (4+ letters) used by one brand."""
    q = question.lower()
    hits = [n for n in sorted(names, key=len, reverse=True) if n and n.lower() in q]
    if hits:
        return hits
    words = set(re.findall(r"[a-z]{4,}", q))
    out = []
    for n in names:
        mine = {w for w in re.findall(r"[a-z]{4,}", n.lower())}
        others = set()
        for m in names:
            if m != n:
                others |= set(re.findall(r"[a-z]{4,}", m.lower()))
        if (mine - others) & words:
            out.append(n)
    return out


def merge(kept: list[str], template: list[str], names: list[str]) -> list[str]:
    """Keep verified sentences, then add template sentences about brands the kept text did not cover."""
    covered = " ".join(kept).lower()
    out = list(kept)
    for t in template:
        mentioned = [n for n in names if n and n.lower() in t.lower()]
        if (mentioned and not any(n.lower() in covered for n in mentioned)) or (not mentioned and not kept):
            out.append(t)
    return out
