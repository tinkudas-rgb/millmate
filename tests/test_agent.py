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
    assert set(agent.TOOLS) == {"get_state", "get_brand", "project_stockout", "compare_brands", "usage_trend", "draft_reorder", "needed_stock"}


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


def test_thought_signature_sent_back(monkeypatch):
    b, m = mill()
    sig = {"google": {"thought_signature": "abc"}}
    seen = []
    replies = [{"content": "", "tool_calls": [{"id": "c1", "name": "get_brand",
                                              "arguments": json.dumps({"name": "Idli Rice"}), "extra_content": sig}]},
               {"content": "DONE", "tool_calls": []}]
    monkeypatch.setenv("LLM_API_KEY", "x")
    def fake(msgs, **k):
        seen.append(json.loads(json.dumps(msgs)))
        return replies.pop(0) if replies else text({"headline": "x", "summary": "y", "claims": []})
    monkeypatch.setattr(llm, "chat", fake)
    agent.run_watch(b, m, TODAY)
    asst = [x for x in seen[1] if x["role"] == "assistant"][0]
    assert asst["tool_calls"][0]["extra_content"] == sig and asst["content"] == ""


def test_llm_chat_keeps_extra_content(monkeypatch):
    from types import SimpleNamespace as N
    tc = N(id="c1", function=N(name="get_brand", arguments="{}"), extra_content={"google": {"thought_signature": "s"}})
    msg = N(content=None, tool_calls=[tc])
    client = N(chat=N(completions=N(create=lambda **k: N(choices=[N(message=msg)]))))
    monkeypatch.setenv("LLM_API_KEY", "x")
    monkeypatch.setattr(llm, "_client", lambda t: client)
    r = llm.chat([{"role": "user", "content": "hi"}])
    assert r["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "s"


def test_bug_a_false_action_claims_dropped(monkeypatch):
    b, m = mill()
    for bad in ["I have sent a request to order 840 kg of India Gate Basmati.", "I have requested a restock.",
                "Your order was placed for India Gate Basmati."]:
        assert not text_ok(bad, Pool(__import__("app.analytics", fromlist=["x"]).analyze(b, m, TODAY)))
    keyed(monkeypatch, [{"content": "DONE", "tool_calls": []},
                        text({"headline": "India Gate Basmati will run out first",
                              "summary": "India Gate Basmati has 420 kg left. I have sent a request to order more.",
                              "claims": [{"text": "India Gate Basmati has 420 kg left.", "fact_id": "India Gate Basmati.stock_kg"}]})])
    adv = agent.run_watch(b, m, TODAY)
    assert "sent" not in adv.story.summary and adv.used_ai is False


def test_bug_b_needed_kg_is_grounded():
    b, m = mill()   # India Gate sells 60 kg a day, 420 in stock
    a = agent.run_chat("How many kg of India Gate Basmati do I need for 30 days?", b, m, TODAY)
    assert any(s.tool == "needed_stock" for s in a.agent_steps)
    assert "1,800 kg" in a.answer and "1,380 kg more" in a.answer and "sold per day" in a.answer
    assert "60.0 kg" in a.answer and "output" not in a.answer
    assert {c.fact_id for c in a.claims} >= {"needed.India Gate Basmati.need_kg", "India Gate Basmati.avg_daily_out_kg"}
    w = agent.run_chat("do I have enough India Gate for 2 weeks?", b, m, TODAY)
    assert "840 kg" in w.answer


def test_bug_b_unseen_number_dropped(monkeypatch):
    b, m = mill()
    # 60 kg per day exists in the facts but the answer must also cite it in a claim, else the sentence is dropped
    keyed(monkeypatch, [{"content": "DONE", "tool_calls": []},
                        text({"answer": "India Gate Basmati has 420 kg in stock. Average daily output is 60 kg.",
                              "claims": [{"text": "India Gate Basmati has 420 kg in stock.", "fact_id": "India Gate Basmati.stock_kg"}]})])
    a = agent.run_chat("how is india gate?", b, m, TODAY)
    assert "output" not in a.answer and not (a.used_ai and "60" in a.answer)


def test_tool_needed_stock_values():
    b, m = mill()
    from app import analytics
    run = agent.Run(analytics.analyze(b, m, TODAY), m, TODAY)
    r = agent.needed_stock(run, "India Gate Basmati", 30)
    assert r["facts"]["needed.India Gate Basmati.need_kg"] == 1800 and r["facts"]["needed.India Gate Basmati.shortfall_kg"] == 1380


def mill_range():
    b, m = mill()
    # India Gate: faster in the last 7 days than in the 14-day average -> a real range
    for i in range(7):
        m.append(Movement(id=f"x{i}", brand_id="b1", kind="out", kg=20, date=(TODAY - timedelta(days=i)).isoformat()))
    return b, m


def test_fix1_what_if_without_brand_covers_closest():
    b, m = mill()
    a = agent.run_chat("what if I sell 30 kg more a day?", b, m, TODAY)
    assert "India Gate Basmati would last about 4 days" in a.answer
    assert any(s.tool == "project_stockout" and s.args.get("name") == "India Gate Basmati" for s in a.agent_steps)


def test_fix2_gibberish():
    b, m = mill()
    a = agent.run_chat("asdfghjkl qwerty zzz", b, m, TODAY)
    assert a.answer.startswith("I did not understand that") and "reorder" in a.answer and "note you can copy" not in a.answer
    assert a.claims == [] and a.used_ai is False


def test_fix3_needed_wording_leads_with_the_answer():
    b, m = mill()
    a = agent.run_chat("How many kg of India Gate Basmati do I need for 30 days?", b, m, TODAY)
    assert a.answer.startswith("For 30 days you need about 1,800 kg. You have 420 kg, so about 1,380 kg more.")


def test_fix4_range_matches_screen():
    b, m = mill_range()
    from app import analytics
    f = analytics.analyze(b, m, TODAY)
    ig = f.brands[0]
    assert ig.days_left_low != ig.days_left_high
    rng = f"about {ig.days_left_low} to {ig.days_left_high} days"
    a = agent.run_chat("how long will India Gate last?", b, m, TODAY)
    assert rng in a.answer
    ids = {c.fact_id for c in a.claims}
    assert "India Gate Basmati.days_left_low" in ids and "India Gate Basmati.days_left_high" in ids
    assert rng in agent.run_watch(b, m, TODAY).story.summary or rng in agent.run_chat("which runs out first", b, m, TODAY).answer
