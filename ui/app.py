"""The bugfix agent in the browser, in Hebrew. Start it with `bugfix ui`."""

# 📘 הממשק הגרפי, בנוי עם Streamlit. Streamlit מריץ את הקובץ הזה מלמעלה למטה בכל לחיצה, ובונה ממנו
# 📘 דף אינטרנט שנפתח בדפדפן. יש בו שני מצבים, כל אחד בקובץ משלו:
# 📘 file_view.py: מעלים קובץ קוד אחד, רואים את הבאגים, ומתקנים רק באישור (ברירת המחדל).
# 📘 project_view.py: פרויקט שלם עם טסטים, שבו התיקון מוכח בהרצת הטסטים.
# 📘 ההיגיון נמצא ב-ui/logic.py והטקסטים ב-ui/texts.py. המשתמש לא משנה את האיגנט מכאן: המודל
# 📘 והתקציב נקבעים בקוד (core/config.py) או במשתני סביבה.

from __future__ import annotations

import os

import streamlit as st

from core.config import Settings
from ui import file_view, logic, project_view
from ui.widgets import apply_style, step

st.set_page_config(page_title="איגנט לתיקון באגים", page_icon="🛠️", layout="centered")
apply_style()
settings = Settings.from_env()

st.title("🛠️ איגנט לתיקון באגים", anchor=False)
st.markdown(
    """
    <div class="hero">
      <p>מעלים קובץ קוד. האיגנט מראה בדיוק איזה קטע שבור ולמה, ומתקן רק אם מאשרים.
      אם אין באג, הוא אומר את זה.</p>
      <div class="how">
        <div><b>📄</b>מעלים קובץ קוד</div>
        <div><b>🔍</b>רואים מה שבור ולמה</div>
        <div><b>✅</b>מאשרים, והוא מתקן</div>
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

simple, advanced = st.tabs(["📄 בדיקת קובץ", "🧪 פרויקט שלם עם טסטים (מתקדם)"])
with simple:
    file_view.render(settings, missing)
with advanced:
    st.caption("למתקדמים: פרויקט שלם עם טסטים. האיגנט מתקן ומוכיח את התיקון על ידי הרצת כל הטסטים.")
    project_view.render(settings, missing)

st.caption(f"מודל: {settings.model} · תקרת עלות לריצה: ${settings.max_cost_usd:.2f}")
