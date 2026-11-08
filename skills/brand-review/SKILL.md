---
name: brand-review
description: Check a social media draft for unsupported claims, tone and brand fit. Use before a draft goes to human review.
---

# Brand review

1. List every factual claim in the draft.
2. For each claim about Dots-In, confirm it with `search_brand_knowledge`. For each outside claim, confirm it against the supplied source summary.
3. Check that the tone is confident, honest and free of unverifiable superlatives.

Set `approved` to false if any claim is unsupported, if a company statement is presented as proven fact, or if the post promises a health or productivity outcome.

`score` runs from 0 to 100. Put each unsupported claim in `unsupported_claims` and a concrete fix for it in `suggested_edits`. `explanation` is at most two sentences. You review only. You never publish or schedule.
