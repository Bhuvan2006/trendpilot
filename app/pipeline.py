"""The Agno Workflow. Code owns the order, and agents only decide inside a step."""
from pathlib import Path

from agno.workflow import Workflow
from agno.workflow.parallel import Parallel
from agno.workflow.step import Step
from agno.workflow.types import StepInput, StepOutput

from app import agents, db, sources
from app.guard import check_draft
from app.harness import Harness
from app.memory import Memory
from app.schemas import ReviewOutput, ScoutOutput, StrategyOutput, StudioOutput, score

BRAND_FILE = Path(__file__).parent.parent / "knowledge" / "brand.md"
BRAND_URL = "https://www.dotsin.ai/"
QUALIFY_AT = 60
MAX_ITEMS_FOR_SCOUT = 30


def seed_brand(memory: Memory) -> None:
    pillar, entries = "", []
    for line in BRAND_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            pillar = line[3:].strip()
        elif line.startswith("- "):
            entries.append((pillar, line[2:].strip(), BRAND_URL))
    memory.seed_brand(entries)


def fixtures(run_id: int) -> dict:
    """Mock mode data. URLs include the run id so repeat offline demos still find something new."""

    def make(source, n, points):
        return {"source": source, "url": f"https://example.com/mock/{run_id}/{source}/{n}", "title": f"[MOCK] Sample {source} item {n}",
                "summary": "[MOCK] Canned data, not a real discussion.", "points": points, "comments": points // 3, "created_at": db.now()}

    return {"hackernews": lambda: [make("hackernews", 1, 150), make("hackernews", 2, 90)], "arxiv": lambda: [make("arxiv", 1, 0)]}


def create_run(path, provider: str, model_id: str) -> int:
    db.init(path)
    with db.connect(path) as c:
        return c.execute("INSERT INTO runs(started_at, status, provider, model) VALUES(?,?,?,?)",
                         (db.now(), "running", provider, model_id)).lastrowid


def _finish(path, run_id, status, message):
    with db.connect(path) as c:
        c.execute("UPDATE runs SET status = ?, message = ?, finished_at = ? WHERE id = ?", (status, message, db.now(), run_id))
    db.audit(path, run_id, "workflow", "finished", status, allowed=status == "done", detail=message)


_data = agents.untrusted


def run_pipeline(path, provider, model_id, max_cost_usd, top_n=2, collectors=None, run_id=None) -> int:
    run_id = run_id or create_run(path, provider, model_id)
    try:
        _execute(path, provider, model_id, max_cost_usd, top_n, collectors, run_id)
    except Exception as e:  # a setup failure must not leave the run stuck as running
        _finish(path, run_id, "failed", f"{type(e).__name__}: {e}")
    return run_id


def _execute(path, provider, model_id, max_cost_usd, top_n, collectors, run_id) -> None:
    memory = Memory(path)
    seed_brand(memory)
    harness = Harness(path, run_id, max_cost_usd, model_id)
    items, tool_items, source_errors = [], [], []
    team = agents.build_agents(harness, memory, provider, model_id, tool_items)
    collectors = collectors or (fixtures(run_id) if provider == "mock" else {"hackernews": sources.collect_hn, "arxiv": sources.fetch_arxiv})
    state = {"stop": None, "failed": None, "topics": [], "qualified": [], "drafts": []}

    def collect(name):
        def fn():
            try:
                found = collectors[name]()
            except Exception as e:  # one source being down must not end the run
                source_errors.append(name)
                db.audit(path, run_id, name, "source_error", allowed=False, detail=f"{type(e).__name__}: {e}")
                return f"{name} failed"
            items.extend(found)
            return f"{len(found)} items from {name}"
        return fn

    def store(found):
        # The upsert moves an item left by a failed run onto this run, so it counts as seen once this run finishes.
        with db.connect(path) as c:
            c.executemany("INSERT INTO trends(run_id, source, source_url, title, summary, points, comments, retrieved_at, momentum) VALUES(?,?,?,?,?,?,?,?,?)"
                          " ON CONFLICT(source_url) DO UPDATE SET run_id=excluded.run_id, points=excluded.points, comments=excluded.comments, retrieved_at=excluded.retrieved_at, momentum=excluded.momentum",
                          [(run_id, i["source"], i["url"], i["title"], i["summary"], i["points"], i["comments"], db.now(), sources.momentum(i)) for i in found])

    def scout():
        if not items and len(source_errors) == len(collectors):
            raise RuntimeError("All trend sources failed, check the network and try again")
        seen = memory.seen_urls()
        fresh = list({i["url"]: i for i in items if i["url"] not in seen}.values())
        if not fresh:
            state["stop"] = "Nothing new since the last run"
            return state["stop"]
        fresh.sort(key=sources.momentum, reverse=True)
        fresh = fresh[:MAX_ITEMS_FOR_SCOUT]
        store(fresh)
        out = harness.run_agent(team["Trend Scout"], "Select the topics worth considering from these items.\n" + _data(fresh), ScoutOutput)
        # Items found by the agent's own search count as fetched, and must be stored before they can be scored.
        extra = [i for i in tool_items if i["url"] not in seen]
        store(extra)
        fetched = {i["url"] for i in fresh + extra}
        for t in out.topics:
            if t.source_url in fetched:
                state["topics"].append(t)
            else:
                db.audit(path, run_id, "Trend Scout", "invalid_source", allowed=False, detail=f"not fetched this run: {t.source_url}")
        if not state["topics"]:
            state["stop"] = "No relevant topics in this batch"
        return f"{len(state['topics'])} topics"

    def strategist():
        known = {t.source_url: t for t in state["topics"]}
        out = harness.run_agent(team["Relevance Strategist"], "Score these topics.\n" + _data([t.model_dump() for t in state["topics"]]), StrategyOutput)
        scored = []
        with db.connect(path) as c:
            for t in out.trends:
                if t.source_url not in known:
                    db.audit(path, run_id, "Relevance Strategist", "invalid_source", allowed=False, detail=f"not a selected topic: {t.source_url}")
                    continue
                momentum = c.execute("SELECT momentum FROM trends WHERE source_url = ?", (t.source_url,)).fetchone()["momentum"]
                total = score(t.relevance, t.brand_fit, momentum, t.evidence, t.content_potential)
                c.execute("UPDATE trends SET topic=?, relevance=?, brand_fit=?, evidence=?, content_potential=?, score=?, pillar=?, reason=?, angle=?, status='rejected' WHERE source_url=?",
                          (known[t.source_url].topic, t.relevance, t.brand_fit, t.evidence, t.content_potential, total, t.pillar, t.reason, t.angle, t.source_url))
                scored.append((total, t.source_url))
            top = [url for total, url in sorted(scored, reverse=True) if total >= QUALIFY_AT][:top_n]
            for url in top:
                c.execute("UPDATE trends SET status = 'qualified' WHERE source_url = ?", (url,))
            state["qualified"] = [dict(c.execute("SELECT * FROM trends WHERE source_url = ?", (u,)).fetchone()) for u in top]
        if not top:
            state["stop"] = f"No topic reached the qualifying score of {QUALIFY_AT}"
        return f"{len(top)} qualified of {len(scored)}"

    def write_prompt(trend):
        fields = {k: trend[k] for k in ("topic", "summary", "pillar", "angle", "source_url")}
        return "Write one linkedin post and one instagram caption for this topic.\n" + _data(fields)

    def studio():
        for trend in state["qualified"]:
            out = harness.run_agent(team["Content Studio"], write_prompt(trend), StudioOutput)
            for platform in ("linkedin", "instagram"):  # exactly one per platform, whatever the model returned
                body = next((d.body for d in out.drafts if d.platform == platform), None)
                if body:
                    state["drafts"].append({"trend": trend, "platform": platform, "body": body})
        return f"{len(state['drafts'])} drafts"

    def check(draft):
        """Deterministic guard first, because it is free. The model reviewer runs only if the guard passes."""
        trend = draft["trend"]
        problems = check_draft(draft["body"], draft["platform"], memory.all_brand_text() + trend["title"] + (trend["summary"] or ""))
        if problems:
            return False, 0, "Guard: " + "; ".join(problems)
        fields = {"platform": draft["platform"], "draft": draft["body"], "source_summary": trend["summary"], "source_url": trend["source_url"]}
        r = harness.run_agent(team["Brand Reviewer"], "Review this draft.\n" + _data(fields), ReviewOutput)
        notes = " ".join([r.explanation, *[f"Unsupported: {u}" for u in r.unsupported_claims], *[f"Fix: {s}" for s in r.suggested_edits]])
        return r.approved, r.score, notes

    def review():
        with_notes, rows = 0, []
        for draft in state["drafts"]:
            passed, review_score, notes = check(draft)
            if not passed:  # one revision only, so a stubborn draft cannot burn the budget
                out = harness.run_agent(team["Content Studio"], f"{write_prompt(draft['trend'])}\n\nRevise the {draft['platform']} draft below using the reviewer feedback.\n" + _data({"reviewer_feedback": notes, "draft": draft["body"]}), StudioOutput)
                revised = next((d.body for d in out.drafts if d.platform == draft["platform"]), None)
                if revised:
                    draft["body"] = revised
                    passed, review_score, notes = check(draft)
            with_notes += not passed
            rows.append((draft["trend"]["id"], run_id, draft["platform"], draft["body"], "needs_review", int(passed), review_score, notes))
        with db.connect(path) as c:  # all at once, so a failed run leaves no partial drafts for its retry to duplicate
            c.executemany("INSERT INTO drafts(trend_id, run_id, platform, body, status, review_passed, review_score, review_notes) VALUES(?,?,?,?,?,?,?,?)", rows)
        return f"{len(state['drafts'])} drafts sent to human review, {with_notes} flagged"

    def step(name, fn):
        def executor(step_input: StepInput) -> StepOutput:
            if state["stop"] or state["failed"]:
                return StepOutput(content="skipped")
            db.audit(path, run_id, "workflow", "step_start", name)
            try:
                return StepOutput(content=fn())
            except Exception as e:  # BudgetExceeded, StepFailed or anything unexpected must end the run with a reason
                state["failed"] = str(e)
                return StepOutput(content=str(e), success=False, stop=True)
        return Step(name=name, executor=executor, max_retries=0)

    workflow = Workflow(name="TrendPilot", telemetry=False, steps=[
        Parallel(step("Collect Hacker News", collect("hackernews")), step("Collect arXiv", collect("arxiv")), name="Collect"),
        step("Trend Scout", scout), step("Relevance Strategist", strategist), step("Content Studio", studio), step("Brand Review", review),
    ])
    try:
        workflow.run(input="Find and draft content for today's best trend opportunities.")
    except Exception as e:
        state["failed"] = state["failed"] or f"{type(e).__name__}: {e}"
    if state["failed"]:
        _finish(path, run_id, "failed", state["failed"])
    else:
        _finish(path, run_id, "done", state["stop"] or f"{len(state['drafts'])} drafts ready for review")
