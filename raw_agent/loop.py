"""The hand-written agent loop.

This is the heart of the "no SDK" implementation, and it is deliberately
short: ask the model, run the tools it requests, return the results, repeat
until it stops asking. Everything that makes it production-grade lives in the
pieces it delegates to: budgets (`core.guards`), tools and their safety checks
(`core.tools`), retries (`raw_agent.client`) and wire format
(`raw_agent.providers`).
"""

# 📘 [בלי SDK] זו הלולאה שכתבנו ביד, וזה הלב של ההשוואה.
# 📘 [עם SDK] בגרסה השנייה כל הלולאה הזו מוחלפת בקריאה אחת לספרייה, והספרייה מריצה את הלולאה
# 📘 בעצמה.

from __future__ import annotations

from core.guards import Budget, BudgetExceeded
from core.harness import EngineOutcome
from core.prompts import SYSTEM_PROMPT, TASK_PROMPT
from core.telemetry import Tracer
from core.tools import Toolbox
from core.types import RunStatus, ToolResult
from raw_agent.client import APIError
from raw_agent.providers.base import ModelProvider, StopReason, ToolCall

_CONTINUE_AFTER_TRUNCATION = (
    "Your last response hit the output limit and was cut off. Continue from where you "
    "stopped, more concisely."
)


# 📘 Engine הוא החוזה המשותף לשני האיגנטים (מוגדר ב-core/harness.py). כל איגנט מממש רק את solve,
# 📘 וכל השאר משותף.
class RawEngine:
    """Implements `core.harness.Engine` with an explicit loop over a `ModelProvider`."""

    name = "raw_agent"

    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider

    def solve(self, toolbox: Toolbox, budget: Budget, tracer: Tracer) -> EngineOutcome:
        conversation = self._provider.new_conversation(SYSTEM_PROMPT, toolbox.specs())
        # 📘 פותחים שיחה: הוראות המערכת, רשימת הכלים והמשימה עצמה.
        conversation.add_user_text(TASK_PROMPT)

        try:
            # 📘 הלולאה: 1. שואלים את המודל. 2. אם ביקש כלים, מריצים אותם ומחזירים לו את התוצאות.
            # 📘 3. חוזרים על זה עד שהוא מסיים או שנגמר התקציב.
            while True:
                # 📘 לפני כל סבב בודקים שלא עברנו את מספר הסבבים המותר. אם עברנו, נזרקת
                # 📘 BudgetExceeded והלולאה נעצרת.
                budget.record_iteration()
                with tracer.span("model_call", iteration=budget.iterations) as span:
                    turn = conversation.send()
                    span.update(
                        stop_reason=turn.raw_stop_reason,
                        model=turn.model,
                        input_tokens=turn.usage.input_tokens,
                        output_tokens=turn.usage.output_tokens,
                        cache_read_input_tokens=turn.usage.cache_read_input_tokens,
                        cache_creation_input_tokens=turn.usage.cache_creation_input_tokens,
                        tool_calls=[c.name for c in turn.tool_calls],
                        text=turn.text,
                    )
                # 📘 אחרי כל תשובה מוסיפים את הטוקנים לחשבון, ובודקים את תקרת הטוקנים ואת תקרת
                # 📘 העלות.
                budget.record_usage(turn.usage)

                # 📘 המקרה הנפוץ: המודל ביקש להפעיל כלי אחד או יותר.
                if turn.stop_reason is StopReason.TOOL_USE and turn.tool_calls:
                    results = [self._run_tool(call, toolbox, tracer) for call in turn.tool_calls]
                    conversation.add_tool_results(results)
                # 📘 המודל סיים וכתב דוח. שימי לב שמחזירים UNRESOLVED: מי שקובע אם הבאג תוקן הוא
                # 📘 הבודק העצמאי, ולא המודל.
                elif turn.stop_reason is StopReason.END_TURN:
                    return EngineOutcome(RunStatus.UNRESOLVED, final_report=turn.text)
                # 📘 התשובה נחתכה באמצע. אם היא נחתכה בתוך בקשה לכלי, לא מריצים אותה, כי הפרמטרים
                # 📘 עלולים להיות חלקיים.
                elif turn.stop_reason is StopReason.MAX_TOKENS:
                    if turn.tool_calls:
                        # A cut-off tool call may carry truncated input. Answer every
                        # call so the transcript stays valid, but do not run them.
                        conversation.add_tool_results(
                            [
                                (
                                    c,
                                    ToolResult(
                                        "Call cut off by the output limit; resend it.", True
                                    ),
                                )
                                for c in turn.tool_calls
                            ]
                        )
                    else:
                        conversation.add_user_text(_CONTINUE_AFTER_TRUNCATION)
                elif turn.stop_reason is StopReason.REFUSAL:
                    return EngineOutcome(
                        RunStatus.ERROR,
                        final_report=turn.text,
                        error=f"model declined the request: {turn.extra.get('stop_details')}",
                    )
                else:
                    return EngineOutcome(
                        RunStatus.ERROR,
                        final_report=turn.text,
                        error=f"unexpected stop reason: {turn.raw_stop_reason}",
                    )
        # 📘 [בלי SDK] את הטיפול בעצירה ובשגיאות כתבנו בעצמנו. ב-SDK חלק מזה מובנה.
        except BudgetExceeded as exc:
            tracer.event("budget_exceeded", reason=str(exc))
            return EngineOutcome(RunStatus.BUDGET_EXCEEDED, error=str(exc))
        except APIError as exc:
            tracer.event("api_error", error=str(exc), error_class=type(exc).__name__)
            return EngineOutcome(RunStatus.ERROR, error=f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _run_tool(call: ToolCall, toolbox: Toolbox, tracer: Tracer) -> tuple[ToolCall, ToolResult]:
        with tracer.span("tool_call", tool=call.name, arguments=call.arguments) as span:
            result = toolbox.dispatch(call.name, call.arguments)
            span.update(is_error=result.is_error, output_chars=len(result.output))
        return call, result
