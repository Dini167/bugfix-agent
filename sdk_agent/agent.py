"""The same agent, on the Claude Agent SDK.

Compare with `raw_agent/`: there is no HTTP client, no wire format, no
transcript management and no loop here. The SDK owns all of that. This file
only wires the shared pieces into it:

* the shared tools are exposed as in-process MCP tools;
* every built-in SDK tool (Bash, Read, Edit, ...) is disabled, so both agents
  have exactly the same capabilities;
* user and project settings are not loaded, so nothing on the host machine
  changes the agent's behavior;
* the shared system prompt, model, effort and budgets are passed through.

The SDK's own caps (`max_turns`, `max_budget_usd`) are set from the same
settings, and the shared `Budget` is still updated from the message stream, so
budget accounting is identical across both implementations.
"""

# 📘 [עם SDK] זו כל הגרסה השנייה. שימי לב מה אין כאן: אין לקוח HTTP, אין טיפול בפורמט של ה-API,
# 📘 אין ניהול של היסטוריית השיחה ואין לולאה. את כל אלה ה-SDK עושה בשבילנו.
# 📘 [בלי SDK] בגרסה הראשונה כל אלה נכתבו ביד, בארבעה קבצים: client.py, providers/base.py,
# 📘 providers/anthropic.py ו-loop.py.
# 📘 מה שכן נשאר לנו לעשות כאן: לחבר את הכלים המשותפים ל-SDK, לכבות את הכלים המובנים שלו, ולקרוא
# 📘 את ההודעות שהוא מחזיר.

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    Message,
    ResultMessage,
    SdkMcpTool,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from core.config import Settings
from core.guards import Budget, BudgetExceeded
from core.harness import EngineOutcome
from core.prompts import SYSTEM_PROMPT, TASK_PROMPT
from core.telemetry import Tracer
from core.tools import Toolbox
from core.types import RunStatus, Usage

# 📘 [עם SDK] ה-SDK מקבל כלים מותאמים אישית דרך "שרת MCP" שרץ בתוך התהליך שלנו. זה השם של השרת.
SERVER_NAME = "bugfix"

QueryFn = Callable[..., AsyncIterator[Message]]

# ResultMessage subtypes that mean "a cap was hit", not "something broke".
_BUDGET_SUBTYPES = frozenset({"error_max_turns", "error_max_budget_usd"})


# 📘 ה-SDK נותן לכל כלי MCP שם בפורמט mcp__<שרת>__<כלי>. צריך את השם הזה כדי לאשר את הכלים מראש.
def mcp_tool_name(name: str) -> str:
    """The name the SDK exposes an in-process MCP tool under."""
    return f"mcp__{SERVER_NAME}__{name}"


# 📘 [עם SDK] עוטפים כל אחד מחמשת הכלים המשותפים ככלי של ה-SDK. אותו קוד ואותו JSON Schema כמו
# 📘 בגרסה הראשונה, רק במעטפת אחרת.
# 📘 [בלי SDK] שם ההמרה לפורמט של ה-API נכתבה ביד ב-to_api_tools.
def build_tools(toolbox: Toolbox, tracer: Tracer) -> list[SdkMcpTool[Any]]:
    """Wrap each shared tool as an SDK MCP tool. Same code, same schemas."""
    tools: list[SdkMcpTool[Any]] = []
    for spec in toolbox.specs():

        async def handler(arguments: dict[str, Any], _name: str = spec.name) -> dict[str, Any]:
            def run() -> tuple[str, bool]:
                with tracer.span("tool_call", tool=_name, arguments=arguments) as span:
                    result = toolbox.dispatch(_name, arguments)
                    span.update(is_error=result.is_error, output_chars=len(result.output))
                return result.output, result.is_error

            # Tools run subprocesses (pytest), so keep them off the event loop.
            # 📘 ה-SDK עובד בצורה אסינכרונית (async). הכלים מריצים pytest, שלוקח זמן, ולכן מריצים
            # 📘 אותם ב-thread נפרד כדי לא לתקוע את ה-SDK.
            output, is_error = await asyncio.to_thread(run)
            response: dict[str, Any] = {"content": [{"type": "text", "text": output}]}
            if is_error:
                response["is_error"] = True
            return response

        tools.append(tool(spec.name, spec.description, spec.input_schema)(handler))
    return tools


