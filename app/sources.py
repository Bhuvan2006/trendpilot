"""Live, public, keyless trend sources. Engagement numbers come straight from the source and are never generated."""
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import httpx

HN_QUERIES = ["AI agents", "agent memory", "LLM memory", "AI personalization", "personal AI", "AI assistant", "digital twin",
              "proactive assistant", "recommendation system", "user behavior", "on-device AI", "AI privacy"]
HN_FRONT_PAGE_QUERY = "AI"
ARXIV_QUERY = 'all:"agent memory" OR all:"behavior modeling" OR all:"personalized agents" OR all:"causal behavior"'
ATOM = "{http://www.w3.org/2005/Atom}"
TIMEOUT = 20
HN_WINDOW_DAYS = 14


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def fetch_hn(query: str, limit: int = 10, tags: str = "story") -> list[dict]:
    """Newest matches first. Relevance ranking keeps returning the same stories from run to run."""
    since = int((datetime.now(timezone.utc) - timedelta(days=HN_WINDOW_DAYS)).timestamp())
    r = httpx.get("https://hn.algolia.com/api/v1/search_by_date",
                  params={"query": query, "tags": tags, "hitsPerPage": limit,
                          "numericFilters": f"created_at_i>{since},points>10"}, timeout=TIMEOUT)
    r.raise_for_status()
    return [{"source": "hackernews", "url": f"https://news.ycombinator.com/item?id={h['objectID']}",
             "title": _plain(h.get("title")), "summary": _plain(h.get("story_text"))[:500] or _plain(h.get("title")),
             "points": h.get("points") or 0, "comments": h.get("num_comments") or 0, "created_at": h["created_at"]}
            for h in r.json()["hits"]]


def fetch_arxiv(query: str = ARXIV_QUERY, limit: int = 15) -> list[dict]:
    r = httpx.get("https://export.arxiv.org/api/query",
                  params={"search_query": query, "max_results": limit, "sortBy": "submittedDate", "sortOrder": "descending"},
                  timeout=TIMEOUT, follow_redirects=True)
    r.raise_for_status()
    return [{"source": "arxiv", "url": e.findtext(f"{ATOM}id", "").replace("http://", "https://"),
             "title": _plain(e.findtext(f"{ATOM}title", "")), "summary": _plain(e.findtext(f"{ATOM}summary", ""))[:500],
             "points": 0, "comments": 0, "created_at": e.findtext(f"{ATOM}published", "")}
            for e in ET.fromstring(r.text).findall(f"{ATOM}entry")]


def collect_hn() -> list[dict]:
    front_page = fetch_hn(HN_FRONT_PAGE_QUERY, tags="story,front_page")
    return front_page + [item for q in HN_QUERIES for item in fetch_hn(q)]


def momentum(item: dict) -> int:
    """0 to 100. Hacker News uses engagement per hour. arXiv has no engagement data, so it uses recency alone."""
    created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
    age_hours = max((datetime.now(timezone.utc) - created).total_seconds() / 3600, 1)
    if item["source"] == "hackernews":
        return min(100, round((item["points"] + 2 * item["comments"]) / age_hours * 12))
    return max(0, round(100 - age_hours / 24 * 10))
