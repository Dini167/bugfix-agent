<div dir="rtl">

# בלי SDK מול Claude Agent SDK: אותו איגנט, נבנה פעמיים

המסמך הזה משווה בין שני מימושים של אותו איגנט לתיקון באגים. לשניהם אותם כלים,
אותו פרומפט מערכת, אותו מודל, אותם תקציבים, אותו ארגז חול ואותו בודק (התיקייה
`core/`). ההבדל היחיד ביניהם הוא שכבת התזמור, כלומר הלולאה שמדברת עם המודל:

- `raw_agent/` מדבר עם ה-Messages API ישירות ב-HTTP, עם לולאה שכתבנו בעצמנו.
- `sdk_agent/` מעביר את הלולאה ל-Claude Agent SDK.

לכן כל הבדל בהתנהגות ביניהם נובע מהשכבה הזאת בלבד.

## במבט אחד

| | `raw_agent` (בלי SDK) | `sdk_agent` (עם Claude Agent SDK) |
|---|---|---|
| קוד שכתבנו, בלי הערות ותיעוד | **438 שורות, 4 קבצים** (ועוד 252 למודלים חינמיים ולגיבוי) | **149 שורות, קובץ אחד** |
| לולאת האיגנט | כתובה ידנית (`while True` לפי סיבת העצירה) | מסופקת (`query()` מריץ אותה) |
| HTTP, כותרות אימות, גרסת API | כתוב ידנית | מסופק |
| ניסיונות חוזרים וסיווג שגיאות | כתוב ידנית (המתנה הולכת וגדלה עם רכיב אקראי, `retry-after`, `x-should-retry`) | מסופק |
| פורמט ההודעות ותוצאות הכלים | כתוב ידנית | מסופק |
| שמירת פרומפט במטמון (caching) | מפורש (`cache_control` בכל בקשה) | ה-SDK מטפל בזה |
| הגבלת צעדים ועלות | ה-`Budget` שלנו, נבדק בכל סיבוב | `max_turns` / `max_budget_usd` של ה-SDK, ובנוסף ה-`Budget` שלנו |
| ספקי מודלים | Claude ועוד מודלים חינמיים (Ollama, Groq, OpenRouter, Gemini), מאחורי `ModelProvider` | Claude בלבד |
| גיבוי כשהמודל נכשל | כל שרשרת, למשל Claude ואחריו מודל חינמי, עם מעבר באמצע הריצה | רק הגיבוי של Claude עצמו במקרה של סירוב |
| מבנה התהליך | תהליך Python אחד, סינכרוני | Python ועוד ה-CLI של Claude Code שמגיע עם ה-SDK, כתהליך-בן, אסינכרוני |
| בדיקות בלי מפתח API | ברמת ה-HTTP: API מדומה בתוך התהליך (`httpx.MockTransport`) | ברמת זרם ההודעות: תחליף מתוסרט ל-`query()` |
| שקיפות | כל בקשה וכל תשובה בידיים שלנו לתיעוד | רק ההודעות שה-SDK בוחר לשלוח |

## שורות קוד לפי שכבה

נספר על ידי `benchmark/loc.py` (שורות לא ריקות שאינן הערות או תיעוד), ומתעדכן
בקובץ `benchmark/results/results.md` בכל הרצה של הבנצ'מרק.

| מימוש | שכבה | שורות |
|---|---|---|
| raw_agent | לקוח HTTP, שגיאות, ניסיונות חוזרים (`client.py`) | 181 |
| raw_agent | ממשק הספק והיסטוריה ניטרלית (`providers/base.py`) | 50 |
| raw_agent | פורמט ההודעות של Anthropic (`providers/anthropic.py`) | 121 |
| raw_agent | לולאת האיגנט (`loop.py`) | 86 |
| **raw_agent** | **סה"כ לאיגנט של Claude** | **438** |
| raw_agent | מודלים חינמיים וגיבוי (`providers/openai_compat.py`, `providers/fallback.py`) | 252 |
| sdk_agent | חיבור ל-SDK וטיפול בהודעות (`agent.py`) | 149 |
| משותף | כלים, ארגז חול, הגנות, ציון, שפות, מעטפת ההרצה (`core/`) | 904 |

בערך 60 מתוך 438 השורות קיימות כדי שריצה תוכל לעבור בין ספקים (היסטוריה ניטרלית של השיחה,
ושחזור שלה בפורמט של Anthropic). לפני שהוספנו את הגיבוי, האיגנט שבלי SDK היה 377 שורות.

