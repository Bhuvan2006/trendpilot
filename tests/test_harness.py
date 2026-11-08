from pathlib import Path
from types import SimpleNamespace

import pytest
from agno.skills import validate_skill_directory
from agno.tools.function import Function, FunctionCall
from pydantic import BaseModel

from app import db
from app.harness import MAX_TOOL_CALLS, BudgetExceeded, Harness, StepFailed
from app.schemas import score


class Out(BaseModel):
    answer: str


class FakeAgent:
    name = "Trend Scout"

    def __init__(self, replies, tokens=(100, 50)):
        self.replies, self.prompts, self.tokens = list(replies), [], tokens

    def run(self, prompt):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(content=reply, metrics=SimpleNamespace(input_tokens=self.tokens[0], output_tokens=self.tokens[1]))


def make_harness(tmp_path, budget=1.0):
    path = tmp_path / "t.db"
    db.init(path)
    with db.connect(path) as c:
        run = c.execute("INSERT INTO runs(status) VALUES('running')").lastrowid
    return Harness(path, run, budget, "claude-sonnet-5-5", backoff_s=0)


def audit_rows(h):
    with db.connect(h.path) as c:
        return [dict(r) for r in c.execute("SELECT * FROM audit ORDER BY id")]


def call_through_agno(hook, fn):
    """Runs the tool through Agno's real FunctionCall, the path an agent's tool call takes."""
    f = Function.from_callable(fn)
    f.tool_hooks = [hook]
    return FunctionCall(function=f, arguments={}).execute().result


def test_score_uses_the_published_weights():
    assert score(90, 95, 80, 90, 85) == 88.75


def test_forbidden_tool_is_denied_and_never_runs(tmp_path):
    h = make_harness(tmp_path)
    ran = []

    def publish_post() -> str:
        """Publish."""
        ran.append(1)
        return "posted"

    result = call_through_agno(h.tool_hook("Trend Scout"), publish_post)
    assert "DENIED" in result and ran == []
    row = audit_rows(h)[-1]
    assert (row["actor"], row["target"], row["allowed"]) == ("Trend Scout", "publish_post", 0)


def test_allowed_tool_runs_and_is_audited(tmp_path):
    h = make_harness(tmp_path)

    def search_hackernews() -> str:
        """Search."""
        return "results"

    assert call_through_agno(h.tool_hook("Trend Scout"), search_hackernews) == "results"
    assert audit_rows(h)[-1]["allowed"] == 1


def test_tool_call_cap_denies_extra_calls(tmp_path):
    h = make_harness(tmp_path)
    hook = h.tool_hook("Trend Scout")
    results = [hook("search_hackernews", lambda: "ok", {}) for _ in range(MAX_TOOL_CALLS + 1)]
    assert results[MAX_TOOL_CALLS - 1] == "ok" and "DENIED" in results[MAX_TOOL_CALLS]


def test_invalid_output_retries_with_the_error(tmp_path):
    h = make_harness(tmp_path)
    agent = FakeAgent(["not json", '{"answer": "fixed"}'])
    assert h.run_agent(agent, "go", Out).answer == "fixed"
    assert "invalid" in agent.prompts[1].lower()


def test_accepts_fenced_json_and_parsed_models(tmp_path):
    h = make_harness(tmp_path)
    assert h.run_agent(FakeAgent(['```json\n{"answer": "a"}\n```']), "go", Out).answer == "a"
    assert h.run_agent(FakeAgent([Out(answer="b")]), "go", Out).answer == "b"


def test_accepts_json_surrounded_by_prose(tmp_path):
    h = make_harness(tmp_path)
    reply = 'Here is the result:\n{"answer": "a"}\nLet me know if you need more.'
    assert h.run_agent(FakeAgent([reply]), "go", Out).answer == "a"


def test_models_without_native_output_are_not_forced_into_json_mode(tmp_path):
    # Forced JSON mode stops a model from calling tools, so it can never load its skill.
    from app.agents import build_agents
    from app.memory import Memory
    h = make_harness(tmp_path)
    scout = build_agents(h, Memory(h.path), "nvidia", "nvidia/nemotron-3-ultra-550b-a55b", [])["Trend Scout"]
    assert scout.output_schema is None
    assert any("topics" in line for line in scout.instructions)


def test_models_with_native_output_keep_the_schema(tmp_path):
    from app.agents import build_agents
    from app.memory import Memory
    from app.schemas import ScoutOutput
    h = make_harness(tmp_path)
    scout = build_agents(h, Memory(h.path), "anthropic", "claude-sonnet-5-5", [])["Trend Scout"]
    assert scout.output_schema is ScoutOutput


def test_gives_up_after_bounded_attempts(tmp_path):
    h = make_harness(tmp_path)
    agent = FakeAgent(["bad", "still bad", "never reached"])
    with pytest.raises(StepFailed):
        h.run_agent(agent, "go", Out)
    assert len(agent.prompts) == 2


def test_transient_error_is_retried(tmp_path):
    h = make_harness(tmp_path)
    agent = FakeAgent([ConnectionError("blip"), '{"answer": "ok"}'])
    assert h.run_agent(agent, "go", Out).answer == "ok"


def test_budget_stops_the_next_model_call(tmp_path):
    h = make_harness(tmp_path, budget=0.0001)
    agent = FakeAgent(['{"answer": "x"}', '{"answer": "y"}'], tokens=(1000, 1000))
    h.run_agent(agent, "go", Out)
    with pytest.raises(BudgetExceeded):
        h.run_agent(agent, "go", Out)
    assert len(agent.prompts) == 1


def test_usage_is_recorded_with_real_price(tmp_path):
    h = make_harness(tmp_path)
    h.run_agent(FakeAgent(['{"answer": "x"}'], tokens=(1_000_000, 100_000)), "go", Out)
    assert h.cost_usd == pytest.approx(3.0)
    row = audit_rows(h)[-1]
    assert (row["actor"], row["input_tokens"], row["output_tokens"]) == ("Trend Scout", 1_000_000, 100_000)


def test_free_openrouter_models_cost_nothing_and_still_count_tokens(tmp_path):
    path = tmp_path / "t.db"
    db.init(path)
    with db.connect(path) as c:
        run = c.execute("INSERT INTO runs(status) VALUES('running')").lastrowid
    h = Harness(path, run, 1.0, "nvidia/some-model:free", backoff_s=0)
    h.run_agent(FakeAgent(['{"answer": "x"}'], tokens=(5000, 500)), "go", Out)
    assert h.cost_usd == 0 and h.input_tokens == 5000


def test_unknown_model_refuses_to_run_without_a_price(tmp_path):
    with pytest.raises(ValueError):
        Harness(tmp_path / "t.db", 1, 1.0, "made-up-model")


def test_skills_are_valid_agno_skills():
    folders = sorted(p for p in Path("skills").iterdir() if p.is_dir())
    assert len(folders) == 4
    for folder in folders:
        assert validate_skill_directory(folder) == [], folder
