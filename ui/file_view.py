"""The simple view: upload one code file, see each bug, and fix only what you approve."""

# 📘 המצב הפשוט: מעלים קובץ קוד אחד, בכל שפה. האיגנט מסמן את הקטע הבעייתי ומסביר אותו, ושואל אם
# 📘 לתקן. הקובץ משתנה רק אחרי לחיצה על "כן, תקן", ובסוף מורידים את הקובץ המתוקן.

from __future__ import annotations

import hashlib
import html
from dataclasses import dataclass, field

import streamlit as st

from core.config import Settings
from raw_agent import review
from raw_agent.client import APIError
from ui import logic
from ui.widgets import step

STYLE = """
<style>
.finding { border: 1px solid #e5e7eb; border-radius: 14px; padding: .2rem .2rem 0; }
.badge { display: inline-block; border-radius: 999px; padding: .05rem .6rem; font-size: .8rem;
         font-weight: 700; margin-left: .4rem; vertical-align: middle; }
.badge.critical { background: #fee2e2; color: #991b1b; }
.badge.major { background: #ffedd5; color: #9a3412; }
.badge.minor { background: #fef9c3; color: #854d0e; }
.ftitle { font-size: 1.12rem; font-weight: 700; color: #111827; vertical-align: middle; }
.where { color: #6b7280; font-size: .9rem; margin: .2rem 0 .4rem; }
.codeview { direction: ltr; text-align: left; background: #f8fafc; border: 1px solid #e2e8f0;
            border-radius: 10px; padding: .4rem 0; overflow-x: auto; margin: .3rem 0 .6rem;
            font-family: ui-monospace, Consolas, monospace; font-size: .86rem; }
.codeview div { white-space: pre; padding: 0 .8rem; line-height: 1.55; }
.codeview .n { display: inline-block; width: 3.2em; color: #94a3b8; user-select: none; }
.codeview .bad { background: #fee2e2; border-left: 3px solid #ef4444; }
.label { font-weight: 700; color: #1e1b4b; margin-top: .4rem; }
.done { border-radius: 10px; padding: .55rem .8rem; font-weight: 600; }
.done.fixed { background: #ecfdf5; color: #065f46; }
.done.skipped { background: #f3f4f6; color: #4b5563; }
</style>
"""


@dataclass(slots=True)
class Session:
    """One reviewed file: its text as uploaded, its text now, and what was decided per finding."""

    fingerprint: str
    name: str
    original: str
    code: str
    result: review.Review
    decisions: dict[int, str] = field(default_factory=dict)


def _proposed_change(finding: review.Finding) -> str:
    removed = [f"- {line}" for line in finding.original.splitlines()]
    added = [f"+ {line}" for line in finding.fixed.splitlines()]
    return "\n".join(removed + added)


def _code_view(code: str, finding: review.Finding) -> None:
    rows = logic.snippet(code, finding.start_line, finding.end_line)
    body = "".join(
        f'<div class="{"bad" if bad else ""}"><span class="n">{number}</span>'
        f"{html.escape(text) or ' '}</div>"
        for number, text, bad in rows
    )
    st.markdown(f'<div class="codeview">{body}</div>', unsafe_allow_html=True)


def _show_finding(index: int, finding: review.Finding, session: Session) -> None:
    with st.container(border=True):
        severity = html.escape(logic.SEVERITY.get(finding.severity, finding.severity))
        st.markdown(
            f'<span class="badge {html.escape(finding.severity)}">{severity}</span>'
            f'<span class="ftitle">{index + 1}. {html.escape(finding.title)}</span>',
            unsafe_allow_html=True,
        )
        if finding.applicable:
            lines = (
                f"שורה {finding.start_line}"
                if finding.start_line == finding.end_line
                else f"שורות {finding.start_line}-{finding.end_line}"
            )
            st.markdown(f'<div class="where">📍 {lines}</div>', unsafe_allow_html=True)
            _code_view(session.original, finding)

        st.markdown('<div class="label">מה הבעיה</div>', unsafe_allow_html=True)
        st.markdown(finding.explanation)
        st.markdown('<div class="label">התיקון המוצע</div>', unsafe_allow_html=True)
        st.code(_proposed_change(finding), language="diff")
        if finding.why_fix_works:
            st.markdown('<div class="label">למה זה מתקן את הבעיה</div>', unsafe_allow_html=True)
            st.markdown(finding.why_fix_works)

        decision = session.decisions.get(index)
        if decision == "fixed":
            st.markdown('<div class="done fixed">✅ תוקן</div>', unsafe_allow_html=True)
        elif decision == "skipped":
            st.markdown(
                '<div class="done skipped">דילגת על התיקון הזה</div>', unsafe_allow_html=True
            )
        elif not finding.applicable:
            st.warning(
                "לא הצלחתי לאתר את הקטע המדויק בקובץ, ולכן אי אפשר לתקן אותו אוטומטית. "
                "אפשר לתקן ידנית לפי ההסבר."
            )
        else:
            st.markdown("**לתקן את זה?**")
            yes, no = st.columns(2)
            if yes.button(
                "✅ כן, תקן", key=f"fix-{index}", type="primary", use_container_width=True
            ):
                try:
                    fixed = review.apply_finding(session.code, finding)
                except review.ReviewError as exc:
                    st.error(f"לא תוקן: {exc}")
                    return
                problem = review.syntax_problem(session.name, fixed)
                if problem:
                    st.error(f"לא תוקן: התיקון היה שובר את התחביר של הקובץ ({problem}).")
                    return
                session.code = fixed
                session.decisions[index] = "fixed"
                st.rerun()
            if no.button("✖️ לא, תודה", key=f"skip-{index}", use_container_width=True):
                session.decisions[index] = "skipped"
                st.rerun()


