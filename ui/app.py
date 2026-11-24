"""The bugfix agent in the browser, in Hebrew. Start it with `bugfix ui`."""

# 📘 הממשק הגרפי, בנוי עם Streamlit. Streamlit מריץ את הקובץ הזה מלמעלה למטה בכל לחיצה, ובונה ממנו
# 📘 דף אינטרנט שנפתח בדפדפן. כל ההיגיון נמצא ב-ui/logic.py וכל הטקסטים ב-ui/texts.py, וכאן רק
# 📘 מציירים את המסך.
# 📘 המשתמש לא משנה את האיגנט מכאן: המודל, התקציב וההגדרות נקבעים בקוד (core/config.py) או
# 📘 במשתני סביבה, כמו בפקודה bugfix.

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import streamlit as st
from streamlit.delta_generator import DeltaGenerator
from streamlit.runtime.uploaded_file_manager import UploadedFile

from cli.engines import EngineSetupError
from core.changes import apply_changes
from core.config import Settings
from core.types import RunResult, RunStatus
from ui import logic, texts

st.set_page_config(page_title="איגנט לתיקון באגים", page_icon="🛠️", layout="centered")

# Right-to-left layout and a Hebrew font; code stays left-to-right.
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;700&display=swap');
    html, body, .stApp, .stMarkdown, p, label, h1, h2, h3, li, button, input, table {
        font-family: 'Heebo', sans-serif;
    }
    .stApp { direction: rtl; }
    .stMarkdown, .stAlert, label, h1, h2, h3, p, table, th, td { text-align: right; }
    pre, code, .stCode { direction: ltr !important; text-align: left !important; }
    [data-testid="stSidebar"], [data-testid="stToolbar"], #MainMenu { display: none; }
    .step-title { font-size: 1.15rem; font-weight: 700; margin-top: 0.5rem; }
    .hint { color: #6b7280; font-size: 0.92rem; margin-top: -0.4rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

ICONS = {"info": "📋", "think": "💭", "tool": "🔧", "warn": "⚠️"}
settings = Settings.from_env()


def step(title: str, hint: str = "") -> None:
    st.markdown(f'<div class="step-title">{title}</div>', unsafe_allow_html=True)
    if hint:
        st.markdown(f'<div class="hint">{hint}</div>', unsafe_allow_html=True)


# ------------------------------------------------------------------ header

st.title("🛠️ איגנט לתיקון באגים")
st.markdown(
    "האיגנט מקבל קוד עם באג, **מוצא את הסיבה, מתקן אותה ומוכיח שהתיקון עובד** על ידי הרצת כל "
    "הטסטים. הוא עובד על עותק, כך שהקבצים שלך לא משתנים בלי אישור."
)

# The API key is needed to talk to the model. It is never saved or shown.
missing = logic.missing_key(settings)
if missing:
    with st.container(border=True):
        step(
            "לפני שמתחילים: מפתח API",
            "המפתח נשמר רק בזיכרון עד שסוגרים את הדף, ולא נשמר בשום מקום.",
        )
        typed = st.text_input(missing, type="password", label_visibility="collapsed")
        if typed:
            os.environ[missing] = typed.strip()
            st.rerun()

# ------------------------------------------------------------------ step 1: what to fix

UPLOAD, LOCAL = "להעלות תיקייה מהמחשב", "תיקייה לפי נתיב"


def uploaded_project(files: list[UploadedFile]) -> Path:
    """Save the uploaded folder once, and reuse it on every rerun of the page."""
    signature = sorted((f.name, f.size) for f in files)
    saved = st.session_state.get("upload")
    if saved and saved["signature"] == signature:
        return Path(saved["repo"])
    if saved:
        shutil.rmtree(saved["root"], ignore_errors=True)
    root = tempfile.mkdtemp(prefix="bugfix-upload-")
    repo = logic.save_upload(((f.name, f.getvalue()) for f in files), Path(root))
    st.session_state.upload = {"signature": signature, "root": root, "repo": str(repo)}
    return repo


with st.container(border=True):
    step("שלב 1: מה לתקן?")
    source = st.radio(
        "מה לתקן",
        ["באג לדוגמה (מוכן מראש)", UPLOAD, LOCAL],
        horizontal=True,
        label_visibility="collapsed",
    )
    sample = None
    repo = Path()
    if source.startswith("באג"):
        sample = st.selectbox("באג לדוגמה", logic.sample_cases(), format_func=logic.sample_label)
        repo = sample.repo
        st.caption("13 באגים שהוכנו מראש. הם אף פעם לא משתנים, כך שאפשר להריץ שוב ושוב.")
    elif source == UPLOAD:
        files = st.file_uploader(
            "בוחרים את תיקיית הפרויקט (עם הקוד והטסטים)", accept_multiple_files="directory"
        )
        st.caption("הקבצים מועתקים לתיקייה זמנית. בסוף אפשר להוריד את הפרויקט המתוקן כקובץ zip.")
        if files:
            try:
                repo = uploaded_project(files)
                st.caption(f"התקבלו {len(files)} קבצים מהתיקייה {repo.name}.")
            except ValueError as exc:
                st.warning(str(exc))
    else:
        typed_path = st.text_input("הנתיב לתיקיית הפרויקט", placeholder=r"C:\Users\me\my-project")
        repo = Path(typed_path.strip().strip('"')).expanduser()
        st.caption("כך אפשר להחיל את התיקון ישירות על הקבצים שלך, אחרי שרואים אותו.")

has_path = sample is not None or str(repo) not in ("", ".")
problem = logic.is_supported(repo) if has_path else None
if problem:
    st.warning(problem)
ready = has_path and not problem

# ------------------------------------------------------------------ step 2: see the bug

with st.container(border=True):
    step("שלב 2: לראות שיש באג", "מריצים את הטסטים של הפרויקט, בלי איגנט ובלי עלות.")
    if st.button("▶️ הרץ את הטסטים", disabled=not ready):
        with st.spinner("מריץ את הטסטים..."):
            runner, outcome = logic.check_project(repo, settings)
        if outcome.all_passed:
            st.success(f"כל {outcome.passed} הטסטים עוברים. אין כאן באג לתקן.")
        else:
            failing = outcome.failed + outcome.errors
            st.error(
                f"{failing} טסטים נכשלים ו-{outcome.passed} עוברים. יש באג, אפשר לעבור לשלב 3."
            )
        with st.expander("הפלט המלא של הטסטים"):
            st.code(outcome.output or "(אין פלט)")

# ------------------------------------------------------------------ step 3: fix it

with st.container(border=True):
    step(
        "שלב 3: לתקן",
        "אותו איגנט נבנה פעמיים: פעם עם לולאה שכתבנו בעצמנו (בלי SDK), ופעם עם Claude Agent SDK. "
        "אפשר להריץ אחד מהם, או את שניהם זה לצד זה ולהשוות.",
    )
    mode = st.radio(
        "איזה איגנט",
        ["שניהם, זה לצד זה", "בלי SDK", "עם SDK"],
        horizontal=True,
        label_visibility="collapsed",
    )
    engines = {"בלי SDK": ["raw"], "עם SDK": ["sdk"]}.get(mode, ["raw", "sdk"])
    start = st.button("🔍 מצא ותקן את הבאג", type="primary", disabled=not ready or bool(missing))

if start:
    st.session_state.results = {}
    columns = st.columns(len(engines))
    for engine, column in zip(engines, columns, strict=True):
        with column:
            live = st.status(f"{texts.AGENTS[engine]}: עובד...", expanded=True)

            def on_step(item: logic.Step, live: DeltaGenerator = live) -> None:
                live.write(f"{ICONS[item.kind]} {item.text}")

            try:
                result = logic.run_agent(engine, repo, settings, on_step)
            except EngineSetupError as exc:
                live.update(label=str(exc), state="error")
                continue
            done = result.status is RunStatus.RESOLVED
            live.update(
                label=f"{texts.AGENTS[engine]}: {'תוקן' if done else 'הסתיים'} "
                f"ב-{result.iterations} צעדים (לחיצה מציגה אותם)",
                state="complete" if done else "error",
                expanded=False,
            )
            st.session_state.results[engine] = result
    st.session_state.repo = str(repo)
    st.session_state.source = "sample" if sample is not None else source

# ------------------------------------------------------------------ results


def show_result(result: RunResult) -> None:
    good = result.status is RunStatus.RESOLVED
    message = texts.STATUS.get(result.status, result.status.value)
    if good:
        st.success(f"✅ {message} ({result.tests_passed} מתוך {result.tests_total}).")
    else:
        st.error(f"❌ {message}." + (f" {result.error}" if result.error else ""))
    a, b, c = st.columns(3)
    a.metric("צעדים", result.iterations)
    b.metric("זמן", f"{result.wall_time_s:.1f} שניות")
    c.metric("עלות", f"${result.cost_usd:.4f}")
    if result.patch:
        st.markdown("**השינוי בקוד** (שורה עם `-` נמחקה, שורה עם `+` נוספה):")
        st.code(result.patch, language="diff")
    if result.final_report:
        with st.expander("הדוח של האיגנט: מה היה הבאג ולמה התיקון נכון (באנגלית)"):
            st.markdown(result.final_report)


results: dict[str, RunResult] = st.session_state.get("results", {})
if results:
    st.divider()
    st.header("התוצאה")
    if len(results) > 1:
        st.markdown("**השוואה בין שני האיגנטים:**")
        named = {texts.AGENTS[k]: v for k, v in results.items()}
        st.table(logic.comparison_rows(named))
    for engine, result in results.items():
        if len(results) > 1:
            st.subheader(texts.AGENTS[engine])
        show_result(result)

    fixed = [r for r in results.values() if r.status is RunStatus.RESOLVED and r.changes]
    target = Path(st.session_state.repo)
    if fixed and st.session_state.get("source") == UPLOAD:
        st.info("התיקון נבדק. אפשר להוריד את הפרויקט עם התיקון, או רק את השינוי.")
        left, right = st.columns(2)
        left.download_button(
            "⬇️ להוריד את הפרויקט המתוקן (zip)",
            logic.fixed_project_zip(target, fixed[0].changes),
            file_name=f"{target.name}-fixed.zip",
            mime="application/zip",
        )
        right.download_button(
            "⬇️ להוריד רק את השינוי (patch)",
            fixed[0].patch,
            file_name=f"{target.name}.patch",
            mime="text/x-diff",
        )
    elif fixed and st.session_state.get("source") == LOCAL:
        st.info("התיקון נבדק על עותק. הקבצים שלך עדיין לא השתנו.")
        if st.button(f"✅ להחיל את התיקון על {target.name}"):
            try:
                changed = apply_changes(target, fixed[0].changes)
                st.success(f"התיקון הוחל על: {', '.join(changed)}")
            except RuntimeError as exc:
                st.error(str(exc))

st.caption(f"מודל: {settings.model} · תקרת עלות לריצה: ${settings.max_cost_usd:.2f}")
