"""Rice Mill Stock Advisor - data shapes. Frozen contract v2.1. Written fresh on 2026-10-10.
All user-facing strings: plain English, no tech words."""
from typing import Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field

Number = Union[int, float]


class Brand(BaseModel):
    id: str
    name: str
    rice_type: str = "Other"            # e.g. Basmati, Non-basmati, Parboiled, Raw, Other (free text allowed)
    stock_kg: float                      # always derived: opening stock + ins - outs
    reorder_level_kg: Optional[float] = None
    created: str                         # YYYY-MM-DD


class Movement(BaseModel):
    id: str
    brand_id: str
    kind: Literal["in", "out"]           # out = sold/dispatched, in = new load received
    kg: float                            # always > 0
    date: str                            # YYYY-MM-DD
    note: str = ""


class BrandFacts(BaseModel):
    brand_id: str
    name: str
    rice_type: str
    stock_kg: float
    avg_daily_out_kg: Optional[float] = None
    days_left: Optional[int] = None
    days_left_low: Optional[int] = None      # faster of the two rates
    days_left_high: Optional[int] = None     # slower rate; None when the 7-day rate is zero (no finite upper bound)
    weeks_of_stock: Optional[float] = None
    status: Literal["running_out", "ok", "piling_up", "no_data"]
    out_last_7d_kg: float = 0.0
    out_prior_7d_kg: float = 0.0
    change_pct: Optional[float] = None


class Facts(BaseModel):
    as_of: str
    brands: List[BrandFacts]
    total_stock_kg: float
    running_out: List[str] = []              # brand names
    piling_up: List[str] = []                # brand names
    data_notes: List[str] = []
    # Flat map of every number the AI may quote. Keys: "<brand name>.<field>" e.g. "India Gate Basmati.days_left",
    # plus "total_stock_kg". Tool outputs may add keys at run time.
    fact_ids: Dict[str, Union[float, int, str]]


class Claim(BaseModel):
    text: str
    fact_id: str


class Story(BaseModel):
    headline: str
    summary: str
    claims: List[Claim] = []


class Action(BaseModel):
    title: str
    why: str
    expected_effect: str
    proof: List[str] = []                    # "show me the numbers" lines
    draft_text: Optional[str] = None         # plain reorder note the owner can copy; filled by the agent (draft_reorder), never sent


class AgentStep(BaseModel):
    step: int
    tool: str
    args: Dict[str, Union[str, int, float, bool, None]] = {}
    result_summary: str                      # plain words


class Advice(BaseModel):
    story: Story
    actions: List[Action]                    # exactly 3 when any brand has data, else 1 ("Tell me what you sold so I can learn")
    used_ai: bool
    agent_steps: List[AgentStep] = []
    as_of: str = ""


class ChatRequest(BaseModel):
    question: str


class ChatAnswer(BaseModel):
    answer: str
    claims: List[Claim] = []
    used_ai: bool
    agent_steps: List[AgentStep] = []


class FriendlyError(BaseModel):
    message: str
    fix: str


# ---- request bodies ----
class BrandCreate(BaseModel):
    name: str
    rice_type: str = "Other"
    stock_kg: float = Field(ge=0)
    reorder_level_kg: Optional[float] = None


class BrandPatch(BaseModel):
    name: Optional[str] = None
    rice_type: Optional[str] = None
    reorder_level_kg: Optional[float] = None


class MovementCreate(BaseModel):
    brand_id: str
    kind: Literal["in", "out"]
    kg: float = Field(gt=0)
    date: Optional[str] = None               # defaults to today
    note: str = ""


class State(BaseModel):
    brands: List[Brand]
    movements: List[Movement]                # last 50, newest first
    facts: Facts
