"""SQLite storage. Each call opens its own connection, because agents, tools and the API run on different threads."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, status TEXT,
  provider TEXT, model TEXT, input_tokens INT DEFAULT 0, output_tokens INT DEFAULT 0,
  cost_usd REAL DEFAULT 0, message TEXT);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id INT, actor TEXT, action TEXT, target TEXT,
  allowed INT, detail TEXT, input_tokens INT DEFAULT 0, output_tokens INT DEFAULT 0, duration_ms INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS trends(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INT, source TEXT, source_url TEXT UNIQUE, title TEXT,
  topic TEXT, summary TEXT, points INT DEFAULT 0, comments INT DEFAULT 0, retrieved_at TEXT,
  relevance REAL, brand_fit REAL, momentum REAL, evidence REAL, content_potential REAL, score REAL,
  pillar TEXT, reason TEXT, angle TEXT, status TEXT DEFAULT 'new');
CREATE TABLE IF NOT EXISTS drafts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, trend_id INT NOT NULL REFERENCES trends(id), run_id INT, platform TEXT,
  body TEXT, status TEXT, review_passed INT DEFAULT 0, review_score INT DEFAULT 0, review_notes TEXT, edited INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, draft_id INT NOT NULL REFERENCES drafts(id), decision TEXT,
  reviewer TEXT, comment TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS posts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, draft_id INT NOT NULL UNIQUE REFERENCES drafts(id), platform TEXT,
  mode TEXT, external_id TEXT, status TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS learnings(key TEXT PRIMARY KEY, value TEXT, source TEXT, updated_at TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS brand_knowledge USING fts5(pillar, content, source_url UNINDEXED);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(path):
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init(path) -> None:
    with connect(path) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)


def audit(path, run_id, actor, action, target="", allowed=True, detail="",
          input_tokens=0, output_tokens=0, duration_ms=0) -> None:
    with connect(path) as c:
        c.execute(
            "INSERT INTO audit(ts, run_id, actor, action, target, allowed, detail, input_tokens, output_tokens, duration_ms)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (now(), run_id, actor, action, str(target), int(allowed), str(detail)[:500],
             input_tokens, output_tokens, duration_ms),
        )
