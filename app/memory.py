"""Three memory stores. Agents pull small, query-specific slices instead of carrying history in the prompt."""
import re

from app import db

MAX_CHARS = 1500  # hard cap on what one memory lookup can add to a prompt


class Memory:
    def __init__(self, path):
        self.path = path

    def seed_brand(self, entries: list[tuple[str, str, str]]) -> None:
        with db.connect(self.path) as c:
            c.execute("DELETE FROM brand_knowledge")
            c.executemany("INSERT INTO brand_knowledge(pillar, content, source_url) VALUES(?,?,?)", entries)

    def search_brand(self, query: str, k: int = 3) -> str:
        words = re.findall(r"\w+", query)
        if not words:
            return ""
        # Quoting each word stops user text being parsed as FTS5 operators.
        match = " OR ".join(f'"{w}"' for w in words)
        with db.connect(self.path) as c:
            rows = c.execute(
                "SELECT pillar, content, source_url FROM brand_knowledge WHERE brand_knowledge MATCH ? ORDER BY rank LIMIT ?",
                (match, k),
            ).fetchall()
        return "\n".join(f"[{r['pillar']}] {r['content']} (source: {r['source_url']})" for r in rows)[:MAX_CHARS]

    def all_brand_text(self) -> str:
        with db.connect(self.path) as c:
            return "\n".join(r["content"] for r in c.execute("SELECT content FROM brand_knowledge"))

    def campaign_history(self, pillar: str, k: int = 3) -> str:
        with db.connect(self.path) as c:
            rows = c.execute(
                "SELECT d.platform, d.body FROM drafts d JOIN trends t ON t.id = d.trend_id"
                " WHERE t.pillar = ? AND d.status IN ('approved','published') ORDER BY d.id DESC LIMIT ?",
                (pillar, k),
            ).fetchall()
        return "\n".join(f"[{r['platform']}] {r['body'][:300]}" for r in rows)[:MAX_CHARS] or "No past posts for this pillar."

    def seen_urls(self) -> set[str]:
        with db.connect(self.path) as c:
            # Only finished runs count, so items from a failed run are retried.
            return {r["source_url"] for r in c.execute("SELECT t.source_url FROM trends t JOIN runs r ON r.id = t.run_id WHERE r.status = 'done'")}

    def remember(self, key: str, value: str, source: str) -> None:
        with db.connect(self.path) as c:
            c.execute(
                "INSERT INTO learnings(key, value, source, updated_at) VALUES(?,?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, source=excluded.source, updated_at=excluded.updated_at",
                (key, value, source, db.now()),
            )

    def get_learnings(self, k: int = 5) -> str:
        with db.connect(self.path) as c:
            rows = c.execute("SELECT key, value FROM learnings ORDER BY updated_at DESC, rowid DESC LIMIT ?", (k,)).fetchall()
        return "\n".join(f"{r['key']}: {r['value']}" for r in rows)[:MAX_CHARS] or "No human feedback recorded yet."
