import pytest

from app import db, publishing
from app.memory import Memory
from app.publishing import GateError


def make_draft(path, passed=1, body="A plain post"):
    db.init(path)
    with db.connect(path) as c:
        run = c.execute("INSERT INTO runs(status) VALUES('done')").lastrowid
        trend = c.execute(
            "INSERT INTO trends(run_id, source, source_url, title, summary, pillar) VALUES(?,?,?,?,?,?)",
            (run, "hn", f"https://x.test/{run}-{body[:3]}", "A title", "A summary", "Zero-Prompt AI"),
        ).lastrowid
        return c.execute(
            "INSERT INTO drafts(trend_id, run_id, platform, body, status, review_passed) VALUES(?,?,?,?,?,?)",
            (trend, run, "linkedin", body, "needs_review", passed),
        ).lastrowid


class Adapter:
    def __init__(self, fail_times=0):
        self.calls, self.fail_times = 0, fail_times

    def publish(self, draft):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("network down")
        return f"dry-{draft['id']}"


def test_publish_blocked_without_approval_and_audited(tmp_path):
    path = tmp_path / "t.db"
    d = make_draft(path)
    with pytest.raises(GateError):
        publishing.publish(path, d, Adapter())
    with db.connect(path) as c:
        row = c.execute("SELECT allowed FROM audit WHERE action='publish'").fetchone()
    assert row["allowed"] == 0


def test_publish_is_idempotent(tmp_path):
    path = tmp_path / "t.db"
    d = make_draft(path)
    publishing.approve(path, d, "bhuvan")
    adapter = Adapter()
    first = publishing.publish(path, d, adapter)
    second = publishing.publish(path, d, adapter)
    assert adapter.calls == 1 and first["external_id"] == second["external_id"]
    assert first["mode"] == "dry_run"


def test_failed_publish_can_be_retried_without_double_post(tmp_path):
    path = tmp_path / "t.db"
    d = make_draft(path)
    publishing.approve(path, d, "bhuvan")
    adapter = Adapter(fail_times=1)
    with pytest.raises(GateError):
        publishing.publish(path, d, adapter)
    assert publishing.publish(path, d, adapter)["status"] == "published"
    with db.connect(path) as c:
        assert c.execute("SELECT count(*) n FROM posts").fetchone()["n"] == 1


def test_review_failed_draft_needs_human_edit_before_approval(tmp_path):
    path = tmp_path / "t.db"
    d = make_draft(path, passed=0)
    with pytest.raises(GateError):
        publishing.approve(path, d, "bhuvan")
    publishing.edit(path, d, "A corrected plain post")
    assert publishing.approve(path, d, "bhuvan")["status"] == "approved"


def test_approval_reruns_guard_on_edited_text(tmp_path):
    path = tmp_path / "t.db"
    d = make_draft(path)
    publishing.edit(path, d, "Boosts focus by 73%")
    with pytest.raises(GateError):
        publishing.approve(path, d, "bhuvan")


def test_approved_draft_cannot_be_edited(tmp_path):
    path = tmp_path / "t.db"
    d = make_draft(path)
    publishing.approve(path, d, "bhuvan")
    with pytest.raises(GateError):
        publishing.edit(path, d, "swap the text after approval")


def test_pending_post_is_refused_so_the_adapter_cannot_run_twice(tmp_path):
    path = tmp_path / "t.db"
    d = make_draft(path)
    publishing.approve(path, d, "bhuvan")
    with db.connect(path) as c:
        c.execute("INSERT INTO posts(draft_id, platform, mode, status, ts) VALUES(?,?,?,?,?)", (d, "linkedin", "dry_run", "pending", db.now()))
    adapter = Adapter()
    with pytest.raises(GateError):
        publishing.publish(path, d, adapter)
    assert adapter.calls == 0


def interleave(monkeypatch, path, sql):
    """Runs another writer right after the gate has read the draft, the window a race would use."""
    real, done = publishing._draft, []

    def wrapper(c, draft_id):
        row = real(c, draft_id)
        if not done:
            done.append(1)
            with db.connect(path) as other:
                other.execute(sql, (draft_id,))
        return row

    monkeypatch.setattr(publishing, "_draft", wrapper)


def test_approve_refuses_text_changed_after_the_guard_read_it(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    d = make_draft(path)
    interleave(monkeypatch, path, "UPDATE drafts SET body = 'The world''s only twin' WHERE id = ?")
    with pytest.raises(GateError):
        publishing.approve(path, d, "bhuvan")
    with db.connect(path) as c:
        assert c.execute("SELECT status FROM drafts WHERE id = ?", (d,)).fetchone()["status"] == "needs_review"


def test_edit_cannot_land_after_a_concurrent_approval(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    d = make_draft(path)
    interleave(monkeypatch, path, "UPDATE drafts SET status = 'approved' WHERE id = ?")
    with pytest.raises(GateError):
        publishing.edit(path, d, "swapped text")
    with db.connect(path) as c:
        assert c.execute("SELECT body FROM drafts WHERE id = ?", (d,)).fetchone()["body"] == "A plain post"


def test_reject_cannot_overwrite_a_concurrent_approval(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    d = make_draft(path)
    interleave(monkeypatch, path, "UPDATE drafts SET status = 'approved' WHERE id = ?")
    with pytest.raises(GateError):
        publishing.reject(path, d, "bhuvan", "no")
    with db.connect(path) as c:
        assert c.execute("SELECT status FROM drafts WHERE id = ?", (d,)).fetchone()["status"] == "approved"


def test_reject_stores_learning(tmp_path):
    path = tmp_path / "t.db"
    d = make_draft(path)
    publishing.reject(path, d, "bhuvan", "too promotional")
    assert "too promotional" in Memory(path).get_learnings()
    with pytest.raises(GateError):
        publishing.approve(path, d, "bhuvan")
