"""The Planner: turns the conversation and the Bank's Operation catalogue into a Plan.

It always plans, even for one step, unless it declines the request as out of scope
(Wealth Operations, non-banking topics). The Operations it may use, and those it offers
instead when it declines, come from the catalogue the Bank publishes, never from a list in
the Agent.
"""

import json
from graphlib import TopologicalSorter
from typing import Any, Literal

from pydantic import BaseModel

from agent.events import PlanStep
from agent.model import ModelClient

PLAN_TOOL = "propose_plan"
DECLINE_TOOL = "decline_request"
MAX_OFFERED = 5
"""At most this many Operations are offered when a request is declined."""

SYSTEM_PROMPT = """\
你是一家银行的 AI 助手（Agent），代表当前客户向银行发起业务操作（Operation）。
You are a bank's AI Agent, requesting Operations from the Bank on the current Customer's behalf.

把客户最新的请求变成一个计划（Plan）：一组银行操作及其输入，按依赖关系组成有向无环图。
Turn the Customer's latest request into a Plan: the Operations to run, with their inputs,
as a dependency graph. Always call the `{plan_tool}` tool, even for a single step.
Only use Operations from the catalogue below, with inputs that match their input schema.
The Bank decides each Operation's Permission Tier and whether it may run.

理财业务（产品推荐、风险测评、申购、赎回）和与银行无关的话题不在服务范围内：不要计划，改为调用
`{decline_tool}`，并从目录中挑选几项对该客户最有用的操作作为替代。
Wealth Operations (product recommendations, risk assessment, subscribing or redeeming) and
non-banking topics are out of scope: don't plan them. Call `{decline_tool}` instead, offering
the few catalogue Operations most useful to this Customer.

Operation catalogue (data from the Bank):
{catalogue}
"""


class Plan(BaseModel):
    intent: str = ""
    """What the model understood the Customer wants, for the decision trace."""
    steps: list[PlanStep]

    def in_dependency_order(self) -> list[PlanStep]:
        """The steps so that each comes after the steps it depends on, otherwise as planned."""
        by_id = {step.id: step for step in self.steps}
        sorter = TopologicalSorter({step.id: step.depends_on for step in self.steps})
        sorter.prepare()
        ordered: list[PlanStep] = []
        while sorter.is_active():
            ready = sorted(sorter.get_ready(), key=lambda id: self.steps.index(by_id[id]))
            ordered.extend(by_id[id] for id in ready)
            sorter.done(*ready)
        return ordered


class Declined(BaseModel):
    """The Planner won't plan the request; `can_do` names Operations to offer instead."""

    reason: Literal["out_of_scope"]
    can_do: list[str] = []


class PlannerFailed(Exception):
    pass


def operation_names(catalogue: list[dict[str, Any]]) -> list[str]:
    return [op["name"] for op in catalogue]


def plan_tool(catalogue: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": PLAN_TOOL,
            "description": "Propose the Plan for the Customer's latest request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "description": "The Customer's request as you understood it, in one"
                        " short sentence in the Customer's language",
                    },
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "Unique, e.g. s1"},
                                "operation": {
                                    "type": "string",
                                    "enum": operation_names(catalogue),
                                },
                                "input": {"type": "object"},
                                "depends_on": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "IDs of steps that must run first",
                                },
                            },
                            "required": ["id", "operation", "input", "depends_on"],
                        },
                    }
                },
                "required": ["intent", "steps"],
            },
        },
    }


def decline_tool(catalogue: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": DECLINE_TOOL,
            "description": (
                "Decline a request the Agent can't serve, offering what it can do instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "enum": ["out_of_scope"]},
                    "can_do": {
                        "type": "array",
                        "items": {"type": "string", "enum": operation_names(catalogue)},
                        "maxItems": MAX_OFFERED,
                        "description": "Catalogue Operations to offer the Customer instead",
                    },
                },
                "required": ["reason", "can_do"],
            },
        },
    }


def describe(catalogue: list[dict[str, Any]]) -> str:
    entries = [
        {
            "name": op["name"],
            "description_zh": op["description_zh"],
            "description_en": op["description_en"],
            "input_schema": op["input_schema"],
            "tier": op["tier"],
        }
        for op in catalogue
    ]
    return json.dumps(entries, ensure_ascii=False, indent=1)


def make_plan(
    model: ModelClient, history: list[dict[str, Any]], catalogue: list[dict[str, Any]]
) -> Plan | Declined:
    system = SYSTEM_PROMPT.format(
        plan_tool=PLAN_TOOL, decline_tool=DECLINE_TOOL, catalogue=describe(catalogue)
    )
    reply = model.chat(
        [{"role": "system", "content": system}, *history],
        [plan_tool(catalogue), decline_tool(catalogue)],
    )
    for call in reply.tool_calls:
        if call.name == PLAN_TOOL:
            return Plan.model_validate(call.arguments)
        if call.name == DECLINE_TOOL:
            return Declined.model_validate(call.arguments)
    raise PlannerFailed(f"the model did not propose a Plan: {reply.content!r}")
