"""The bugfix agent in the browser, in Hebrew. Start it with `bugfix ui`."""

# 📘 הממשק הגרפי, בנוי עם Streamlit. Streamlit מריץ את הקובץ הזה מלמעלה למטה בכל לחיצה, ובונה ממנו
# 📘 דף אינטרנט שנפתח בדפדפן. כל ההיגיון נמצא ב-ui/logic.py וכל הטקסטים ב-ui/texts.py, וכאן רק
# 📘 מציירים את המסך.
# 📘 המשתמש לא משנה את האיגנט מכאן: המודל, התקציב וההגדרות נקבעים בקוד (core/config.py) או
# 📘 במשתני סביבה, כמו בפקודה bugfix.

from __future__ import annotations

import html
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
from core.types import RunResult, RunStatus, TestFailure
from ui import logic, texts

st.set_page_config(page_title="איגנט לתיקון באגים", page_icon="🛠️", layout="centered")

# Right-to-left layout, a Hebrew font and soft cards; code stays left-to-right.
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;700;800&display=swap');
    html, body, .stApp, .stMarkdown, p, label, h1, h2, h3, li, button, input, table {
        font-family: 'Heebo', sans-serif;
    }
    .stApp { direction: rtl; }
    .stMarkdown, .stAlert, label, h1, h2, h3, p, table, th, td, li { text-align: right; }
    pre, code, .stCode { direction: ltr !important; text-align: left !important;
                         unicode-bidi: isolate; }
    [data-testid="stSidebar"], [data-testid="stToolbar"], #MainMenu { display: none; }
    .block-container { padding-top: 2.5rem; max-width: 820px; }

    .hero { background: linear-gradient(135deg, #eef2ff 0%, #f5f3ff 100%);
            border-radius: 18px; padding: 1.2rem 1.5rem; margin-bottom: 1.2rem; }
    h1 { font-weight: 800 !important; color: #1e1b4b; }
    .hero p { font-size: 1.05rem; color: #3f3d56; margin: 0 0 1rem; }
    .how { display: flex; gap: .6rem; flex-wrap: wrap; }
    .how div { flex: 1 1 150px; background: #fff; border-radius: 12px; padding: .6rem .8rem;
               font-size: .93rem; color: #312e81; box-shadow: 0 1px 3px rgba(30,27,75,.08); }
    .how b { display: block; font-size: 1.25rem; margin-bottom: .1rem; }

    .step { display: flex; align-items: center; gap: .6rem; margin: .2rem 0 .1rem; }
    .step .num { background: #4f46e5; color: #fff; width: 1.9rem; height: 1.9rem;
                 border-radius: 50%; display: inline-flex; align-items: center;
                 justify-content: center; font-weight: 700; flex: none; }
    .step .title { font-size: 1.2rem; font-weight: 700; color: #1e1b4b; }
    .hint { color: #6b7280; font-size: .93rem; margin: 0 2.5rem .4rem 0; }

    .fail { border: 1px solid #fecaca; background: #fff7f7; border-radius: 12px;
            padding: .7rem .9rem; margin: .5rem 0; }
    .fail .name { font-weight: 700; color: #991b1b; }
    .fail .where { color: #6b7280; font-size: .85rem; direction: ltr; display: inline-block; }
    .fail .msg { background: #fff; border-radius: 8px; padding: .5rem .7rem; margin-top: .4rem;
                 font-family: ui-monospace, Consolas, monospace; font-size: .85rem;
                 white-space: pre-wrap; color: #374151; direction: ltr; text-align: left; }

    .verdict { border-radius: 14px; padding: 1rem 1.2rem; margin: .6rem 0 1rem;
               font-size: 1.15rem; font-weight: 700; }
    .verdict.good { background: #ecfdf5; color: #065f46; border: 1px solid #a7f3d0; }
    .verdict.bad { background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }
    .verdict small { display: block; font-weight: 400; font-size: .95rem; margin-top: .2rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

ICONS = {"info": "📋", "think": "💭", "tool": "🔧", "warn": "⚠️"}
UPLOAD, LOCAL = "להעלות תיקייה מהמחשב", "תיקייה לפי נתיב"
settings = Settings.from_env()


def step(number: int, title: str, hint: str = "") -> None:
    st.markdown(
        f'<div class="step"><span class="num">{number}</span>'
        f'<span class="title">{title}</span></div>',
        unsafe_allow_html=True,
    )
    if hint:
        st.markdown(f'<div class="hint">{hint}</div>', unsafe_allow_html=True)


def show_failures(failures: list[TestFailure] | tuple[TestFailure, ...]) -> None:
    """Each failing test as a card: its name, where it failed, and what it reported."""
    for failure in failures:
        where = f'<span class="where">{html.escape(failure.location)}</span>'
        st.markdown(
            f'<div class="fail"><span class="name">❌ {html.escape(failure.name)}</span> '
            f"{where if failure.location else ''}"
            f'<div class="msg">{html.escape(failure.message)}</div></div>',
            unsafe_allow_html=True,
        )


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


# ------------------------------------------------------------------ header

st.title("🛠️ איגנט לתיקון באגים", anchor=False)
st.markdown(
    """
    <div class="hero">
      <p>נותנים לו קוד עם טסטים שנכשלים. הוא מוצא את הסיבה, מתקן אותה ומוכיח שהתיקון עובד.
      הוא עובד על עותק, כך שהקבצים שלך לא משתנים בלי אישור.</p>
      <div class="how">
        <div><b>📂</b>בוחרים פרויקט</div>
        <div><b>🧪</b>רואים בדיוק מה נכשל</div>
        <div><b>🛠️</b>מקבלים תיקון מוכח והסבר</div>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# The API key is needed to talk to the model. It is never saved or shown.
missing = logic.missing_key(settings)
if missing:
    with st.container(border=True):
        step(0, "לפני שמתחילים: מפתח API", "נשמר רק בזיכרון עד שסוגרים את הדף, ולא בשום מקום אחר.")
        typed = st.text_input(missing, type="password", label_visibility="collapsed")
        if typed:
            os.environ[missing] = typed.strip()
            st.rerun()

# ------------------------------------------------------------------ step 1: what to fix

with st.container(border=True):
    step(1, "מה לתקן?")
    source = st.radio(
        "מה לתקן",
        ["באג לדוגמה (מוכן מראש)", UPLOAD, LOCAL],
        horizontal=True,
        label_visibility="collapsed",
    )
    sample = None
    repo = Path()
    if source.startswith("באג"):
        cases = logic.sample_cases()
        sample = st.selectbox("באג לדוגמה", cases, format_func=logic.sample_label)
        repo = sample.repo
        st.caption(
            f"{len(cases)} באגים אמיתיים ב-Python, JavaScript, Go, Rust, C++\u200e ו-C#\u200e. "
            "הם אף פעם לא משתנים, כך שאפשר להריץ שוב ושוב."
        )
    elif source == UPLOAD:
        files = st.file_uploader(
            "בוחרים את תיקיית הפרויקט (עם הקוד והטסטים)", accept_multiple_files="directory"
        )
        if files:
            try:
                repo = uploaded_project(files)
                st.caption(f"📂 התקבלו {len(files)} קבצים מהתיקייה **{repo.name}**.")
            except ValueError as exc:
                st.warning(str(exc))
        else:
            st.caption("הקבצים מועתקים לתיקייה זמנית. בסוף מורידים את הפרויקט המתוקן כקובץ zip.")
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
    step(2, "מה נכשל?", "מריצים את הטסטים של הפרויקט ורואים כל טסט שנכשל. בלי איגנט ובלי עלות.")
    if st.button("▶️ הרץ את הטסטים", disabled=not ready):
        with st.spinner("מריץ את הטסטים..."):
            runner, outcome = logic.check_project(repo, settings)
        if outcome.all_passed:
            st.success(f"כל {outcome.passed} הטסטים עוברים. אין כאן באג לתקן.")
        else:
            failing = outcome.failed + outcome.errors
            st.error(f"{failing} טסטים נכשלים ו-{outcome.passed} עוברים ({runner}).")
            show_failures(outcome.failures)
        with st.expander("הפלט המלא של הטסטים"):
            st.code(outcome.output or "(אין פלט)", language=None)

# ------------------------------------------------------------------ step 3: fix it

with st.container(border=True):
    step(
        3,
        "לתקן",
        "אותו איגנט בנוי פעמיים: פעם עם לולאה שכתבנו בעצמנו (בלי SDK), ופעם עם Claude Agent SDK. "
        "אפשר להריץ אחד מהם, או את שניהם ולהשוות.",
    )
    mode = st.radio(
        "איזה איגנט",
        ["שניהם, זה לצד זה", "בלי SDK", "עם SDK"],
        horizontal=True,
        label_visibility="collapsed",
    )
    engines = {"בלי SDK": ["raw"], "עם SDK": ["sdk"]}.get(mode, ["raw", "sdk"])
    start = st.button(
        "🔍 מצא ותקן את הבאג",
        type="primary",
        disabled=not ready or bool(missing),
        use_container_width=True,
    )

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
    detail = (
        f"לפני: {result.tests_failing_before} טסטים נכשלו. "
        f"אחרי: {result.tests_passed} מתוך {result.tests_total} עוברים."
    )
    if result.error:
        detail += f" {result.error}"
    st.markdown(
        f'<div class="verdict {"good" if good else "bad"}">{"✅" if good else "❌"} '
        f"{html.escape(message)}<small>{html.escape(detail)}</small></div>",
        unsafe_allow_html=True,
    )
    a, b, c = st.columns(3)
    a.metric("צעדים", result.iterations)
    b.metric("זמן", f"{result.wall_time_s:.1f} שניות")
    c.metric("עלות", f"${result.cost_usd:.4f}")

    sections = logic.report_sections(result.final_report)
    if sections:
        st.markdown("#### הדוח של האיגנט")
        for title, body in sections:
            with st.container(border=True):
                st.markdown(f"**{title}**")
                st.markdown(body)
    elif result.final_report:
        with st.expander("הדוח של האיגנט"):
            st.markdown(result.final_report)

    if result.failing_tests:
        with st.expander(f"הטסטים שנכשלו לפני התיקון ({len(result.failing_tests)})"):
            show_failures(result.failing_tests)
    if result.patch:
        st.markdown("#### השינוי בקוד")
        st.caption("שורה עם - נמחקה, שורה עם + נוספה.")
        st.code(result.patch, language="diff")


results: dict[str, RunResult] = st.session_state.get("results", {})
if results:
    st.divider()
    st.header("התוצאה", anchor=False)
    if len(results) > 1:
        st.markdown("**השוואה בין שני האיגנטים:**")
        named = {texts.AGENTS[k]: v for k, v in results.items()}
        st.table(logic.comparison_rows(named))
        for tab, result in zip(st.tabs(list(named)), results.values(), strict=True):
            with tab:
                show_result(result)
    else:
        show_result(next(iter(results.values())))

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
            use_container_width=True,
        )
        right.download_button(
            "⬇️ להוריד רק את השינוי (patch)",
            fixed[0].patch,
            file_name=f"{target.name}.patch",
            mime="text/x-diff",
            use_container_width=True,
        )
    elif fixed and st.session_state.get("source") == LOCAL:
        st.info("התיקון נבדק על עותק. הקבצים שלך עדיין לא השתנו.")
        if st.button(f"✅ להחיל את התיקון על {target.name}", use_container_width=True):
            try:
                changed = apply_changes(target, fixed[0].changes)
                st.success(f"התיקון הוחל על: {', '.join(changed)}")
            except RuntimeError as exc:
                st.error(str(exc))

st.caption(f"מודל: {settings.model} · תקרת עלות לריצה: ${settings.max_cost_usd:.2f}")
