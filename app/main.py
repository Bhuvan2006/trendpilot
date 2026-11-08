"""FastAPI layer. Run with: python -m app.main"""
import os
import threading
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import db, pipeline, publishing
from app.harness import PRICES
from app.memory import Memory
from app.publishing import GateError

ROOT = Path(__file__).parent.parent


class Empty(BaseModel):
    """A required JSON body. Browsers cannot send JSON cross-site without CORS, which this app never grants."""


class Decision(BaseModel):
    reviewer: str = Field("demo-reviewer", max_length=80)
    comment: str = Field("", max_length=1000)


class Edit(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


def create_app(db_path=None) -> FastAPI:
    load_dotenv(ROOT / ".env")
    path = db_path or os.getenv("TRENDPILOT_DB") or ROOT / "trendpilot.db"
    db.init(path)
    pipeline.seed_brand(Memory(path))
    with db.connect(path) as c:
        c.execute("UPDATE runs SET status = 'failed', message = 'Interrupted by a server restart', finished_at = ? WHERE status = 'running'", (db.now(),))
    run_lock = threading.Lock()
    app = FastAPI(title="TrendPilot AI")

    def settings():
        provider = os.getenv("MODEL_PROVIDER", "anthropic")
        return provider, ("mock" if provider == "mock" else os.getenv("MODEL_ID", "claude-sonnet-5-5"))

    def query(sql, *args):
        with db.connect(path) as c:
            return [dict(r) for r in c.execute(sql, args)]

    def gated(fn, *args, **kwargs):
        try:
            return fn(path, *args, **kwargs)
        except GateError as e:
            raise HTTPException(409, str(e))

    @app.get("/")
    def dashboard():
        return FileResponse(ROOT / "app" / "static" / "index.html")

    @app.get("/api/status")
    def status():
        provider, model = settings()
        return {"provider": provider, "model": model, "running": run_lock.locked(),
                "key_configured": provider == "mock" or bool(os.getenv("ANTHROPIC_API_KEY"))}

    @app.post("/api/pipeline/run", status_code=202)
    def run(_: Empty):
        provider, model = settings()
        if provider != "mock" and not os.getenv("ANTHROPIC_API_KEY"):
            raise HTTPException(400, "Add ANTHROPIC_API_KEY to trendpilot/.env and restart the server")
        if model not in PRICES:
            raise HTTPException(400, f"No price configured for {model}, so the spend cap cannot work")
        if not run_lock.acquire(blocking=False):
            raise HTTPException(409, "A run is already in progress")
        run_id = pipeline.create_run(path, provider, model)
        max_cost = float(os.getenv("MAX_COST_USD", "1.00"))

        def work():
            try:
                pipeline.run_pipeline(path, provider, model, max_cost, run_id=run_id)
            finally:
                run_lock.release()

        # shortcut: a thread dies with the process (startup marks such runs failed), move to a queue for scheduled runs.
        threading.Thread(target=work, daemon=True).start()
        return {"run_id": run_id}

    @app.get("/api/runs")
    def runs():
        return query("SELECT * FROM runs ORDER BY id DESC LIMIT 20")

    @app.get("/api/trends")
    def trends():
        return query("SELECT * FROM trends WHERE score IS NOT NULL ORDER BY id DESC LIMIT 50")

    @app.get("/api/content")
    def content():
        return query("SELECT d.*, t.topic, t.pillar, t.source_url, t.score, t.angle FROM drafts d JOIN trends t ON t.id = d.trend_id ORDER BY d.id DESC LIMIT 50")

    @app.post("/api/content/{draft_id}/edit")
    def edit(draft_id: int, body: Edit):
        return gated(publishing.edit, draft_id, body.body)

    @app.post("/api/content/{draft_id}/approve")
    def approve(draft_id: int, body: Decision):
        return gated(publishing.approve, draft_id, body.reviewer, body.comment)

    @app.post("/api/content/{draft_id}/reject")
    def reject(draft_id: int, body: Decision):
        return gated(publishing.reject, draft_id, body.reviewer, body.comment)

    @app.post("/api/publish/{draft_id}")
    def publish(draft_id: int, _: Empty):
        return gated(publishing.publish, draft_id)

    @app.get("/api/audit")
    def audit():
        return query("SELECT * FROM audit ORDER BY id DESC LIMIT 300")

    @app.get("/api/memory")
    def memory():
        return {"learnings": Memory(path).get_learnings(), "brand": query("SELECT pillar, content, source_url FROM brand_knowledge")}

    return app


if __name__ == "__main__":
    # Bound to localhost because there is no login. Add authentication before binding anywhere else.
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)
