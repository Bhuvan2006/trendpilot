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
        _draft(c, draft_id)  # raises if it does not exist
        changed = c.execute("UPDATE drafts SET body = ?, edited = 1 WHERE id = ? AND status = 'needs_review'", (body, draft_id)).rowcount
    if not changed:
        _deny(path, "human", "edit", draft_id, "this draft is no longer under review")
    db.audit(path, None, "human", "edit", draft_id)
    return {"id": draft_id, "status": "needs_review"}


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
        # Matching the body means only the exact text the guard just checked can be approved.
        changed = c.execute("UPDATE drafts SET status = 'approved' WHERE id = ? AND status = 'needs_review' AND body = ?", (draft_id, d["body"])).rowcount
        if changed:
            c.execute("INSERT INTO approvals(draft_id, decision, reviewer, comment, ts) VALUES(?,?,?,?,?)",
                      (draft_id, "approved", reviewer, comment, db.now()))
    if not changed:
        _deny(path, reviewer, "approve", draft_id, "this draft changed while it was being approved, review it again")
    db.audit(path, None, reviewer, "approve", draft_id)
    return {"id": draft_id, "status": "approved"}


def reject(path, draft_id, reviewer: str, comment: str) -> dict:
    with db.connect(path) as c:
        d = _draft(c, draft_id)
        if d["status"] != "needs_review":
            raise GateError(f"cannot reject a draft that is {d['status']}")
        changed = c.execute("UPDATE drafts SET status = 'rejected' WHERE id = ? AND status = 'needs_review'", (draft_id,)).rowcount
        if changed:
            c.execute("INSERT INTO approvals(draft_id, decision, reviewer, comment, ts) VALUES(?,?,?,?,?)",
                      (draft_id, "rejected", reviewer, comment, db.now()))
    if not changed:
        raise GateError("this draft is no longer under review")
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
        blocked = None
        if d["status"] != "approved":
            blocked = f"this draft is {d['status'].replace('_', ' ')}, a human must approve it first"
        elif post and post["status"] == "pending":
            # shortcut: a crash mid-publish leaves this row pending and needs a manual check, because the post may have gone out.
            blocked = "a publish is already in progress for this draft"
        elif post:  # an earlier attempt failed, so this one takes over the row
            c.execute("UPDATE posts SET status = 'pending' WHERE draft_id = ?", (draft_id,))
        else:
            c.execute("INSERT INTO posts(draft_id, platform, mode, status, ts) VALUES(?,?,?,?,?)",
                      (draft_id, d["platform"], mode, "pending", db.now()))
    if blocked:
        _deny(path, "publisher", "publish", draft_id, blocked)
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