# 📘 [עם SDK] כאן נמצא רוב ה"קוד" של הגרסה השנייה: הגדרות. כל שורה מחליפה משהו שבגרסה הראשונה
# 📘 כתבנו בעצמנו או שלא היה צריך שם.
def build_options(
    settings: Settings, toolbox: Toolbox, tools: list[SdkMcpTool[Any]]
) -> ClaudeAgentOptions:
    """SDK configuration that matches the raw agent's capabilities exactly."""
    server = create_sdk_mcp_server(SERVER_NAME, tools=tools)
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        model=settings.model,
        effort=settings.effort,  # type: ignore[arg-type]
        # 📘 מכבים את כל הכלים המובנים של ה-SDK (Bash, Read, Edit ועוד), כדי שלשני האיגנטים יהיו
        # 📘 בדיוק אותן יכולות. בלי זה ההשוואה לא הוגנת.
        tools=[],  # disable every built-in tool
        mcp_servers={SERVER_NAME: server},
        strict_mcp_config=True,  # ignore any MCP servers configured on the host
        allowed_tools=[mcp_tool_name(t.name) for t in tools],
        # 📘 כל כלי שלא אושר מראש נחסם אוטומטית, בלי לשאול.
        permission_mode="dontAsk",  # anything not pre-approved is denied
        # 📘 לא טוענים הגדרות מהמחשב שעליו רצים, כך שההתנהגות זהה בכל מחשב.
        setting_sources=[],  # do not load user/project settings or CLAUDE.md
        cwd=str(toolbox.workspace_root),
        # 📘 [עם SDK] תקרות מובנות של ה-SDK: מספר סבבים ותקציב בדולרים. [בלי SDK] בגרסה הראשונה
        # 📘 את זה בדקנו בעצמנו בכל סבב.
        max_turns=settings.max_iterations,
        max_budget_usd=settings.max_cost_usd,
    )


def _usage(raw: dict[str, Any] | None) -> Usage:
    raw = raw or {}
    return Usage(
        input_tokens=int(raw.get("input_tokens") or 0),
        output_tokens=int(raw.get("output_tokens") or 0),
        cache_read_input_tokens=int(raw.get("cache_read_input_tokens") or 0),
        cache_creation_input_tokens=int(raw.get("cache_creation_input_tokens") or 0),
    )


