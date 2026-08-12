# CLAUDE.md — Swing Desk: Project Knowledge Base

> **מסמך חי.** מעדכנים אותו בסיום כל משימה משמעותית — רק כשמתבקש במפורש ("עדכן את CLAUDE.md").
> בכל שיחה חדשה: צרף קובץ זה + הקבצים הרלוונטיים. אין צורך בהיסטוריית צ'אט.

---

## 1. חזון ומטרה

**Swing Desk** הוא דשבורד מסחר פיננסי בזמן אמת, המיועד לטריידרים קצרי-טווח (swing trading). המטרה: לספק בממשק אחד את כל המידע הקריטי לקבלת החלטות מסחר — סריקת מניות טכנית ברמה גבוהה, חדשות מאקרו, גרפי סקטורים ומדדים, בדיקה היסטורית (Backtesting), וניתוח מעמיק למניה בודדת — בלי לנווט בין עשרות מקורות.

**מודל עסקי עתידי:** אתר ציבורי עם פוטנציאל מוניטיזציה (מנויים, פרמיום).

**כתובת האתר הנוכחית (Frontend):** https://swing-desk-tau.vercel.app
**כתובת ה-API הנוכחית (Backend):** https://swing-dashboard-3btx.onrender.com

---

## 2. ארכיטקטורה כללית — **שונתה לגמרי באוגוסט 2026** (מעבר מ-Railway)

Railway (ניסיון חינם 30 יום) הסתיים. הפרויקט עבר לערימת שירותים **חינמיים לצמיתות** (לא תקופת ניסיון):

```
[GitHub Repository: sabatonissim/swing-dashboard — כעת PUBLIC]
         |
         ├── Vercel (Frontend) — ללא שינוי
         │     └── index.html
         │
         ├── Render (Backend API — Free Web Service)
         │     └── api_server.py — FastAPI, "נרדם" אחרי 15 דק' חוסר פעילות
         │
         ├── Neon (Postgres — Free Tier)
         │     └── מסד הנתונים — "auto-suspend" אחרי ~5 דק' חוסר פעילות ב-DB
         │
         └── GitHub Actions (מחליף את ה-Cron Jobs של Railway — חינמי ללא הגבלה, כי הריפו ציבורי)
               ├── stock-scanner.yml   — מריץ pipeline_a_scanner.py
               ├── news-aggregator.yml — מריץ pipeline_b_news_aggregator.py
               └── keep-render-awake.yml — פינג ל-/api/health כל 13 דק' כדי ש-Render לא ירדם
```

**עיקרון:** שינוי קוד → push ל-GitHub → Render ו-Vercel מתעדכנים אוטומטית (build+deploy). קבצי ה-workflows רצים לפי לוח הזמנים שמוגדר בהם, בלי תלות בשרת חי.

