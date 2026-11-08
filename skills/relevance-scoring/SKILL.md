---
name: relevance-scoring
description: Score candidate topics against the Dots-In content pillars and recommend an angle. Use when ranking topics for marketing.
---

# Relevance scoring

Look up the brand with `search_brand_knowledge`, and check `campaign_history` so you do not repeat a recent angle.

Score each topic from 0 to 100:

| Field | Meaning | 80 or more means |
|---|---|---|
| `relevance` | Would the target audience care? | People who follow AI, productivity or self-improvement would stop for it |
| `brand_fit` | Does it connect to a pillar honestly? | The link needs no stretching |
| `evidence` | How solid is the source? | Primary research or detailed technical discussion |
| `content_potential` | Can it become a useful post? | There is a clear idea to teach |

Do not score momentum. Code computes it from real engagement data.

Most topics should score below 70. Give a low `brand_fit` instead of forcing a connection. `reason` explains the score in one sentence. `angle` is the specific point a post would make.