שתי תובנות חשובות יותר מהמספרים הכוללים:

1. **ה-SDK חוסך את הצנרת, לא את ההנדסה.** 70% מהאיגנט שבלי SDK הם HTTP, ניסיונות
   חוזרים ופורמט הודעות: עבודה הכרחית, שקל לטעות בה בפרטים הקטנים, וזהה בכל
   איגנט. בדיוק את זה ה-SDK חוסך.
2. **רוב העבודה האמיתית משותפת.** 902 השורות המשותפות (הכלים, הגבלת הגישה
   לתיקייה, הגנה על קבצי טסט, תקציבים, ציון עצמאי, זיהוי שפה) נחוצות לשניהם.
   SDK לא כותב בשבילך את מודל הבטיחות.

## זה לצד זה

### הלולאה

בלי SDK, הלולאה שלנו. כל סיבת עצירה דורשת החלטה:

</div>

```python
# raw_agent/loop.py (condensed)
conversation = provider.new_conversation(SYSTEM_PROMPT, toolbox.specs())
conversation.add_user_text(TASK_PROMPT)
while True:
    budget.record_iteration()
    turn = conversation.send()                  # one HTTP request
    budget.record_usage(turn.usage)
    if turn.stop_reason is StopReason.TOOL_USE:
        results = [(c, toolbox.dispatch(c.name, c.arguments)) for c in turn.tool_calls]
        conversation.add_tool_results(results)  # all results in ONE user message
    elif turn.stop_reason is StopReason.END_TURN:
        return EngineOutcome(RunStatus.UNRESOLVED, final_report=turn.text)
    elif turn.stop_reason is StopReason.MAX_TOKENS:
        ...  # answer cut-off tool calls without running them, or ask to continue
    elif turn.stop_reason is StopReason.REFUSAL:
        ...
```

<div dir="rtl">

הסבר: בכל סיבוב שולחים בקשה אחת למודל ובודקים למה הוא עצר. אם ביקש להפעיל כלים
(`TOOL_USE`), מפעילים אותם ומחזירים את כל התוצאות בהודעה אחת. אם סיים
(`END_TURN`), יוצאים. אם נקטע באמצע (`MAX_TOKENS`) או סירב (`REFUSAL`), צריך
להחליט מה עושים. את כל ההחלטות האלה כתבנו בעצמנו.

עם SDK, הלולאה היא קריאה אחת, ואנחנו רק קוראים את מה שהיא מחזירה:

</div>

```python
# sdk_agent/agent.py (condensed)
async for message in query(prompt=TASK_PROMPT, options=options):
    if isinstance(message, AssistantMessage):
        on_assistant(message)      # count the turn, record usage
    elif isinstance(message, ResultMessage):
        result = message           # final text, total usage, why it stopped
```

<div dir="rtl">

הסבר: ה-SDK מריץ את כל הסיבובים בעצמו. אנחנו רק מקשיבים: על כל תשובה של המודל
סופרים צעד ורושמים שימוש בטוקנים, ובסוף מקבלים הודעת סיכום אחת.

### הגדרת הכלים

שניהם משתמשים באותה `Toolbox.specs()` (שם, תיאור, JSON Schema) ובאותה
`Toolbox.dispatch()`. רק המתאם שונה.

</div>

```python
# raw: the API's tool format, built by hand
{"name": s.name, "description": s.description, "input_schema": s.input_schema}

# raw: results returned by hand, matched by id, errors flagged
{"type": "tool_result", "tool_use_id": call.id, "content": output, "is_error": True}
```

```python
# SDK: the same spec, registered as an in-process MCP tool
tool(spec.name, spec.description, spec.input_schema)(handler)
create_sdk_mcp_server("bugfix", tools=tools)
```

<div dir="rtl">

הסבר: בלי SDK אנחנו בונים בעצמנו את המבנה שה-API מצפה לו, ומחזירים כל תוצאה עם
המזהה של הבקשה שלה. עם SDK רושמים את אותו כלי פעם אחת, וה-SDK דואג לכל השאר.

### איך דאגנו שההשוואה תהיה הוגנת

ל-SDK יש כלים מובנים חזקים (טרמינל, קריאה ועריכה של קבצים, חיפוש, גישה לאינטרנט).
אם היינו משאירים אותם פעילים, לאיגנט עם ה-SDK היו יכולות שאין לאיגנט השני,
וההשוואה הייתה מודדת כלים ולא תזמור. לכן:

</div>

```python
ClaudeAgentOptions(
    tools=[],                    # every built-in tool off
    allowed_tools=[...],         # only our five MCP tools
    permission_mode="dontAsk",   # anything else is denied
    strict_mcp_config=True,      # no MCP servers from the host machine
    setting_sources=[],          # no user/project settings or CLAUDE.md
    system_prompt=SYSTEM_PROMPT, # replaces the SDK's default prompt
)
```

<div dir="rtl">

| הגדרה | מה היא עושה |
|---|---|
| `tools=[]` | מכבה את כל הכלים המובנים |
| `allowed_tools=[...]` | מתיר רק את חמשת הכלים שלנו |
| `permission_mode="dontAsk"` | כל דבר אחר נדחה אוטומטית |
| `strict_mcp_config=True` | לא טוען שרתי MCP מהמחשב המארח |
| `setting_sources=[]` | לא טוען הגדרות משתמש או פרויקט |
| `system_prompt=SYSTEM_PROMPT` | מחליף את פרומפט ברירת המחדל של ה-SDK בפרומפט המשותף |

### שגיאות וניסיונות חוזרים

בלי SDK היינו צריכים לסווג כל כישלון בעצמנו (`raw_agent/client.py`):

- שגיאות 429, 5xx, 529 ובעיות חיבור מנוסות שוב, עם המתנה שגדלה פי שתיים בכל
  ניסיון ורכיב אקראי, כדי שלקוחות רבים לא יחזרו כולם באותו רגע.
- מכבדים את הכותרות `retry-after` ו-`x-should-retry` שהשרת שולח.
- שגיאות 400, 401, 403, 404 לא מנוסות שוב, כי ניסיון נוסף לא יעזור.
- שגיאת 429 שנגרמה בגלל שהחשבון הגיע לתקרת ההוצאה לא מנוסה שוב, כי המתנה לא
  תפתור אותה.

עם SDK, כל זה קורה בתוך ה-CLI, ואנחנו מטפלים רק ב-`ClaudeSDKError` וב-`subtype`
של הודעת הסיכום.

### מודלי גיבוי: מה מקבלים כשהלולאה בידיים שלך

זו היכולת היחידה שאין לגרסת ה-SDK, ואי אפשר להוסיף לה. הלולאה של האיגנט שבלי SDK מדברת עם
`ModelProvider`, ולא ישירות עם Claude, ולכן אפשר לשרשר כמה ספקים. אם Claude נכשל בצורה שניסיון
חוזר לא יפתור (נגמר הקרדיט, תקרת הוצאה, מפתח לא תקין, תקלה שנמשכת יותר מהניסיונות החוזרים),
הריצה ממשיכה אצל המודל הבא בשרשרת, מאותה נקודה:

</div>

```bash
bugfix fix --fallback groq:<model> --fallback ollama:<model>
```

```python
# raw_agent/providers/fallback.py (condensed)
try:
    turn = self._current.send()
except APIError:
    self._index += 1                       # next provider in the chain
    self._current = self.provider.new_conversation(
        self._system, self._tools, self._history)   # replay what was said so far
```

<div dir="rtl">

הסבר: אם הספק הנוכחי נכשל, עוברים לבא בתור, ובונים אצלו מחדש את כל מה שנאמר עד עכשיו.

שלושה דברים איפשרו את זה: פורמט הודעות שני (הפורמט של OpenAI, שרוב השירותים החינמיים
מדברים בו), תיעוד ניטרלי של השיחה שלא תלוי בספק, וחישוב עלות לכל קריאה בנפרד, כי ריצה אחת
יכולה עכשיו להיות מחויבת בשני מחירים שונים (טוקנים של מודל חינמי עולים 0). ב-SDK יש רק גיבוי
בצד השרת בין מודלים של Claude, במקרה שבקשה נדחתה.

### איפה ספריית `anthropic` נכנסת

יש שכבת ביניים בין השניים, שהפרויקט הזה בכוונה לא בונה: ספריית ה-Python הרשמית
`anthropic`. היא הייתה מחליפה את רוב `raw_agent/client.py` (HTTP, כותרות אימות,
סוגי שגיאות, ניסיונות חוזרים), אבל לא את הלולאה, לא את פורמט תוצאות הכלים ולא את
הטיפול בסיבות העצירה. כך שלוש האפשרויות יושבות על קו אחד:

