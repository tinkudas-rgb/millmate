import json
from datetime import date, timedelta
import pytest
from app import agent, chat, explain, grounding, llm, tracing
from app.errors import FriendlyException
from app.grounding import BANNED, Pool, claim_ok, text_ok
from app.schemas import Brand, Movement

TODAY = date(2026, 10, 10)


def mill():
    brands = [Brand(id="b1", name="India Gate Basmati", rice_type="Basmati", stock_kg=420, created="2026-09-01"),
              Brand(id="b2", name="Sona Masoori", rice_type="Raw", stock_kg=3000, created="2026-09-01"),
              Brand(id="b3", name="Idli Rice", rice_type="Parboiled", stock_kg=100, created="2026-09-01")]
    mv = []
    for i in range(14):
        d = (TODAY - timedelta(days=i)).isoformat()
        mv.append(Movement(id=f"a{i}", brand_id="b1", kind="out", kg=60, date=d))
        mv.append(Movement(id=f"s{i}", brand_id="b2", kind="out", kg=20, date=d))
    return brands, mv


@pytest.fixture(autouse=True)
def nokey(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("NEATLOGS_API_KEY", raising=False)


def keyed(monkeypatch, replies):
    """Pretend a key exists; llm.chat returns the queued replies in order."""
    monkeypatch.setenv("LLM_API_KEY", "x")
    q = list(replies)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: q.pop(0))
    return q


def text(d):
    return {"content": json.dumps(d), "tool_calls": []}


def call(_tool, **args):
    return {"content": "", "tool_calls": [{"id": "c1", "name": _tool, "arguments": json.dumps(args)}]}


def test_watch_no_key_rule_planner():
    b, m = mill()
    adv = agent.run_watch(b, m, TODAY)
    assert adv.used_ai is False and len(adv.actions) == 3 and adv.as_of == "2026-10-10"
    tools = [s.tool for s in adv.agent_steps]
    assert tools[0] == "get_state" and "compare_brands" in tools and "draft_reorder" in tools
    assert [s.step for s in adv.agent_steps] == list(range(1, len(tools) + 1)) and len(tools) <= 6
    assert adv.actions[0].draft_text and "India Gate Basmati" in adv.actions[0].draft_text
    assert not BANNED.search(adv.story.summary + adv.story.headline)
    assert adv.story.claims and all(c.fact_id for c in adv.story.claims)


def test_template_claims_all_verify():
    b, m = mill()
    from app import analytics
    f = analytics.analyze(b, m, TODAY)
    s, ai = explain.explain(f)
    p = Pool(f)
    assert ai is False and text_ok(s.summary, p) and text_ok(s.headline, p)
    assert all(claim_ok(c.text, c.fact_id, p) for c in s.claims)


def test_no_brands_and_no_data():
    adv = agent.run_watch([], [], TODAY)
    assert adv.story.claims == [] and adv.used_ai is False
    b, _ = mill()
    assert "what you sold" in agent.run_watch(b, [], TODAY).story.headline


def test_llm_planner_and_writer_ok(monkeypatch):
    b, m = mill()
    keyed(monkeypatch, [call("project_stockout", name="india gate", extra_out_per_day=0), text({"content": ""}) | {"content": "DONE"},
                        text({"headline": "India Gate Basmati will run out first",
                              "summary": "India Gate Basmati has 420 kg left.",
                              "claims": [{"text": "India Gate Basmati has 420 kg left.", "fact_id": "India Gate Basmati.stock_kg"}]})])
    adv = agent.run_watch(b, m, TODAY)
    assert adv.used_ai is True and adv.agent_steps[1].tool == "project_stockout"


def test_llm_invented_number_is_replaced(monkeypatch):
    b, m = mill()
    keyed(monkeypatch, [{"content": "DONE", "tool_calls": []},
                        text({"headline": "India Gate Basmati will run out first",
                              "summary": "India Gate Basmati has 420 kg left. It will be gone in 2 days.",
                              "claims": [{"text": "It has 420 kg.", "fact_id": "India Gate Basmati.stock_kg"}]})])
    adv = agent.run_watch(b, m, TODAY)
    assert adv.used_ai is False and " 2 days" not in adv.story.summary
    assert "420 kg left" in adv.story.summary


def test_llm_mostly_wrong_falls_back_to_template(monkeypatch):
    b, m = mill()
    keyed(monkeypatch, [{"content": "DONE", "tool_calls": []},
                        text({"headline": "Hi", "summary": "It is 99 kg. And 98 kg. Fine.", "claims": []})])
    adv = agent.run_watch(b, m, TODAY)
    assert adv.used_ai is False and "99" not in adv.story.summary