def render(settings: Settings, missing: str | None) -> None:
    st.markdown(STYLE, unsafe_allow_html=True)
    with st.container(border=True):
        step(1, "מעלים קובץ קוד", "קובץ אחד, בכל שפת תכנות. לא צריך טסטים.")
        upload = st.file_uploader("קובץ קוד", label_visibility="collapsed", key="code-file")
        if upload is None:
            st.session_state.pop("review_session", None)
            return
        data = upload.getvalue()
        code = logic.decode(data)
        if code is None:
            st.warning("זה לא נראה כמו קובץ קוד (קובץ טקסט). נסו קובץ אחר.")
            return
        language = review.language_of(upload.name)
        lines = code.count("\n") + 1
        st.caption(f"📄 {upload.name} · {lines} שורות" + (f" · {language}" if language else ""))
        with st.expander("הצגת הקוד"):
            st.code(code, language=language or None, line_numbers=True)

    fingerprint = hashlib.sha256(upload.name.encode() + data).hexdigest()
    session: Session | None = st.session_state.get("review_session")
    if session is not None and session.fingerprint != fingerprint:
        session = None
        st.session_state.pop("review_session", None)

    with st.container(border=True):
        step(
            2, "בודקים את הקוד", "האיגנט קורא את הקוד ומחפש באגים אמיתיים. הקובץ לא משתנה בשלב הזה."
        )
        if st.button(
            "🔍 בדוק את הקוד",
            type="primary",
            disabled=bool(missing),
            use_container_width=True,
            key="review-button",
        ):
            with st.spinner("קורא את הקוד ומחפש באגים..."):
                try:
                    result = logic.review_code(upload.name, code, settings)
                except (review.ReviewError, APIError) as exc:
                    st.error(f"הבדיקה לא הצליחה: {exc}")
                    return
            session = Session(fingerprint, upload.name, code, code, result)
            st.session_state.review_session = session

    if session is None:
        return
    result = session.result
    with st.container(border=True):
        step(3, "מה נמצא")
        if not result.findings:
            st.success("✅ לא נמצאו באגים בקוד הזה. אין מה לתקן.")
            if result.summary:
                st.markdown(result.summary)
        else:
            count = len(result.findings)
            st.error(
                "נמצאה בעיה אחת בקוד." if count == 1 else f"נמצאו {count} בעיות בקוד.",
                icon="🐞",
            )
            if result.summary:
                st.markdown(result.summary)
            st.caption('שום דבר לא משתנה עד שלוחצים "כן, תקן".')
            for index, finding in enumerate(result.findings):
                _show_finding(index, finding, session)
        st.caption(
            f"הבדיקה לקחה {result.wall_time_s:.1f} שניות ועלתה ${result.cost_usd:.4f} "
            f"({result.model})."
        )

    current, original = session.code, session.original
    if current != original:
        with st.container(border=True):
            step(4, "הקובץ המתוקן")
            st.code(logic.file_diff(upload.name, original, current), language="diff")
            st.download_button(
                "⬇️ להוריד את הקובץ המתוקן",
                current.encode("utf-8"),
                file_name=upload.name,
                mime="text/plain",
                type="primary",
                use_container_width=True,
            )