### ⚠️ למה הריפו ציבורי (החלטה מכוונת, לא שגיאה)
GitHub Actions מגביל ריפו **פרטי** ל-2,000 דקות ריצה/חודש בחינם. בהינתן שהחדשות והסריקה רצות בתדירות גבוהה, זה עלול לחרוג מהמכסה. ריפו **ציבורי** מקבל דקות **ללא הגבלה**. הוחלט להפוך את הריפו לציבורי כדי לפתור את זה לצמיתות. **הסודות (`DATABASE_URL` וכו') לא נחשפים** — הם שמורים ב-GitHub Secrets המוצפנים, בנפרד לגמרי מהקוד, גם בריפו ציבורי.

---

## 3. קבצי הפרויקט

| קובץ | איפה רץ | תפקיד |
|---|---|---|
| `index.html` | Vercel | הדשבורד — ממשק המשתמש המלא, קובץ יחיד |
| `api_server.py` | Render | FastAPI — מחזיר נתונים מ-Postgres/yfinance לפרונט |
| `pipeline_a_scanner.py` | GitHub Actions (`stock-scanner.yml`) | סורק טכני — מזהה תבניות, כותב ל-Postgres |
| `pipeline_b_news_aggregator.py` | GitHub Actions (`news-aggregator.yml`) | שולף חדשות RSS, מתרגם, כותב ל-Postgres |
| `requirements.txt` | Render + GitHub Actions | תלויות Python |
| `.github/workflows/stock-scanner.yml` | GitHub Actions | תזמון הסריקה (16:40 + 22:50 שעון ישראל, בקירוב — ראה סעיף 14) |
| `.github/workflows/news-aggregator.yml` | GitHub Actions | תזמון החדשות (כל שעה, ב-17 דק' אחרי השעה) |
| `.github/workflows/keep-render-awake.yml` | GitHub Actions | פינג תקופתי ל-Render כדי למנוע "הירדמות" |

---

## 4. טכנולוגיות

### Frontend
- **Vanilla HTML/CSS/JS** — קובץ אחד, בלי framework, בלי build process
- **TradingView Widget** (חינמי) — גרפים בתוך המגירה, גרפי מדדים, ועמוד "ניתוח מניה"
- **Chart.js 4.5.0** (CDN עם גיבוי jsdelivr) — גרפי פונדמנטלס, גרף הסקטורים, עקומת equity בבדיקה ההיסטורית
- **RTL עברית כברירת מחדל**, עם toggle לאנגלית
- **localStorage** — משמש רק למחשבון הסיכונים ולהעדפת שפה. לא לשום נתון עסקי.

### Backend
- **FastAPI + Uvicorn** — Python API
- **psycopg2-binary** — חיבור ל-Postgres
- **yfinance** — נתוני שוק בזמן אמת (חינמי, ללא API key) — ⚠️ ראה סעיף 12, `.info` לא אמין מ-IP של ענן
- **feedparser** — שליפת RSS
- **urllib (built-in)** — תרגום ל-Google Translate ללא API key
- **pandas.read_csv** — מקור אמין לרשימת S&P 500 + סקטור/תת-סקטור (במקום `.info`)

### Infrastructure (נוכחי — אוגוסט 2026)
- **Render** — Free Web Service ל-API (`swing-dashboard-3btx.onrender.com`)
- **Neon** — Postgres חינמי לצמיתות (לא ניסיון!), auto-suspend אחרי ~5 דק' חוסר פעילות
- **Vercel** — אחסון הפרונט (חינמי, ללא שינוי)
- **GitHub** — source control, ציבורי, + GitHub Actions למחליף ל-cron jobs

### מה לא בשימוש (ולמה)
- ❌ **Railway** — תקופת הניסיון (30 יום, $5 קרדיט) הסתיימה. הוחלף כולו ב-Neon+Render+GitHub Actions (ראה סעיף 2).
- ❌ **OpenAI** — לא בשימוש בשום pipeline. תרגום/ניתוח מבוססים על מיפויים סטטיים וGoogle Translate חינמי.
- ❌ **lxml** — הוחלף במקור CSV פשוט (`datasets/s-and-p-500-companies`)
- ❌ **VIX (בכל צורה)** — נוסה TradingView Widget (כל הפורמטים נכשלו), נוסה גרף עצמאי מ-yfinance (Chart.js ואז lightweight-charts) — המשתמש לא אהב את המראה מול שאר הגרפים. **הוסר לגמרי**, חזרה ל-5 טאבים המקוריים (SPY/QQQ/GLD/IWM/Bitcoin). מדד הפחד/תאווה (Fear & Greed) משמש כתחליף.
- ❌ **cron-job.org / שירותי פינג חיצוניים** — נשקל לפתרון בעיית תזמון GitHub Actions הלא-מדויק (ראה סעיף 14), אך המשתמש בחר **לא** להוסיף עוד שירות חיצוני ולקבל דיוק-זמן נמוך יותר במקום זאת.

---

## 5. מסד הנתונים (Postgres, מתארח ב-Neon)

### ⚠️ נקודה קריטית תפעולית: Neon Branches
כשהנתונים הועברו מ-Railway ל-Neon (Import Data Assistant), הם נחתו ב-**branch נפרד** בשם `import-2026-08...`, **לא** ב-branch `production`/`main` המקורי שנוצר עם הפרויקט. `DATABASE_URL` שבשימוש בפועל (ב-Render וב-GitHub Secrets) **חייב** להצביע ל-connection string של ה-branch `import-2026-08...` — לא של ה-branch הראשי. אם אי פעם יוצרים branch/פרויקט Neon חדש, לשים לב לאיזה branch באמת מצביעה הכתובת.

### ⚠️ Neon Auto-Suspend — שונה מהותית מ-Railway
Neon מכבה את מחשוב ה-DB אוטומטית אחרי **כ-5 דקות בלי שאילתות** (כך הוא נשאר חינמי). זה גרם לבאג אמיתי: `pipeline_a_scanner.py` פתח חיבור ל-DB **לפני** הסריקה (שלוקחת כ-6 דקות בלי לגעת ב-DB בכלל), וכשניסה לכתוב בסוף — Neon כבר "נרדם" וניתק את החיבור (`psycopg2.OperationalError: SSL connection has been closed unexpectedly`). **תוקן:** החיבור נפתח רק **ממש לפני** הכתיבה בפועל (אחרי שהסריקה כבר הסתיימה), עם **retry אוטומטי** אם החיבור בכל זאת מתנתק באמצע. **חשוב לזכור לעתיד:** כל קוד חדש שסורק/מעבד נתונים הרבה זמן לפני כתיבה ל-DB חייב לפתוח את החיבור **בסמוך לכתיבה**, לא בהתחלה — אחרת אותו באג יחזור.

### `scanned_stocks` — תוצאות הסריקה
עמודות עיקריות: `ticker`, `trigger_text_he/en`, `pattern_type`, `swing_score` (0-100), `entry_price`, `support_level`, `resistance_targets` (JSON), `sma50/150/200`, `trend_stage`, `atr_value/pct`, `rs_rating` (1-99), `days_to_earnings`, `forward_return_10d/20d` (ממולאים בדיעבד — ראה תיקון תזמון למטה), `ai_summary_he/en`, `breakout_volume_pct`, `change_pct`, `exchange`, `market_cap`, `avg_volume_20d`, `timestamp`

**תיקון תזמון ה-backfill:** `forward_return_10d` חיכה בעבר 30 יום סתם לפני מילוי (במקום 14 הנדרשים בפועל ל-10 ימי מסחר). תוקן: כל horizon (10d/20d) נבדק בנפרד לפי הזמן שבאמת נדרש לו (14/28 יום).

### `macro_news` — חדשות מאקרו
`category_tag`, `summary_he/en`, `hook_he`, `impact_level` (High/Critical), `source_url` (UNIQUE), `timestamp`

### `watchlist` — רשימת מעקב אישית
`ticker` (PRIMARY KEY), `added_at`

### `universe_movers` — מקור למפת החום + fallback ל"הכי זזות"
`ticker` (PK), `change_pct`, `close_price`, `in_sp500`, `in_nasdaq100`, `timestamp`

### משתני סביבה נדרשים
- `DATABASE_URL` = connection string של Neon (**ה-branch `import-2026-08...`**, לא production/main — ראה למעלה). מוגדר גם ב-Render (Environment) וגם כ-GitHub Secret (Settings → Secrets and variables → Actions).
- `ALLOWED_ORIGINS` = `https://swing-desk-tau.vercel.app` (מוגדר ב-Render Environment)

---

## 6. API Endpoints

Base URL הנוכחי: `https://swing-dashboard-3btx.onrender.com`

| Endpoint | Method | תיאור |
|---|---|---|
| `/api/health` | GET | בדיקת חיות + חיבור DB — גם מה ש-`keep-render-awake.yml` פוקד עליו |
| `/api/stocks` | GET | מניות מסומנות מה-DB |
| `/api/macro-news` | GET | חדשות מאקרו — עד 25 אחרונות, רוטציה חכמה |
| `/api/macro-news/search` | GET | **חדש** — חיפוש מלא בחדשות על פני עד 30 יום אחורה ב-DB (לא רק ב-25 שמוצגות), פרמטרים `q`/`days`/`limit` |
| `/api/lookup/{ticker}` | GET | נתוני שוק חיים — **נבנה מחדש** להסתמך בעיקר על `.history()` (אמין), `.info` כתוספת אופציונלית בלבד (ראה סעיף 12) |
| `/api/sector-comparison/{ticker}` | GET | **חדש** — תשואת המניה מול ה-ETF של הסקטור שלה (שנה אחורה) + עד 4 "מתחרים" מאותו תת-סקטור GICS מדויק |
| `/api/signal-history/{ticker}` | GET | **חדש** — כל האיתותים ההיסטוריים של טיקר ספציפי מהסריקה |
| `/api/backtest` | GET | **חדש** — עקומת equity, Max Drawdown, פילוח RS Rating, רשימת איתותים. פרמטרים `pattern`/`horizon` (10/20) |
| `/api/backtest/patterns` | GET | **חדש** — רשימת תבניות עם איתותים בשלים, לתפריט הסינון |
| `/api/market-movers` | GET | הכי זזות היום, fallback ל-`universe_movers` |
| `/api/sector-performance` | GET | ביצועי 11 סקטורי SPDR, נורמליזציה, טווחי 1M/3M/YTD/1Y |
| `/api/pattern-stats` | GET | אחוז הצלחה + תשואה ממוצעת לתבנית (5+ מקרים נדרשים) — מוצג גם כתג צבעוני ברשימת המניות הראשית |
| `/api/watchlist` | GET/POST/DELETE | רשימת מעקב אישית |
| `/api/heatmap` | GET | מפת חום, פרמטר `index=sp500\|nasdaq100` |
| `/api/fear-greed` | GET | מדד Fear & Greed האמיתי של CNN (endpoint לא-רשמי, cache שעה) |
| `/api/fundamentals/{ticker}` | GET | דוחות רבעוניים מ-SEC EDGAR — **נוסף שדה `market_cap`** (חושב במקום להישאר ריק — ראה סעיף 12); **תוקן** חישוב חוב-נטו לחברות בלי חוב מדווח (כמו SHOP) |
| `/api/stock-news/{ticker}` | GET | חדשות ספציפיות לטיקר |
| `/api/track`, `/api/analytics/*` | — | אנליטיקס, ללא שינוי |

---

## 7. Pipeline A — סורק מניות טכני

### לוח זמנים — **שונה מ-Railway Cron ל-GitHub Actions**
`stock-scanner.yml`: `40 13 * * *` ו-`50 19 * * *` (UTC) = 16:40 ו-22:50 שעון ישראל (בקיץ, UTC+3). ⚠️ **cron לא מתאים אוטומטית לשעון חורף** — כשהשעון בישראל יחליף (סביב סוף אוקטובר), הזמנים האלה יזוזו בשעה. יש לעדכן ידנית את הקבצים אם רוצים לשמר את השעה המקומית המדויקת.

⚠️ **חשוב:** GitHub Actions **לא מבטיח דיוק בזמן `schedule`** — במיוחד בעומס. בפועל נצפו סטיות של שעות, לא רק דקות. המשתמש בחר לקבל את זה (ראה סעיף 14) במקום להוסיף שירות טריגר חיצוני.

### Universe
S&P 500 + Nasdaq 100 המלאים (מ-CSV ציבוריים) + רשימת תוספות קבועה. פילטרים: מחיר > $10, נפח 20 יום > 1.5M, שווי שוק > $1.5B, NYSE/NASDAQ בלבד.

### תבניות (11, ללא שינוי מהסבב הקודם)
Cup & Handle, Double Bottom, 52W Breakout, Golden Cross, Bull Flag, Ascending Triangle, Ascending Trendline Support, MACD Crossover, Momentum Surge, RSI Bounce, Descending Trendline Break.

### תיקון קריטי: ניהול חיבור DB (Neon auto-suspend)
ראה סעיף 5 — `main()` פותח חיבור רק לפני הכתיבה, לא לפני הסריקה, עם retry אוטומטי בכתיבה.

### מבנה מגמה, RS Rating, מודעות דוחות, סטטיסטיקת הצלחה
ללא שינוי מהסבב הקודם — ראה גרסה קודמת של מסמך זה להרחבה, או הקוד עצמו (מתועד בהערות).

---

## 8. Pipeline B — אגרגטור חדשות

### לוח זמנים — **שונה, פעמיים**
1. במעבר מ-Railway ל-GitHub Actions: הוגדר במקור `*/30 * * * *` (כל 30 דקות)
2. **התברר לא אמין** — GitHub דוחה/מדלג הרצות בתדירות גבוהה (ראה סעיף 14). **שונה בפועל ל-`17 * * * *`** — כל שעה, ב-17 דקות אחרי השעה (נמנע מ"עומס" שעה עגולה).

### מקורות RSS, מניעת כפילויות, "Critical" נשמר 72 שעות, תרגום חינמי, hook_he
ללא שינוי מהסבב הקודם.

---

## 9. Frontend — index.html

### ניווט — **4 טאבים כעת** (היו 2)
1. **בית** — הדשבורד הרגיל
2. **פעימת השוק** — מפת חום + Fear & Greed
3. **בדיקה היסטורית** (Backtest) — **חדש**
4. **ניתוח מניה** (Deep Dive) — **חדש**

### עמוד "בדיקה היסטורית" — **חדש**
פילטר תבנית + toggle 10/20 יום. 4 כרטיסי סיכום (מספר איתותים, % הצלחה, תשואה ממוצעת, Max Drawdown), גרף עקומת equity (Chart.js), פילוח לפי RS Rating, טבלת כל האיתותים ההיסטוריים. ריק עד שמצטברת מספיק היסטוריה בשלה (זה תקין, לא באג).

### עמוד "ניתוח מניה" (Deep Dive) — **חדש**
חיפוש טיקר חופשי (לא רק מה שהסורק מזהה). גרף TradingView מלא. כרטיסי סטטיסטיקה (52w high/low, market cap, P/E, ווליום). **"על החברה"**: תיאור + סקטור + תת-סקטור GICS מדויק (ממקור CSV אמין, לא `.info`). **"מניה מול סקטור"**: תשואת שנה מול ה-ETF המתאים. **"מתחרים בתת-הסקטור"**: עד 4 חברות מאותו תת-ענף מדויק, עם תשואה שנתית להשוואה ישירה. **נתונים פונדמנטליים**: אותם גרפי SEC EDGAR (הכנסות/FCF/מרווח/חוב-נטו/P/E) — **תוקן באג**: הכותרת הייתה מוצגת אך אף קוד לא קרא בפועל לטעינת הגרפים תחתיה (חובר ל-`loadFundamentals`). היסטוריית איתותים לטיקר הספציפי. חדשות ספציפיות. קיצור דרך מהמגירה הרגילה ("🔎 ניתוח מלא למניה").

### תיקון חיפוש חדשות
תיבת חיפוש מעל פילטר הסקטורים בעמודת החדשות. שולחת בקשה ל-`/api/macro-news/search` (חיפוש DB אמיתי, לא רק ב-25 המוצגות), עם debounce של 350ms.

### תג Win-Rate ברשימת המניות
תג עגול צבעוני (ירוק/כתום/אדום) ליד כל מניה ברשימה הראשית, מציג את אחוז ההצלחה ההיסטורי של התבנית שזוהתה (מ-`/api/pattern-stats`), בלי צורך לפתוח את המניה. מוצג רק כשיש 5+ איתותים היסטוריים לתבנית.

### חלון "השווה מול..." — עוצב מחדש
כפתור ✕ סגירה בפינה (כמו שאר החלונות), אייקון חיפוש בתיבה, תג ציון צבעוני בכל שורת תוצאה, קווי הפרדה בין שורות.

### תיקוני UI קטנים
חץ קיפול/פתיחה של קבוצות "סריקה אחרונה"/"ימים קודמים" הוגדל (22px, מודגש) והפך לבולט יותר בהעברת עכבר.

### API Connection
```javascript
const API_BASE = "https://swing-dashboard-3btx.onrender.com";
```

---

## 10. החלטות ארכיטקטורה מרכזיות (מעודכן)

| החלטה | סיבה |
|---|---|
| Neon + Render + GitHub Actions (לא Railway) | Railway היה תקופת ניסיון בתשלום לאחריה (30 יום), לא תוכנית חינמית קבועה |
| ריפו GitHub **ציבורי** | GitHub Actions ללא הגבלת דקות; המחיר: הקוד גלוי לצפייה (לא לעריכה), אך כל הסודות מוגנים בנפרד |
| GitHub Actions לתדירות **נמוכה יותר** מהמקורי (שעה, לא 30 דק') | `schedule` cron של GitHub לא אמין בתדירות גבוהה — נצפו סטיות של שעות. הוחלט לקבל דיוק נמוך יותר במקום להוסיף שירות חיצוני (cron-job.org) |
| VIX הוסר לגמרי (לא הוחלף ב-UVXY בסוף) | לא נמצא פתרון גרפי שנראה עקבי מספיק מול שאר הגרפים; Fear & Greed משמש תחליף מספק |
| `.history()` כמקור ראשי, `.info` כתוספת אופציונלית בלבד | `.info` נחסם/מוגבל לעיתים קרובות מ-IP ענן (Render) — `.history()` הוכח אמין |
| CSV S&P 500 (כולל תת-סקטור) כמקור ראשי לסקטור, לא `.info` | אמין לגמרי, לא תלוי בחסימות Yahoo |
| חיבור DB נפתח רק ממש לפני כתיבה (לא בתחילת ריצה ארוכה) | Neon auto-suspend מנתק חיבורים ישנים אחרי ~5 דק' חוסר פעילות |

---

## 11. פיצ'רים שהושלמו ✅ (מהסבב הזה — אוגוסט 2026)

- [x] **מעבר תשתית מלא**: Railway → Neon (DB) + Render (API) + GitHub Actions (pipelines), כולל העברת נתונים היסטוריים
- [x] עמוד **"בדיקה היסטורית"** (Backtest) מלא — equity curve, drawdown, פילוח RS, טבלת איתותים
- [x] עמוד **"ניתוח מניה"** (Deep Dive) מלא — כולל השוואת סקטור/תת-סקטור, מתחרים, פונדמנטלס, היסטוריית איתותים
- [x] **חיפוש חדשות** אמיתי מול ה-DB (לא רק 25 המוצגות), עם debounce
- [x] **תג Win-Rate** ברשימת המניות הראשית (לא רק בתוך המגירה)
- [x] תיקון תזמון `backfill_forward_returns` (14/28 יום נכונים במקום 30 סתמי)
- [x] ניסוי VIX (TradingView, Chart.js, lightweight-charts) — **נכשל בסופו של דבר, הוסר לגמרי**
- [x] עיצוב מחדש לחלון "השווה מול..."
- [x] הגדלת חץ הקיפול ברשימת המניות
- [x] תיקון קריטי: Neon auto-suspend מנתק חיבור DB ארוך-חיים → נפתר עם reconnect מאוחר + retry
- [x] תיקון קריטי: `.info` לא אמין מ-Render → `/api/lookup`, `/api/sector-comparison` נבנו מחדש סביב `.history()` + CSV
- [x] תיקון: מיפוי סקטור→ETF לא תאם בין שמות GICS (מה-CSV) לשמות yfinance — תוקן למילון ממוזג
- [x] תיקון: חוב-נטו לחברות בלי חוב מדווח (כמו SHOP) הוצג ריק במקום כ"עמדת מזומן נטו" — תוקן
- [x] תיקון: `market_cap` בפונדמנטלס תמיד הציג "-" (שדה לא היה קיים בתשובת ה-API בכלל) — תוקן, מחושב ממחיר×מניות
- [x] תיקון: גרף פונדמנטלס בעמוד Deep Dive לא נטען בפועל (כותרת בלי קריאת API מתחתיה) — חובר

---

## 12. מגבלות ידועות ⚠️ (מעודכן)

| מגבלה | פירוט |
|---|---|
| **`.info` של yfinance לא אמין מ-Render** | Yahoo חוסם/מגביל קריאות `.info` (שם חברה, שווי שוק, P/E, סקטור) מ-IP-ים של שרתי ענן בצורה אגרסיבית יותר מ-`.history()`. הפתרון: `.history()` כמקור ראשי בכל מקום שאפשר, CSV S&P 500 לסקטור/תת-סקטור, retry בודד (עם השהיה) איפה ש-`.info` הכרחי (P/E, תיאור חברה) — אך **לא פתרון מושלם**: מניות שלא ב-S&P 500 עדיין תלויות ב-`.info` ועלולות להציג שדות חסרים (P/E, תיאור) לעיתים. |
| **GitHub Actions `schedule` לא מדויק** | נצפו סטיות של **שעות**, לא דקות, בתדירות גבוהה (כל 30 דק'). הפתרון החלקי: הורדת התדירות לשעתית. פתרון מלא (טריגר חיצוני כמו cron-job.org) **נשקל ונדחה** ע"י המשתמש — מודעות מלאה לפשרה. |
| **Neon auto-suspend** | מכבה חישוב אחרי ~5 דק' חוסר פעילות ב-DB. כל קוד עתידי שעושה עבודה ארוכה לפני כתיבה ל-DB חייב לפתוח חיבור בסמוך לכתיבה, לא מראש. |
| **Neon branches** | הנתונים נמצאים ב-branch `import-2026-08...`, לא ב-branch הראשי. חשוב לזכור בכל שינוי DB עתידי. |
| **Render Free "נרדם"** | אחרי 15 דק' חוסר פעילות. `keep-render-awake.yml` (פינג כל 13 דק') אמור למנוע את זה — תלוי גם בדיוק תזמון GitHub Actions (ראה למעלה), אז לא מובטח ב-100%. |
| VIX | אין פתרון גרפי מספק — הוסר לגמרי, לא בתוכנית לחזור אליו |
| yfinance rate limit | timeout 15 שנ' + השהיה 0.3 שנ' בין טיקרים + circuit breaker |
| Google Translate חינמי | לא רשמי, יכול להיחסם — fallback לאנגלית |
| CNN Fear & Greed endpoint לא רשמי | תלוי במבנה JSON לא-מתועד של CNN, יכול להשתנות בלי התראה |
| סטטיסטיקת תבניות/Backtest ריקים בהתחלה | דורש 14-28+ יום שהאיתותים "יבשילו" — תקין, לא באג |

---

## 13. TODO עתידי 🔮

### עדיפות בינונית
- [ ] פילוח הכנסות לפי תחום עסקי ("איפה בדיוק מרוויחים כסף") — דורש מקור נתונים בתשלום או עבודה מורכבת יותר מול SEC XBRL segment reporting, לא מומש
- [ ] Alerts באימייל
- [ ] מדריך נרות יפניים לעמוד "פעימת השוק"
- [ ] Push notifications לפריצות

### עתיד רחוק
- [ ] OpenAI לסיכומים חכמים (כשיש הכנסה)
- [ ] Reddit/X social sentiment
- [ ] גרסת פרמיום + משתמשים מרובים (watchlist עדיין משותפת, לא per-user)
- [ ] לשקול מחדש טריגר חיצוני (cron-job.org) אם אי-הדיוק בתזמון GitHub Actions יהפוך לבעיה משמעותית בפועל

---

## 14. סיפור המעבר מ-Railway (אוגוסט 2026) — רקע לצוואר-בקבוק עתידי

תקופת הניסיון של Railway (30 יום, $5 קרדיט) עמדה להסתיים. הוחלט לעבור לערימת שירותים חינמיים-לצמיתות:

1. **Neon** (DB) — נבחר על בסיס: נרכשה ע"י Databricks (מאי 2025, חברה בשווי ~100 מיליארד $), אבטחת SOC 2 Type II, לא "תקופת ניסיון" אלא תוכנית חינמית קבועה.
2. **Render** (API) — נבחר כי תהליך ההרשמה/פריסה דומה ל-Railway (מחובר ל-GitHub), Free Web Service ללא הגבלת זמן (רק "הירדמות" אחרי חוסר פעילות).
3. **GitHub Actions** — הוחלט אחרי חישוב שגילה שריפו **פרטי** עלול לחרוג מ-2,000 דקות/חודש בגלל תדירות החדשות — הריפו הפך **ציבורי** כדי לפתור זאת לצמיתות.

**דרך אגב התגלו כמה תקלות אמיתיות** שלא היו קיימות ב-Railway (כי הסביבה שם התנהגה אחרת), ותוקנו כחלק מהמעבר: Neon auto-suspend מנתק חיבורי DB ארוכים (סעיף 5/7), `.info` של yfinance לא אמין מ-IP של Render (סעיף 12), ו-GitHub Actions `schedule` לא מדויק בתדירות גבוהה (סעיף 12).

**המשתמש ביקש במפורש** לא להוסיף שירותים חיצוניים נוספים (כמו cron-job.org) כדי לשמור על התהליך פשוט — התוצאה: תזמון "בערך" במקום "בול", כפשרה מודעת ומקובלת.

---

## 15. איך לפתוח שיחה חדשה

1. צרף קובץ זה (`CLAUDE.md`)
2. צרף את הקוד הרלוונטי (`index.html` / `api_server.py` / `pipeline_a_scanner.py` וכו')
3. תאר מה אתה רוצה לשנות
4. Claude ימשיך מיד ללא היסטוריית שיחה

**לעדכון הקובץ:** בקש "עדכן את CLAUDE.md" בסיום כל שינוי משמעותי.

---

*עדכון אחרון: אוגוסט 2026 | גרסה: MVP v3.0 (פוסט-מעבר מ-Railway ל-Neon/Render/GitHub Actions)*
