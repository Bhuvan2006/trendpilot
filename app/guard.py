"""Deterministic content checks. They run even if every model in the pipeline is wrong."""
import re

LIMITS = {"linkedin": 3000, "instagram": 2200}
BANNED = ("world's only", "world's first", "guarantee", "clinically proven", "cure", "100% private")
# shortcut: only catches numbers tied to a unit or claim word, plain counts pass; add an LLM-free claim extractor if misses appear.
NUMERIC_CLAIM = re.compile(r"\b\d[\d,.]*\s?(?:%|x\b|×|percent|users|customers|million|billion|k\b)", re.I)


def check_draft(body: str, platform: str, allowed_text: str) -> list[str]:
    problems = []
    if len(body) > LIMITS[platform]:
        problems.append(f"length {len(body)} exceeds {platform} limit {LIMITS[platform]}")
    lowered = body.lower().replace("’", "'")
    problems += [f"banned phrase: {p}" for p in BANNED if re.search(rf"\b{re.escape(p)}", lowered)]  # word start, so "secure" is not "cure"
    problems += [f"unsupported number: {m.group(0)}" for m in NUMERIC_CLAIM.finditer(body) if m.group(0).lower() not in allowed_text.lower()]
    return problems