# 📘 המימוש של אותו חוזה Engine כמו בגרסה הראשונה. ה-harness לא יודע ולא אכפת לו איזה מנוע רץ.
class SdkEngine:
    """Implements `core.harness.Engine` on top of `claude_agent_sdk.query`."""

    name = "sdk_agent"

    # 📘 query הוא הפונקציה של ה-SDK שמריצה את כל הלולאה. מקבלים אותה כפרמטר כדי שבטסטים אפשר
    # 📘 יהיה להחליף אותה בגרסה מדומה, בלי מפתח API.
    def __init__(self, query_fn: QueryFn = query) -> None:
        self.query_fn = query_fn
        self.tools: dict[str, SdkMcpTool[Any]] = {}

    def solve(self, toolbox: Toolbox, budget: Budget, tracer: Tracer) -> EngineOutcome:
        return asyncio.run(self._solve(toolbox, budget, tracer))

    async def _solve(self, toolbox: Toolbox, budget: Budget, tracer: Tracer) -> EngineOutcome:
        tools = build_tools(toolbox, tracer)
        self.tools = {t.name: t for t in tools}
        options = build_options(budget.settings, toolbox, tools)

        seen_messages: set[str] = set()
        texts: list[str] = []
        result: ResultMessage | None = None
        try:
            # 📘 [עם SDK] זו כל "הלולאה": קריאה אחת. ה-SDK מריץ את הלולאה בעצמו, ואנחנו רק קוראים
            # 📘 את ההודעות שהוא מזרים.
            # 📘 [בלי SDK] בגרסה הראשונה זה היה while True עם טיפול בכל סוג של stop_reason.
            stream = self.query_fn(prompt=TASK_PROMPT, options=options)
            try:
                # 📘 שני סוגי הודעות מעניינים אותנו: AssistantMessage היא תשובה של המודל,
                # 📘 ו-ResultMessage היא הסיכום של כל הריצה.
                async for message in stream:
                    if isinstance(message, AssistantMessage):
                        self._on_assistant(message, budget, tracer, seen_messages, texts)
                    elif isinstance(message, ResultMessage):
                        result = message
            finally:
                # Stopping early (e.g. a budget cap) must also stop the SDK's subprocess.
                # 📘 אם עצרנו באמצע (למשל כי נגמר התקציב), סוגרים את הזרם, וזה עוצר גם את התהליך
                # 📘 של ה-SDK.
                aclose = getattr(stream, "aclose", None)
                if aclose is not None:
                    await aclose()
        except BudgetExceeded as exc:
            tracer.event("budget_exceeded", reason=str(exc))
            return EngineOutcome(RunStatus.BUDGET_EXCEEDED, error=str(exc))
        except ClaudeSDKError as exc:
            tracer.event("sdk_error", error=str(exc), error_class=type(exc).__name__)
            return EngineOutcome(RunStatus.ERROR, error=f"{type(exc).__name__}: {exc}")

        if result is None:
            return EngineOutcome(RunStatus.ERROR, error="SDK stream ended without a result")
        return self._outcome(result, budget, texts)

    # 📘 ה-SDK לפעמים מחלק תשובה אחת של המודל לכמה הודעות. סופרים כל תשובה פעם אחת לפי
    # 📘 message_id, כדי שהספירה תהיה זהה לגרסה הראשונה.
    @staticmethod
    def _on_assistant(
        message: AssistantMessage,
        budget: Budget,
        tracer: Tracer,
        seen: set[str],
        texts: list[str],
    ) -> None:
        """Count each model response once, even if the SDK streams it in several parts."""
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                texts.append(block.text)
                tracer.event("model_text", text=block.text)
        key = message.message_id or str(id(message))
        if key in seen:
            return
        seen.add(key)
        budget.record_iteration()
        usage = _usage(message.usage)
        tracer.event(
            "model_call",
            iteration=budget.iterations,
            model=message.model,
            stop_reason=message.stop_reason,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
            tool_calls=[b.name for b in message.content if isinstance(b, ToolUseBlock)],
        )
        budget.record_usage(usage)

    # 📘 מתרגמים את הסיכום של ה-SDK לתוצאה המשותפת שלנו. גם כאן הסטטוס הוא לכל היותר UNRESOLVED,
    # 📘 ומי שקובע אם הבאג תוקן הוא הבודק העצמאי.
    @staticmethod
    def _outcome(result: ResultMessage, budget: Budget, texts: list[str]) -> EngineOutcome:
        # The result's usage is the SDK's authoritative total for the whole run.
        if result.usage:
            # 📘 [עם SDK] ה-SDK מחזיר בסוף סיכום מדויק של כל הטוקנים. [בלי SDK] בגרסה הראשונה
            # 📘 סכמנו אותם בעצמנו מכל תשובה.
            budget.usage = _usage(result.usage)
        report = result.result or (texts[-1] if texts else "")
        if result.subtype in _BUDGET_SUBTYPES:
            return EngineOutcome(
                RunStatus.BUDGET_EXCEEDED, final_report=report, error=result.subtype
            )
        if result.is_error:
            detail = "; ".join(result.errors or []) or result.subtype
            return EngineOutcome(RunStatus.ERROR, final_report=report, error=detail)
        return EngineOutcome(RunStatus.UNRESOLVED, final_report=report)
