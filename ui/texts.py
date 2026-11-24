"""Every word the browser interface shows, in Hebrew, in one place."""

# 📘 כל הטקסטים של הממשק במקום אחד. כדי לשנות ניסוח או לתרגם, משנים רק כאן.

from __future__ import annotations

from typing import Any

from core.types import RunStatus

# Hebrew names for the sample bugs, by case number.
CASE_NAMES = {
    "01": "ממוצע ציונים מדלג על הציון הראשון",
    "02": "משלוח חינם ניתן להזמנות הלא נכונות",
    "03": "תגיות עוברות בטעות בין פתקים שונים",
    "04": "ממוצע של רשימה ריקה קורס עם שגיאה לא נכונה",
    "05": "ההגדרה 'false' נקראת כאילו היא True",
    "06": 'מחירים עם מע"מ נמוכים מדי',
    "07": "מוני מילים חולקים את אותה ספירה",
    "08": "דוח שבועי משייך ימים לשבוע הלא נכון (באג בשני קבצים)",
    "09": "הנחת כמות לא ניתנת בדיוק בגבול המדרגה (באג בשני קבצים)",
    "10": "עיגול בחשבונית מפספס באגורה (מלכודת: התיקון המתבקש שובר טסטים אחרים)",
    "11": "טבלת שיאים מציגה קודם את הציונים הנמוכים",
    "12": "בעמוד האחרון של תוצאות תמיד חסר פריט",
    "13": "תצוגה מקדימה קורסת על טקסט בעברית ועל אימוג'י",
}
LANGUAGES = {"11": "JavaScript", "12": "Go", "13": "Rust"}
DIFFICULTY = {"easy": "קל", "medium": "בינוני", "hard": "קשה"}

STATUS = {
    RunStatus.RESOLVED: "תוקן! כל הטסטים עוברים",
    RunStatus.UNRESOLVED: "לא תוקן: הטסטים עדיין נכשלים",
    RunStatus.BUDGET_EXCEEDED: "נעצר כי הגיע לתקרת העלות או מספר הצעדים",
    RunStatus.TESTS_TAMPERED: "נפסל: האיגנט שינה קבצי טסט, וזה אסור",
    RunStatus.ALREADY_PASSING: "אין מה לתקן: כל הטסטים כבר עוברים",
    RunStatus.ERROR: "נעצר בגלל שגיאה",
}

AGENTS = {
    "raw": "בלי SDK",
    "sdk": "עם SDK",
}


def tool_step(event: dict[str, Any]) -> str:
    """A tool call, as a short Hebrew sentence."""
    args: dict[str, Any] = event.get("arguments") or {}
    tool = event.get("tool")
    failed = " (נכשל)" if event.get("is_error") else ""
    if tool == "run_tests":
        return f"מריץ את הטסטים{failed}"
    if tool == "list_files":
        return f"מסתכל אילו קבצים יש בפרויקט{failed}"
    if tool == "read_file":
        return f"קורא את הקובץ {args.get('path')}{failed}"
    if tool == "search_code":
        return f"מחפש בקוד: {args.get('pattern')}{failed}"
    if tool == "edit_file":
        if event.get("is_error"):
            return f"ניסה לערוך את {args.get('path')} ולא הצליח"
        return f"מתקן את הקובץ {args.get('path')}"
    return f"{tool}{failed}"
