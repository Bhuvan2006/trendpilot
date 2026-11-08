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


def test_reject_stores_learning(tmp_path):
    path = tmp_path / "t.db"
    d = make_draft(path)
    publishing.reject(path, d, "bhuvan", "too promotional")
    assert "too promotional" in Memory(path).get_learnings()
    with pytest.raises(GateError):
        publishing.approve(path, d, "bhuvan")