def test_llm_down_uses_rules(monkeypatch):
    b, m = mill()
    monkeypatch.setenv("LLM_API_KEY", "x")
    def boom(*a, **k): raise llm.LLMUnavailable("down")
    monkeypatch.setattr(llm, "chat", boom)
    adv = agent.run_watch(b, m, TODAY)
    assert adv.used_ai is False and [s.tool for s in adv.agent_steps][:2] == ["get_state", "compare_brands"]


def test_step_cap_and_bad_tool_args(monkeypatch):
    b, m = mill()
    calls = [call("get_brand", name="Idli Rice")] * 0 + [call("get_brand", name=f"nope{i}") for i in range(9)]
    keyed(monkeypatch, calls + [{"content": "DONE", "tool_calls": []}] * 3)
    adv = agent.run_watch(b, m, TODAY)
    assert len(adv.agent_steps) <= 6


def test_chat_what_if_and_discount():
    b, m = mill()
    a = agent.run_chat("what if I sell 30 kg more India Gate a day?", b, m, TODAY)
    assert a.used_ai is False and "30 kg more" in a.answer and any(s.tool == "project_stockout" for s in a.agent_steps)
    p = Pool(__import__("app.analytics", fromlist=["x"]).analyze(b, m, TODAY))
    a2 = agent.run_chat("what if I give a discount on Sona?", b, m, TODAY)
    assert "cannot tell what a discount" in a2.answer
    assert not BANNED.search(a.answer + a2.answer)


def test_chat_other_questions():
    b, m = mill()
    assert "India Gate Basmati" in agent.run_chat("which brand runs out first?", b, m, TODAY).answer
    r = agent.run_chat("how much India Gate should I order?", b, m, TODAY)
    assert "note you can copy" in r.answer and "order" in r.answer
    assert "Idli Rice" in agent.run_chat("how is idli doing", b, m, TODAY).answer
    with pytest.raises(FriendlyException):
        agent.run_chat("   ", b, m, TODAY)


def test_chat_llm_guardrail(monkeypatch):
    b, m = mill()
    keyed(monkeypatch, [{"content": "DONE", "tool_calls": []},
                        text({"answer": "Selling 30 more will clear it in 3 days.", "claims": []})])
    assert agent.run_chat("what if I sell 30 kg more India Gate a day?", b, m, TODAY).used_ai is False
    keyed(monkeypatch, [{"content": "DONE", "tool_calls": []},
                        text({"answer": "India Gate Basmati has 420 kg in stock.",
                              "claims": [{"text": "India Gate Basmati has 420 kg in stock.", "fact_id": "India Gate Basmati.stock_kg"}]})])
    assert agent.run_chat("how is india gate?", b, m, TODAY).used_ai is True


def test_grounding_rules():
    b, m = mill()
    from app import analytics
    p = Pool(analytics.analyze(b, m, TODAY))
    assert text_ok("India Gate Basmati has 420 kg.", p) and text_ok("About 420.4 kg.", p) is False
    assert not text_ok("It has 1.2k kg.", p) and not text_ok("It has 421 lakh.", p)
    assert not text_ok("Our forecast says 420 kg.", p)
    assert text_ok("Over the last 7 days it sold 420 kg.", p)
    assert not text_ok("Gone by 2026-10-15.", p)
    assert not claim_ok("It has 420 kg and 8 days.", "India Gate Basmati.stock_kg", p)
    assert not claim_ok("It has 420 kg.", "Sona Masoori.stock_kg", p)
    assert not claim_ok("It has 420 kg.", "missing.id", p)


def test_tracing_noop_and_tools_never_send():
    assert tracing.init() is False and tracing.enabled() is False
    @tracing.traced("TOOL", "x")
    def f(a): return a + 1
    assert f(1) == 2
    assert set(agent.TOOLS) == {"get_state", "get_brand", "project_stockout", "compare_brands", "usage_trend", "draft_reorder"}


def test_note_attached_to_real_style_title():
    from app.schemas import Action
    b, m = mill()
    from app import analytics
    run = agent.Run(analytics.analyze(b, m, TODAY), m, TODAY)
    acts = [Action(title="Check a new load of India Gate Basmati", why="It runs out in about 7 days.", expected_effect="x"),
            Action(title="Keep logging", why="y", expected_effect="z")]
    agent._attach_notes(run, acts)
    assert "840 kg of India Gate Basmati" in acts[0].draft_text and acts[1].draft_text is None
    acts = [Action(title="Look at India Gate Basmati", why="w", expected_effect="z")]
    agent._attach_notes(run, acts)
    assert acts[0].draft_text


def test_tracing_wrap_noop():
    assert tracing.wrap("c") == "c"
