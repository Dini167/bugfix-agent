"""The bugfix agent in the browser. Start it with `bugfix ui`."""

# 📘 הממשק הגרפי, בנוי עם Streamlit. Streamlit מריץ את הקובץ הזה מלמעלה למטה בכל לחיצה, ובונה ממנו
# 📘 דף אינטרנט שנפתח בדפדפן. כל ההיגיון נמצא ב-ui/logic.py, וכאן רק מציירים את המסך.

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import streamlit as st
from streamlit.delta_generator import DeltaGenerator

from cli.engines import EngineSetupError
from core.changes import apply_changes
from core.config import Settings
from core.types import RunResult, RunStatus
from ui import logic

st.set_page_config(page_title="Bugfix agent", page_icon="🛠️", layout="wide")

ICONS = {"info": "📋", "think": "💭", "tool": "🔧", "warn": "⚠️"}
AGENTS = {"raw": "Without SDK", "sdk": "With Claude Agent SDK"}

# ------------------------------------------------------------------ sidebar: settings

with st.sidebar:
    st.header("Settings")
    model_choice = st.selectbox("Model", [*logic.CLAUDE_MODELS, "Free model..."])
    model = (
        st.text_input("Free model", placeholder="ollama:<model> or groq:<model>")
        if model_choice == "Free model..."
        else model_choice
    )
    fallbacks = st.text_input(
        "Backup models (optional)",
        placeholder="groq:<model>, ollama:<model>",
        help="Used in order if the main model fails. Works with the agent without SDK only.",
    )
    max_cost = st.number_input("Stop if a run costs more than ($)", 0.01, 10.0, 0.50, 0.05)

    st.divider()
    st.subheader("API key")
    if os.environ.get("ANTHROPIC_API_KEY"):
        st.success("ANTHROPIC_API_KEY is set.")
    else:
        typed = st.text_input(
            "ANTHROPIC_API_KEY",
            type="password",
            help="Kept in memory for this session only. Never saved or shown.",
        )
        if typed:
            os.environ["ANTHROPIC_API_KEY"] = typed.strip()
            st.rerun()

settings = replace(
    Settings.from_env(),
    model=model or logic.CLAUDE_MODELS[0],
    fallback_models=fallbacks,
    max_cost_usd=float(max_cost),
)

# ------------------------------------------------------------------ main: what to fix

st.title("🛠️ Bugfix agent")
st.caption(
    "Finds the bug behind failing tests, fixes it on a private copy, and proves the fix "
    "by running the whole test suite. Your files change only if you click Apply."
)

source = st.radio("What to fix", ["A sample bug", "My own project"], horizontal=True)
sample = None
if source == "A sample bug":
    cases = logic.sample_cases()
    sample = st.selectbox("Sample bug", cases, format_func=logic.sample_label)
    repo = sample.repo
    st.info(f"**The bug:** {sample.title}. Sample bugs are never changed, so you can rerun them.")
else:
    repo = Path(st.text_input("Project folder", placeholder=r"C:\Users\me\my-project")).expanduser()

problem = logic.is_supported(repo) if str(repo) not in ("", ".") else "Enter a project folder."

col_check, _ = st.columns([1, 3])
if col_check.button("Check the tests", disabled=bool(problem)):
    with st.spinner("Running the tests..."):
        runner, outcome = logic.check_project(repo, settings)
    if outcome.all_passed:
        st.success(f"{runner}: all {outcome.passed} tests pass. Nothing to fix.")
    else:
        st.error(f"{runner}: {outcome.failed + outcome.errors} failing, {outcome.passed} passing.")
    with st.expander("Test output"):
        st.code(outcome.output or "(no output)")
if problem and source == "My own project" and str(repo) not in ("", "."):
    st.warning(problem)

st.divider()

mode = st.radio(
    "Agent",
    ["Without SDK", "With Claude Agent SDK", "Compare both"],
    horizontal=True,
    help="Both agents use the same tools, prompt and model. Only the loop differs.",
)
engines = {"Without SDK": ["raw"], "With Claude Agent SDK": ["sdk"]}.get(mode, ["raw", "sdk"])


def show_result(result: RunResult) -> None:
    good = result.status is RunStatus.RESOLVED
    text = logic.STATUS_TEXT.get(result.status, result.status.value)
    (st.success if good else st.error)(text + (f": {result.error}" if result.error else ""))
    a, b, c = st.columns(3)
    a.metric("Steps", result.iterations)
    b.metric("Time", f"{result.wall_time_s:.1f} s")
    c.metric("Cost", f"${result.cost_usd:.4f}")
    if result.final_report:
        with st.expander("The agent's report", expanded=good):
            st.markdown(result.final_report)
    if result.patch:
        st.code(result.patch, language="diff")


# ------------------------------------------------------------------ run

missing = logic.missing_key(settings)
if missing:
    st.warning(f"{missing} is not set. Add it in the sidebar or as an environment variable.")

if st.button("🔍 Find and fix the bug", type="primary", disabled=bool(problem or missing)):
    st.session_state.results = {}
    columns = st.columns(len(engines))
    for engine, column in zip(engines, columns, strict=True):
        with column:
            st.subheader(AGENTS[engine])
            live = st.status("Working on a private copy...", expanded=True)

            def on_step(step: logic.Step, live: DeltaGenerator = live) -> None:
                live.write(f"{ICONS[step.kind]} {step.text}")

            try:
                result = logic.run_agent(engine, repo, settings, on_step)
            except EngineSetupError as exc:
                live.update(label=str(exc), state="error")
                continue
            done = result.status is RunStatus.RESOLVED
            live.update(
                label=f"{'Fixed' if done else 'Finished'} in {result.iterations} steps "
                "(click to see them)",
                state="complete" if done else "error",
            )
            st.session_state.results[engine] = result
    st.session_state.repo = str(repo)
    st.session_state.is_sample = sample is not None

# Results stay on screen after any click (Streamlit reruns the page each time).
results: dict[str, RunResult] = st.session_state.get("results", {})
if results:
    if len(results) > 1:
        st.subheader("Side by side")
        named = {AGENTS[k]: v for k, v in results.items()}
        st.table(logic.comparison_rows(named))
    for engine, result in results.items():
        st.subheader(f"Result: {AGENTS[engine]}")
        show_result(result)

    fixed = [r for r in results.values() if r.status is RunStatus.RESOLVED and r.changes]
    if fixed and not st.session_state.get("is_sample"):
        target = Path(st.session_state.repo)
        if st.button(f"✅ Apply this fix to {target.name}"):
            try:
                changed = apply_changes(target, fixed[0].changes)
                st.success(f"Applied to {', '.join(changed)}.")
            except RuntimeError as exc:
                st.error(str(exc))
