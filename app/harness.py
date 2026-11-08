"""The control layer around the agents: what they may call, how often, what they must return and what they may spend."""
import json
import re
import time

from pydantic import BaseModel, ValidationError

from app import db

SKILL_TOOLS = {"get_skill_instructions"}
POLICY = {
    "Trend Scout": {"search_hackernews"} | SKILL_TOOLS,
    "Relevance Strategist": {"search_brand_knowledge", "campaign_history"} | SKILL_TOOLS,
    "Content Studio": {"search_brand_knowledge", "get_learnings"} | SKILL_TOOLS,
    "Brand Reviewer": {"search_brand_knowledge"} | SKILL_TOOLS,
}
MAX_TOOL_CALLS = 6
MAX_ATTEMPTS = 2
# USD per million tokens, (input, output). Cached reads are charged at the full rate, so cost is a safe overestimate.
PRICES = {"claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.0, 20.0), "mock": (0.0, 0.0)}


class BudgetExceeded(Exception):
    pass


class StepFailed(Exception):
    pass


class Harness:
    def __init__(self, path, run_id, max_cost_usd, model_id, backoff_s=2.0):
        if model_id not in PRICES:
            raise ValueError(f"no price known for {model_id}, so the spend cap cannot work. Add it to PRICES.")
        self.path, self.run_id, self.max_cost_usd, self.model_id, self.backoff_s = path, run_id, max_cost_usd, model_id, backoff_s
        self.cost_usd, self.input_tokens, self.output_tokens = 0.0, 0, 0
        self.tool_calls: dict[str, int] = {}

    def tool_hook(self, agent_name):
        """Agno calls this around every tool an agent uses. Returning text instead of raising lets the agent recover."""

        def hook(function_name, function_call, arguments):
            count = self.tool_calls.get(agent_name, 0) + 1
            self.tool_calls[agent_name] = count
            if function_name not in POLICY.get(agent_name, set()):
                reason = f"{function_name} is not permitted for {agent_name}"
            elif count > MAX_TOOL_CALLS:
                reason = f"tool call limit of {MAX_TOOL_CALLS} reached"
            else:
                reason = None
            if reason:
                db.audit(self.path, self.run_id, agent_name, "tool_call", function_name, allowed=False, detail=reason)
                return f"DENIED by harness: {reason}"
            started = time.monotonic()
            result = function_call(**arguments)
            db.audit(self.path, self.run_id, agent_name, "tool_call", function_name, detail=json.dumps(arguments, default=str),
                     duration_ms=int((time.monotonic() - started) * 1000))
            return result

        return hook

    def _record(self, agent_name, out, started, attempt):
        metrics = getattr(out, "metrics", None)
        i, o = (getattr(metrics, "input_tokens", 0) or 0), (getattr(metrics, "output_tokens", 0) or 0)
        input_price, output_price = PRICES[self.model_id]
        self.input_tokens += i
        self.output_tokens += o
        self.cost_usd += (i * input_price + o * output_price) / 1_000_000
        db.audit(self.path, self.run_id, agent_name, "agent_run", f"attempt {attempt}", input_tokens=i, output_tokens=o,
                 duration_ms=int((time.monotonic() - started) * 1000))
        with db.connect(self.path) as c:
            c.execute("UPDATE runs SET input_tokens = ?, output_tokens = ?, cost_usd = ? WHERE id = ?",
                      (self.input_tokens, self.output_tokens, self.cost_usd, self.run_id))

    @staticmethod
    def _parse(content, schema):
        if isinstance(content, BaseModel):
            return schema.model_validate(content.model_dump())
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(content).strip())
        return schema.model_validate_json(text)

    def run_agent(self, agent, prompt: str, schema):
        self.tool_calls[agent.name] = 0
        message, last_error = prompt, ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self.cost_usd >= self.max_cost_usd:
                db.audit(self.path, self.run_id, agent.name, "budget", self.max_cost_usd, allowed=False, detail=f"spent ${self.cost_usd:.4f}")
                raise BudgetExceeded(f"spent ${self.cost_usd:.4f} of a ${self.max_cost_usd:.2f} cap")
            started = time.monotonic()
            try:
                out = agent.run(message)
            except Exception as e:  # a network or provider failure is retried once, then fails the step
                last_error = f"{type(e).__name__}: {e}"
                db.audit(self.path, self.run_id, agent.name, "agent_error", f"attempt {attempt}", allowed=False, detail=last_error)
                time.sleep(self.backoff_s * attempt)
                continue
            self._record(agent.name, out, started, attempt)
            # Agno reports some provider failures as an error status rather than raising.
            if str(getattr(out, "status", "")).lower().endswith("error"):
                last_error = str(out.content)
                db.audit(self.path, self.run_id, agent.name, "agent_error", f"attempt {attempt}", allowed=False, detail=last_error)
                time.sleep(self.backoff_s * attempt)
                continue
            try:
                return self._parse(out.content, schema)
            except (ValidationError, ValueError) as e:
                last_error = str(e)[:600]
                db.audit(self.path, self.run_id, agent.name, "invalid_output", f"attempt {attempt}", allowed=False, detail=last_error)
                message = f"{prompt}\n\nYour previous output was invalid: {last_error}\nReturn only output matching the required schema."
        raise StepFailed(f"{agent.name} failed after {MAX_ATTEMPTS} attempts: {last_error}")
