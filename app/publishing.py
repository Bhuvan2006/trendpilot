"""The human approval gate. Only this module can publish, and no agent is given access to it."""
from app import db
from app.guard import check_draft
from app.memory import Memory


class GateError(Exception):
    pass


class DryRunPublisher:
    """Records what would be posted and sends nothing. A real adapter only needs the same publish method."""
    mode = "dry_run"

    def publish(self, draft) -> str:
        return f"dry-run-{draft['platform']}-{draft['id']}"


def _draft(c, draft_id):
    row = c.execute(
        "SELECT d.*, t.title, t.summary, t.pillar FROM drafts d JOIN trends t ON t.id = d.trend_id WHERE d.id = ?",
        (draft_id,),
    ).fetchone()
    if row is None:
        raise GateError(f"draft {draft_id} not found")
    return row


def _deny(path, actor, action, draft_id, reason):
    db.audit(path, None, actor, action, draft_id, allowed=False, detail=reason)
    raise GateError(reason)


def edit(path, draft_id, body: str) -> dict:
    with db.connect(path) as c:
        status = _draft(c, draft_id)["status"]
    if status != "needs_review":
        _deny(path, "human", "edit", draft_id, f"cannot edit a draft that is {status}")
    with db.connect(path) as c:
        c.execute("UPDATE drafts SET body = ?, edited = 1 WHERE id = ?", (body, draft_id))
    db.audit(path, None, "human", "edit", draft_id)
    return {"id": draft_id, "status": status}


def approve(path, draft_id, reviewer: str, comment: str = "") -> dict:
    with db.connect(path) as c:
        d = _draft(c, draft_id)
    if d["status"] != "needs_review":
        _deny(path, reviewer, "approve", draft_id, f"cannot approve a draft that is {d['status']}")
    if not d["review_passed"] and not d["edited"]:
        _deny(path, reviewer, "approve", draft_id, "Brand Reviewer flagged this draft, edit it before approving")
    allowed = Memory(path).all_brand_text() + d["title"] + d["summary"]
    problems = check_draft(d["body"], d["platform"], allowed)
    if problems:
        _deny(path, reviewer, "approve", draft_id, "; ".join(problems))
    with db.connect(path) as c:
        c.execute("UPDATE drafts SET status = 'approved' WHERE id = ?", (draft_id,))
        c.execute("INSERT INTO approvals(draft_id, decision, reviewer, comment, ts) VALUES(?,?,?,?,?)",
                  (draft_id, "approved", reviewer, comment, db.now()))
    db.audit(path, None, reviewer, "approve", draft_id)
    return {"id": draft_id, "status": "approved"}


def reject(path, draft_id, reviewer: str, comment: str) -> dict:
    with db.connect(path) as c:
        d = _draft(c, draft_id)
        if d["status"] != "needs_review":
            raise GateError(f"cannot reject a draft that is {d['status']}")
        c.execute("UPDATE drafts SET status = 'rejected' WHERE id = ?", (draft_id,))
        c.execute("INSERT INTO approvals(draft_id, decision, reviewer, comment, ts) VALUES(?,?,?,?,?)",
                  (draft_id, "rejected", reviewer, comment, db.now()))
    if comment:
        Memory(path).remember(f"{d['pillar']}/{d['platform']}", comment, reviewer)
    db.audit(path, None, reviewer, "reject", draft_id, detail=comment)
    return {"id": draft_id, "status": "rejected"}


def publish(path, draft_id, adapter=None) -> dict:
    adapter = adapter or DryRunPublisher()
    mode = getattr(adapter, "mode", "dry_run")
    with db.connect(path) as c:
        c.execute("BEGIN IMMEDIATE")  # two simultaneous clicks must not both reach the adapter
        d = _draft(c, draft_id)
        post = c.execute("SELECT * FROM posts WHERE draft_id = ?", (draft_id,)).fetchone()
        if post and post["status"] == "published":
            return dict(post)
        if d["status"] != "approved":
            status = d["status"]
        else:
            status = None
            if not post:
                c.execute("INSERT INTO posts(draft_id, platform, mode, status, ts) VALUES(?,?,?,?,?)",
                          (draft_id, d["platform"], mode, "pending", db.now()))
    if status:
        _deny(path, "publisher", "publish", draft_id, f"blocked: draft is {status}, a human must approve it first")
    try:
        external_id = adapter.publish(dict(d))
    except Exception as e:
        with db.connect(path) as c:
            c.execute("UPDATE posts SET status = 'failed' WHERE draft_id = ?", (draft_id,))
        _deny(path, "publisher", "publish", draft_id, f"adapter failed: {e}")
    with db.connect(path) as c:
        c.execute("UPDATE posts SET status = 'published', external_id = ?, ts = ? WHERE draft_id = ?",
                  (external_id, db.now(), draft_id))
        c.execute("UPDATE drafts SET status = 'published' WHERE id = ?", (draft_id,))
        post = c.execute("SELECT * FROM posts WHERE draft_id = ?", (draft_id,)).fetchone()
    db.audit(path, None, "publisher", "publish", draft_id, detail=f"{mode}: {external_id}")
    return dict(post)