| שכבה | מה כותבים בעצמך | מה מקבלים מוכן |
|---|---|---|
| HTTP ישיר (`raw_agent`) | תקשורת, ניסיונות חוזרים, פורמט הודעות, לולאה | כלום מעבר ל-`httpx` |
| ספריית `anthropic` | פורמט הודעות, לולאה | תקשורת, סוגי שגיאות, ניסיונות חוזרים |
| Claude Agent SDK (`sdk_agent`) | הכלים והטיפול בהודעות | כל מה שלמעלה, ובנוסף הלולאה, המטמון וניהול ההקשר |

האיגנט שבלי SDK יושב בתחתית בכוונה: הוא מראה כל חלק ששני האחרים מסתירים.

## תוצאות הבנצ'מרק

בבנצ'מרק 13 באגים ב-Python, JavaScript, Go ו-Rust, מקלים ועד קשים, כולל שני באגים
שמתפרסים על כמה קבצים ו"מלכודת עיגול" אחת שבה התיקון המתבקש שובר טסטים אחרים. כל
באג נבדק על ידי סוויטת הטסטים של הפרויקט: הוא חייב להיכשל כפי שהוא, והתיקון
המוכן שלו חייב לגרום לו לעבור.

**סטטוס: עוד לא הורץ.** ההרצה קוראת למודל וצריכה מפתח API:

</div>

```bash
export ANTHROPIC_API_KEY=sk-ant-...
bugfix benchmark --repeats 3
```

<div dir="rtl">

ההרצה כותבת את `benchmark/results/results.md` ובו, לכל איגנט: אחוז הבאגים שתוקנו,
ממוצע צעדים, טוקנים, עלות וזמן; תוצאות לכל באג; וכל ריצה שנגעה בטסטים או הגיעה
לתקרת התקציב. כל באג רץ כמה פעמים, כי ריצות של מודל אינן דטרמיניסטיות. כשהטבלה
תהיה קיימת, היא תיכנס לכאן.

## יתרונות וחסרונות

| נושא | בלי SDK | עם Claude Agent SDK |
|---|---|---|
| הזמן עד איגנט עובד | איטי יותר: בונים ובודקים את הצנרת | מהיר יותר: הגדרות וטיפול בהודעות |
| שליטה בכל בקשה | מלאה: כל שדה, כותרת וניסיון חוזר בידיים שלך | רק דרך האפשרויות שה-SDK חושף |
| דיבאג | רואים כל בקשה וכל תשובה | דרך ההודעות והלוגים של ה-SDK |
| החלפת ספק מודל | כבר בנוי: מודלים חינמיים ושרשרת גיבוי, 252 שורות | לא אפשרי, Claude בלבד |
| תלויות ומשקל | `httpx` בלבד | ה-SDK ועוד קובץ CLI מצורף שרץ כתהליך-בן |
| עדכון לשינויים ב-API | באחריותך (כותרות, שדות חדשים, סיבות עצירה חדשות) | באחריות ה-SDK |
| יכולות מובנות | רק מה שכתבת | כלי קבצים, טרמינל ואינטרנט, תת-איגנטים, סשנים, hooks (כאן בכוונה לא בשימוש) |

## מתי לבחור במה

**כדאי לבחור ב-SDK** כשהאיגנט רץ על Claude, המעטפת המובנית מתאימה למשימה, והזמן
עד תוצאה אמינה הוא מה שהכי חשוב. מקבלים לולאה בשלה, ניסיונות חוזרים, מטמון
וניהול הקשר, וכותבים רק את מה שייחודי לבעיה שלך: כאן, 149 שורות.

**כדאי לבנות על ה-API ישירות** כשצריך להחזיק את הלולאה בעצמך: איגנט שלא תלוי בספק
אחד (האיגנט שבלי SDK בפרויקט הזה רץ גם על מודלים חינמיים, ועובר אליהם כש-Claude נכשל),
זרימה לא שגרתית, מינימום תלויות, או שקיפות מלאה לכל בקשה לצורך ביקורת או דיבאג. המחיר
הוא בערך 290 שורות צנרת נוספות שצריך לכתוב, לבדוק ולתחזק.

בשני המקרים, החלקים שקובעים אם אפשר לסמוך על האיגנט (במה מותר לו לגעת, כמה מותר
לו להוציא, ומי מחליט אם הוא הצליח) הם באחריותך. בגלל זה הם יושבים ב-`core/`
ומשותפים לשניהם.

</div>
