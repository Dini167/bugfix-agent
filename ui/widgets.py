"""Pieces of the page that both views share: the style, step headers, failure cards."""

from __future__ import annotations

import html

import streamlit as st

from core.types import TestFailure


def apply_style() -> None:
    """Right-to-left layout, a Hebrew font and soft cards; code stays left-to-right."""
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
