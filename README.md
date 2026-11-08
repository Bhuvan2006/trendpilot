# TrendPilot AI

A multi-agent system that finds live AI and behavioural-science discussions, scores them for Dots-In's content pillars, drafts LinkedIn and Instagram posts, reviews them, and publishes only what a human approves.

Independent interview prototype, not affiliated with or endorsed by Dots-In. It uses only public information. Publishing is a dry run, so nothing is really posted.

Design: [docs/DESIGN.md](docs/DESIGN.md) · Demo script: [docs/DEMO.md](docs/DEMO.md) · Git history: [docs/GIT_DIAGRAM.md](docs/GIT_DIAGRAM.md)

## Status

59 passing tests, run offline, cover the real Agno Workflow, the API and the approval gate. Two full runs on live Hacker News and arXiv data with `nvidia/nemotron-3-ultra-550b-a55b` completed in 5.5 and 7 minutes, used 90,139 and 125,868 input tokens (16,014 and 21,622 output), and each produced 4 reviewed drafts from real sources. In the second run the Brand Reviewer flagged 2 drafts for overclaiming about Dots-In. Two runs are not a benchmark.

## Setup (Windows PowerShell)

```powershell
uv venv .venv --python 3.13
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
Copy-Item .env.example .env
```

Put your key in `.env`. The default is NVIDIA's hosted model (`NVIDIA_API_KEY`). `MODEL_PROVIDER` can also be `anthropic` or `openrouter`, each with its own key.

## Run

Real model:

```powershell
.\.venv\Scripts\python.exe -m app.main
```

Offline mock, with all data clearly labelled and kept in a separate database:

```powershell
$env:MODEL_PROVIDER='mock'; $env:TRENDPILOT_DB='mock.db'; .\.venv\Scripts\python.exe -m app.main
```

Then open http://127.0.0.1:8000.

Tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## How it maps to the brief

| Asked for | Where |
|---|---|
| Agno framework | `app/agents.py`, `app/pipeline.py` |
| Multi-agent orchestration | `app/pipeline.py`: parallel collection, four specialists, one bounded revision |
| Tools | `app/agents.py`, `app/sources.py`, scoped per agent |
| Memory management | `app/memory.py`: brand, campaign and learning stores with capped retrieval |
| Harness | `app/harness.py`, `app/guard.py`, `app/publishing.py` |
| SKILL.md | `skills/`, loaded on demand through Agno Skills |

## Limitations

- Tested end to end on one real model only. Claude and OpenRouter calls failed on account credit limits, so those providers are unverified.
- A real run takes 5.5 to 7 minutes on the NVIDIA model. NVIDIA's hosted endpoint is unbilled, so the spend cap has no effect there.
- The spend cap is checked before each agent run, not each model request, so one agent run can overshoot it slightly.
- Brand facts come from the company's public website, are unverified, and have not been reviewed by Dots-In.
- Publishing is a dry run. A real LinkedIn adapter needs an OAuth app and page permission.
- Score weights are a starting heuristic, not a validated model.
- The numeric-claim guard only catches numbers tied to a unit or claim word.
- It binds to localhost with no login and must not be deployed as is.
- Free model tiers have tight daily limits, and a run makes many model requests.
