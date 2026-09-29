# CLAUDE.md — Swing Desk: Project Knowledge Base

> **מסמך חי.** מעדכנים אותו בסיום כל משימה משמעותית — רק כשמתבקש במפורש ("עדכן את CLAUDE.md").
> בכל שיחה חדשה: צרף קובץ זה. **חשוב:** קודם תעשה push ל-GitHub של כל הקבצים שהשתנו (ראה סעיף 14), ואז Claude יכול למשוך אותם ישירות מהריפו הציבורי.

---

## 1. חזון ומטרה

**Swing Desk** הוא דשבורד מסחר פיננסי בזמן אמת, המיועד לטריידרים קצרי-טווח (swing trading), עם דגש על **ניתוח טכני מבוסס תבניות מחיר אמיתיות** — לא רק אינדיקטורים גנריים. המטרה: לספק בממשק אחד את כל המידע הקריטי לקבלת החלטות מסחר — סריקת מניות טכנית ברמה גבוהה, חדשות מאקרו, גרפי סקטורים ומדדים, יומן דוחות, בדיקה היסטורית (Backtesting), וניתוח מעמיק למניה בודדת (כולל "מה מתקרב") — בלי לנווט בין עשרות מקורות.

**יעד נוכחי:** שימוש אישי, ברמה מקצועית ואמינה. **לא** מוצר רב-משתמשים/בתשלום כרגע — מעבר למודל כזה (הרשמה, חיוב, רישוי מסחרי לנתוני Yahoo) נדחה במפורש עד שהיעד ישתנה בפועל.

**כתובת האתר הנוכחית (Frontend):** https://swing-desk-tau.vercel.app
**כתובת ה-API הנוכחית (Backend):** https://swing-dashboard-3btx.onrender.com

---

## 2. ארכיטקטורה כללית

```
[GitHub Repository: sabatonissim/swing-dashboard — PUBLIC]
         |
         ├── Vercel (Frontend)
         │     └── index.html
         │
         ├── Render (Backend API — Free Web Service)
         │     └── api_server.py — FastAPI, "נרדם" אחרי 15 דק' חוסר פעילות
         │
         ├── Neon (Postgres — Free Tier)
         │     └── מסד הנתונים — "auto-suspend" אחרי ~5 דק' חוסר פעילות ב-DB
         │
         └── GitHub Actions (מחליף את ה-Cron Jobs של Railway — חינמי ללא הגבלה, כי הריפו ציבורי)
               ├── stock-scanner.yml          — מריץ pipeline_a_scanner.py, 2 טריגרים לכל סשן (ראשי + גיבוי ~15 דק' אחריו)
               ├── news-aggregator.yml        — מריץ pipeline_b_news_aggregator.py
               ├── keep-render-awake.yml      — פינג ל-/api/health כל 13 דק' כדי ש-Render לא ירדם
               └── cleanup-stale-signals.yml  — ידני בלבד (workflow_dispatch), מריץ cleanup_stale_signals.py
```

**עיקרון:** שינוי קוד → push ל-GitHub → Render ו-Vercel מתעדכנים אוטומטית (build+deploy). קבצי ה-workflows רצים לפי לוח הזמנים שמוגדר בהם, בלי תלות בשרת חי.

### ⚠️ למה הריפו ציבורי (החלטה מכוונת, לא שגיאה)
GitHub Actions מגביל ריפו **פרטי** ל-2,000 דקות ריצה/חודש בחינם. ריפו **ציבורי** מקבל דקות **ללא הגבלה**. הסודות (`DATABASE_URL` וכו') לא נחשפים — שמורים ב-GitHub Secrets מוצפנים, בנפרד לגמרי מהקוד.

### ⚠️ תקלת CORS אמיתית שקרתה — לזכור לעתיד
פעם אחת האתר נשאר תקוע לצמיתות על "מתחבר לשרת..." **למרות** שלוגי Render הראו בקשות מצליחות (200 OK). הסיבה: הבקשות המוצלחות בלוג היו רק מפינג ה-keep-alive האוטומטי — לא מהדפדפן בפועל. הדפדפן קיבל שגיאת **CORS**, כי ה-deploy האחרון ב-Render **לא באמת עלה** (נתקע/נכשל בשקט) והשרת המשיך להריץ קוד ישן. **אבחון:** F12 → Console בדפדפן (לא רק לוגי Render!). **תיקון:** Render → Deploys → "Deploy latest commit" ידנית.

### ⚠️ קובץ workflow חייב היכן שהוא רץ, לא היכן שהוא מתוזמן
קובץ `.yml` חייב להיות בתוך `.github/workflows/`, אבל כל סקריפט Python שהוא מריץ חייב להיות **בשורש הריפו**. שגיאה `can't open file '.../cleanup_stale_signals.py'` = כמעט תמיד זה.

---

## 3. קבצי הפרויקט

| קובץ | איפה רץ | תפקיד |
|---|---|---|
| `index.html` | Vercel | הדשבורד — ממשק המשתמש המלא, קובץ יחיד |
| `api_server.py` | Render | FastAPI — מחזיר נתונים מ-Postgres/yfinance/SEC/CNN לפרונט. **כולל עותק עצמאי של לוגיקת זיהוי התבניות** (ראה סעיף 7.1) |
| `pipeline_a_scanner.py` | GitHub Actions (`stock-scanner.yml`) | סורק טכני — מזהה תבניות, יומן דוחות, כותב ל-Postgres |
| `pipeline_b_news_aggregator.py` | GitHub Actions (`news-aggregator.yml`) | שולף חדשות RSS, מסווג, מתרגם, כותב ל-Postgres |
| `cleanup_stale_signals.py` | GitHub Actions (ידני בלבד) | סקריפט תחזוקה חד-פעמי |
| `requirements.txt` | Render + GitHub Actions | תלויות Python. (`openai` **הוסר** — לא היה בשימוש; קוד ה-OpenAI/סושיאל המת נמחק מ-`pipeline_a_scanner.py`) |
| `drift_test.py` (אופציונלי, מקומי) | המחשב / sandbox של Claude | בדיקת התאמה סינתטית סורק↔Deep Dive (ראה 7.1). לא רץ בשום שירות חי |
| `.github/workflows/*.yml` | GitHub Actions | תזמון |

---

## 4. טכנולוגיות

### Frontend
- **Vanilla HTML/CSS/JS** — קובץ אחד, בלי framework, בלי build process
- **TradingView Widget (חינמי, `tv.js`)** — הגרפים הראשיים בכל מקום, עם ממוצע נע 150 יום אדום. **החלטה מפורשת של המשתמש: נשאר כמו שהוא.** הווידג'ט החינמי לא מאפשר לצייר עליו קווים מהקוד שלנו (זה רק ב-Charting Library המסחרי), והמשתמש בחר **לא** להחליף ל-`lightweight-charts`. במקום זה יש תרשים סכמטי נפרד משלנו (SVG) בעמוד Deep Dive — ראה סעיף 7.1
- **Chart.js 4.5.0** — פונדמנטלס, סקטורים, equity curve
- **Treemap מותאם-אישית (vanilla JS)** — מפת חום דו-שכבתית לפי סקטור GICS
- **SVG inline שנוצר ב-JS** — מד פחד/תאווה (חצי-עיגול עם מחוג), תרשים "מבנה טכני" (נרות + קווים + תאריכים)
- **RTL עברית קבוע תמיד** (`dir="rtl"`), toggle שפה משנה רק טקסט
- **localStorage** ל: שפה, פילטר קטגוריות חדשות

### Backend
- **FastAPI + Uvicorn**, **psycopg2-binary**, **numpy/pandas**
- **yfinance** — `.history()` אמין; `.info` לא אמין מ-Render
- **SEC EDGAR API ישיר** — פונדמנטלס. **תומך גם ב-US-GAAP וגם ב-IFRS** (ראה סעיף 6)
- **CNN Fear & Greed** (endpoint לא-רשמי) — מד הפחד/תאווה
- **Wikipedia REST API** — fallback לתיאור חברה
- **feedparser** — RSS
- **תרגום חינמי** (Google gtx + MyMemory) — עם retry ותיקון-עצמי

### מה לא בשימוש (ולמה)
- ❌ **OpenAI/Anthropic API** — לא בשימוש בשום pipeline (גם הקוד המת והתלות `openai` הוסרו). חיבור AI (לסיווג חדשות ולסיכום יומי אמיתי) **נדחה במפורש** — המשתמש אמר "כרגע עדיין לא לשלם, ננסה עם מה שיש ובעתיד נכניס AI". מנוי Claude הוא חיוב **נפרד לגמרי** מ-Claude API.
- ⚠️ **VIX** — הוסר מהחישוב/מהמדד (Fear & Greed משמש תחליף), **אבל חזר כמספר אינפורמטיבי בלבד** בשורת המדדים העליונה (לא משפיע על שום ציון)
- ❌ **טוויטר/X** — נדחה (עלות API)
- ❌ **cron-job.org / UptimeRobot** — נדחה
- ❌ **Scraping של גוף מאמרים מאתרי חדשות** — נדחה במפורש (שביר טכנית + בעייתי מבחינת זכויות יוצרים). במקום זה מציגים את תקציר ה-RSS של המו"ל עצמו.
- ❌ **תבניות MACD** — המשתמש: "אני לא מתעסק עם תבניות כאלו". הסורק עדיין מזהה `macd_cross`, אבל **לא** נוסף ל-Deep Dive/סריקת רשימת המעקב.

---

## 5. מסד הנתונים (Postgres, Neon)

### ⚠️ נקודות קריטיות תפעוליות
- **Neon branches**: הנתונים ב-branch `import-2026-08...`, לא ב-`production`/`main`.
- **Neon auto-suspend**: כל קוד עם עבודה ארוכה **חייב לפתוח חיבור בסמוך לכתיבה**.

### `scanned_stocks` — תוצאות הסריקה
עמודות עיקריות: `ticker`, `trigger_text_he/en`, `pattern_type`, `swing_score`, `entry_price`, `support_level`, `resistance_targets`, `sma50/150/200`, `trend_stage`, `atr_value/pct`, `rs_rating`, `days_to_earnings`, `forward_return_10d/20d`, `breakout_volume_pct`, `change_pct`, `exchange`, `market_cap`, `avg_volume_20d`, `timestamp`.
`/api/stocks` מחזיר שורה אחת לכל טיקר (`DISTINCT ON`) + `repeat_count_14d`.
**ערכי `pattern_type` האפשריים:** `cup_handle`, `cup_no_handle`, `double_bottom`, `52w_high`, `horizontal_resistance_breakout`, `golden_cross`, `ma150_breakout`, `bull_flag`, `ascending_triangle`, `ascending_trendline`, `ma150_support_bounce`, `horizontal_level_bounce`, `macd_cross`, `momentum_surge`, `rsi_bounce`, `descending_trendline_breakout`.
**ניקוי היסטורי שבוצע:** `cleanup_stale_signals.py` מחק 10,664 מתוך 12,914 שורות (82%) חזרות ישנות.

### `macro_news` — חדשות (עודכן)
`id`, `category_tag`, `summary_he` (**הכותרת המתורגמת בלבד**, לא תקציר אמיתי), `summary_en` (הכותרת המקורית), `impact_level` (`High`/`Critical`), `source_url` (UNIQUE), `hook_he` (הסבר גנרי לקטגוריה, לא ספציפי לכתבה), **`body_en` / `body_he` (חדש)** — תקציר ה-RSS של המו"ל, מנוקה מ-HTML ומתורגם, `timestamp`.
- העמודות החדשות נוספות ע"י `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` ב-`init_db()` של `pipeline_b` — **רק בריצה הבאה של pipeline_b אחרי ה-deploy**. כתבות ישנות לא יקבלו body; רק חדשות מכאן והלאה.
- ל-`api_server.py` יש `CREATE TABLE IF NOT EXISTS macro_news` משלו **בלי** עמודות ה-body (בלתי מזיק כי כל ה-endpoints משתמשים ב-`SELECT *`; אם תהיה בעיית סכמה — להוסיף שם גם את ה-ALTER).
- פידי Google News בדרך כלל לא נותנים body אמיתי (ה-summary שם הוא רק הכותרת עטופה בקישור) — `_clean_rss_body` מחזיר ריק ואז לא מוצג "עוד מהמקור".

### `analytics_events`, `ui_strings`
- `analytics_events` (`event_type`, `entity_id`, `session_id`) — נכתב ע"י `POST /api/track` (הפרונט קורא לו דרך `track()`: חיפושי מניות, הוספה לרשימת מעקב וכו'). נקרא ע"י `/api/analytics/*`.
- `ui_strings` — טקסטי ממשק שמוגשים ע"י `/api/ui-strings`.

### `universe_movers`, `earnings_calendar`, `sec_cik_cache`, `watchlist`
- `universe_movers`: heatmap + fallback "הכי זזות".
- `earnings_calendar`: `ticker`, `report_date`, `session`, `market_cap`, `eps_estimate/actual`, `surprise_pct`, `revenue_estimate/actual`, `revenue_surprise_pct`. `PRIMARY KEY (ticker, report_date)`. `revenue_estimate` נשמר עם COALESCE שמעדיף את הערך **הישן** (ה-"0q" של yfinance מתגלגל קדימה אחרי הדיווח); `eps_actual`/`revenue_actual` מעדיפים את החדש.
- `sec_cik_cache`: מיפוי טיקר→CIK.
- `watchlist`: `ticker` (PK), `added_at` — **רשימה אחת משותפת, בלי משתמשים**.

### משתני סביבה
`DATABASE_URL` (Neon), `ALLOWED_ORIGINS` (`https://swing-desk-tau.vercel.app`).

---

## 6. API Endpoints

| Endpoint | תיאור |
|---|---|
| `/api/health` | חיות + DB |
| `/api/stocks` | מניות מסומנות |
| `/api/macro-news`, `/api/macro-news/search` | חדשות (`SELECT *` — כולל `body_*` כשקיימים) |
| **`/api/daily-digest?hours=24&top_n=6`** | **חדש.** סיכום יומי **בלי AI**: `critical` (כל הקריטיות) + `hot_topics` (הקטגוריות עם הכי הרבה כתבות, כל אחת עם כתבה מייצגת — קריטית קודם, אחר כך הכי חדשה) + `total_items` |
| **`/api/market-ticker`** | **חדש.** S&P 500 / נאסד"ק / דאו / VIX / ביטקוין / את'ריום / זהב. yfinance `.history(5d)` לכל סמל, קאש 90 שניות, מחזיר `price`, `change_points`, `change_pct`. fallback לקאש האחרון בכשל |
| **`/api/technical-setup/{ticker}`** | **חדש.** מבנה טכני נוכחי — ראה 7.1. מחזיר `candidates[]` (ממוינים), `scan_match`, `price_series` (OHLC, 160 נרות אחרונים) |
| **`/api/watchlist-setups`** | **חדש.** אותה בדיקה על כל טיקרי ה-`watchlist` במקביל (`ThreadPoolExecutor(6)`), מחזיר רק את מי שיש לו מועמד; לכל טיקר המועמד העליון בלבד; פעילים קודם, אחר כך לפי מרחק |
| `/api/earnings-calendar` | יומן דוחות. חלון **-6/+7 ימים** (היה -2/+7), 40 הגדולים לפי שווי שוק **+ טיקרים שכבר דיווחו (יש `eps_actual`) מוגנים מדחיקה** |
| `/api/fundamentals/{ticker}` | SEC EDGAR. **עודכן:** (א) התאמת טיקר מנרמלת מקף/נקודה (`BRK-B`→`BRKB`); (ב) fallback ל-**IFRS** (`ifrs-full` ממוזג עם `us-gaap` + שמות שדות מקבילים) לחברות זרות שמגישות 20-F |
| **`/api/price-compare?tickers=A,B,C&period=1M\|3M\|6M\|YTD\|1Y`** | **שוחזר (חסר בשרת עד v5 — הפרונט קרא לו וקיבל 404).** עד 3 טיקרים, מחזיר `dates[]` + `series[{ticker, values[]}]` באחוזי שינוי מתחילת התקופה. קאש 5 דק' |
| `/api/track` (POST), `/api/analytics/top-interest`, `/api/analytics/summary`, `/api/ui-strings` | אנליטיקה פנימית וטקסטי ממשק (היו קיימים ולא מתועדים) |
| `/api/lookup/{ticker}`, `/api/sector-comparison/{ticker}`, `/api/stock-news/{ticker}`, `/api/backtest*`, `/api/pattern-stats`, `/api/signal-history/{ticker}`, `/api/heatmap`, `/api/market-movers`, `/api/sector-performance`, `/api/fear-greed`, `/api/watchlist` | ללא שינוי מהותי. **כולם מנרמלים טיקר דרך `_canon_ticker`** (ראה למטה). `/api/lookup` מנסה פעמיים: 404 רק אם Yahoo החזיר ריק פעמיים בלי שגיאה; חריגה = 502 ("תקלה זמנית") |

### נרמול טיקרים (class shares) ⚠️
מניות כמו BRK.B / BF.B: **Yahoo, ה-DB וה-SEC משתמשים במקף (`BRK-B`), TradingView משתמש בנקודה (`BRK.B`)**. הפרונט: `canonTicker()` (קלט → מקף, לכל קריאת API) ו-`tvSymbol()` (מקף → נקודה, **רק** לווידג'טים של TradingView). השרת: `_canon_ticker()` בכל endpoint. לקח: `BRK-B` ב-TradingView פתח בשקט רישום אחר (`BRK-B-or1-TSX`, טורונטו) עם מחיר וגרף לא קשורים. הרגקס מתאים רק לצורה `^[A-Z]{1,5}\.[A-Z]$` כך שסיומות בורסה (`RY.TO`) לא נפגעות.

---

## 7. Pipeline A — סורק מניות טכני

### לוח זמנים
`stock-scanner.yml`: 2 טריגרים לכל סשן (ראשי + גיבוי ~15 דק'). `should_skip_scan()` מונע כפילות (90 דק'). **לעדכן ידנית ליד סוף אוקטובר/מרץ** (שעון קיץ/חורף).

### Universe
S&P 500 + Nasdaq 100 + תוספות. פילטרים: מחיר > $10, נפח 20 יום > 1.5M, שווי שוק > $1.5B, NYSE/NASDAQ. `build_scan_universe()` מסובב סדר לפי יום בשנה.

### תבניות — 15 גלאים (16 ערכי `pattern_type`, כי כוס עם/בלי ידית הם גלאי אחד)
רמה 1 (מבניות, ציון גבוה): Cup & Handle, Double Bottom, 52w high, פריצת התנגדות אופקית, Golden Cross, פריצת ממוצע 150, Bull Flag, משולש עולה, קו מגמה עולה, קפיצה על ממוצע 150, קפיצה על תמיכה אופקית. רמה 2 (גנריים, ציון נמוך): MACD, Momentum Surge, RSI Bounce. + שבירת קו מגמה יורד.
**סדר העדיפויות כשכמה תבניות מתאימות למניה אחת** (שרשרת `elif` בסורק): cup_handle > double_bottom > 52w_high > cup_no_handle > horizontal_resistance_breakout > golden_cross > ma150_breakout > bull_flag > ascending_triangle > ascending_trendline > ma150_support_bounce > horizontal_level_bounce > macd_cross > momentum_surge > rsi_bounce > descending_trendline_breakout (קטגוריית "else" — הכי נמוך).
**Freshness check** לכל תבניות הפריצה (אתמול לא חצה, היום חוצה) — מונע ריבוי סימון.
**זיהוי הכוס:** הגלאי מחפש צורת כוס (שפה שמאלית → תחתית → התאוששות) — **אין זיהוי נפרד של "ידית"** ב-Deep Dive.

### יומן דוחות שבועי
40 הגדולים (לפי שווי שוק) בחלון **-6/+7 ימים** + טיקרים מוגנים שכבר דיווחו. **לקח:** `revenue_actual` מגיע מ-`quarterly_income_stmt` של yfinance באיחור של כמה ימים אחרי ה-EPS, ולכן חלון אחורי של יומיים מחק שורות לפני שההכנסות הגיעו. **לקח שני:** הרחבת החלון מגדילה את מאגר המועמדים שמתחרים על 40 המקומות, ודחקה החוצה דיווח ישן יותר — לכן ההגנה על `eps_actual IS NOT NULL`. בכל ריצה נכתב לוג כמה שורות "דיווחו" עדיין בלי `revenue_actual`.

### 7.1 מבנה טכני נוכחי (Deep Dive) — עותק "מועמד" של הגלאים
**איפה:** `api_server.py` (פונקציות `_setup_*`, `_compute_setup_candidates`, `_SETUP_EXPLANATIONS`, `_SETUP_PRIORITY`, `_SCANNER_PATTERN_TO_SETUP_ID`). **לא מייבא** את `pipeline_a_scanner.py` (שני שירותים נפרדים) — הלוגיקה **משוכפלת בכוונה**. ⚠️ **סיכון drift:** שינוי גלאי בסורק דורש שינוי מקביל כאן, אחרת ההתאמה בין הסריקה ל-Deep Dive תישבר (זה קרה עם IFF, ואחר כך עם KDP — ראה "התאמה מלאה לסורק" למטה).

**התאמה מלאה לסורק (v6) — `SCANNER PARITY` ב-`api_server.py`:** מדידה סינתטית (כמה אלפי סדרות מחיר) הראתה ש-4 גלאים סטו לגמרי: קו מגמה עולה (סורק: רגרסיה על 60 ימים; Deep Dive: קו בין שני שפלים על 150 יום — **94% מהסימונים של הסורק לא נמצאו**), משולש עולה (100%), שבירת קו מגמה יורד (95%) ופריצת ממוצע 150 (47%). תיקון: לכל אחד מהם יש עכשיו פונקציית `_scanner_rule_*` שהיא **העתק שורה-בשורה של חוק הסורק**, וה-`_setup_*` המתאים קורא לה **קודם**; אם הסורק היה מסמן — Deep Dive מציג את אותה תבנית באותה רמה. כך גם נסגרו פערים קטנים ב-52w high (הסורק מסמן כל סגירה מעל השיא, לא רק עד 3%), קפיצה מממוצע 150, קפיצה מרמה אופקית וצלב זהב (חלון של 3 ברים). Deep Dive מושך עכשיו `2y` (כמו הסורק) ולא `1y`. אחרי התיקון: 0 פספוסים ב-8,000 סדרות, ורמות המפתח זהות לרמות הסורק.

**`scan_signal` ב-`/api/technical-setup`:** אם הסורק סימן את המניה ב-5 הימים האחרונים אבל התבנית לא מתקיימת בבדיקה החיה (המחיר זז), או שזה `macd_cross` (לא נבדק ב-Deep Dive) — השרת מחזיר `scan_signal.reproduced=false` והפרונט מציג הודעה כתומה מעל התוצאה, **במקום להציג בשקט תבנית אחרת** (זה מה שקרה ב-KDP: סורק = קו מגמה, Deep Dive = תחתית כפולה).

**באג שנמצא ותוקן בסורק:** ב-`detect_horizontal_level_bounce` הושוותה שבר (0.012) לאחוז מעוגל (1.2), ולכן נבחרה הרמה **האחרונה** שעונה על התנאי ולא **הקרובה ביותר** לשפל היום. תוקן; סימונים חדשים יציגו את הרמה הקרובה. סימונים ישנים ב-DB לא משתנים.

**כלל לעתיד:** שינוי גלאי בסורק → לשנות את ה-`_scanner_rule_*` התאום ולהריץ `drift_test.py` (ראה 3).

**ההבדל מהסורק:** הסורק מסמן רק **ביום הפריצה עצמה** (בינארי). כאן כל גלאי מחזיר גם **תבנית שעדיין נבנית**: `stage` = `approaching` / `triggered` / `holding`, `key_level`, `distance_pct`, `lines[]` (קואורדינטות לשרטוט), ולפעמים `target_price`/`target_pct` (מדידת גובה קלאסית: כוס, משולש עולה, דגל שורי, תחתית כפולה).

**13 תבניות:** cup_and_handle, double_bottom, week52_high_breakout, bull_flag, ascending_triangle, golden_cross, ascending_trendline_support, descending_trendline_breakout, horizontal_resistance_breakout, horizontal_support_bounce, ma150_support (ממזג את ma150_breakout + ma150_support_bounce של הסורק), rsi_oversold_bounce, momentum_surge. **בלי MACD.** RSI ו-momentum **בלי קווים** (אין רמת מחיר) — רק טקסט.

**בחירת המועמד הראשי:** (1) פעיל (`triggered`/`holding`) קודם, לפי `_SETUP_PRIORITY` (אותו סדר יחסי כמו שרשרת ה-elif בסורק); (2) אחרת ה-`approaching` הקרוב ביותר לטריגר. (3) **`scan_match`:** אם `scanned_stocks` מכיל לטיקר איתות ב-5 הימים האחרונים והתבנית תואמת מועמד שנמצא — היא מוקפצת לראש ומסומנת `matches_scan`.

**לקחים שנלמדו (IFF):** (א) בפריצת התנגדות אופקית, הסורק בוחר את הרמה **הגבוהה ביותר שנפרצה** (סגירה ≥ רמה×1.01), לא "הקרובה ביותר למחיר" — הגרסה הראשונה שלנו בחרה לא נכון והציגה מספר אחר. (ב) קווי מגמה: **לא רגרסיה** (נראית שרירותית, לא נוגעת בפתילים) אלא חיבור בין **שתי נקודות swing אמיתיות** (ראשונה ואחרונה) + בדיקה שאף swing אחר לא שובר את הקו ביותר מ-2%. (ג) כשהרמה נפרצה — התווית: "התנגדות → הפכה לתמיכה".

**Frontend:** `renderTechnicalSetup` / `buildTechnicalSetupSvg` — נרות OHLC אמיתיים + קווים + **תאריך עוגן ליד כל קו + ציר תאריכים** (התחלה/אמצע/היום, כדי לדעת אם זה ימים או שבועות). מצייר את המועמד הראשי **ועד 2 נוספים** שפעילים או בטווח 6% ומציג אותם ברשימה מתחת. ממוקם ב-Deep Dive **אחרי שורת הסטטיסטיקות ולפני "על החברה"**. דיסקליימר בכל הסבר: ניתוח טכני היסטורי, לא המלצה.

---

## 8. Pipeline B — אגרגטור חדשות

- **סיווג:** `classify_headline` — כותרת+תקציר RSS, שקלול תדירות, `EXCLUSION_PHRASES`, `GEO_AMBIGUOUS_KEYWORDS` דורשים הקשר כלכלי.
- **`CRITICAL_KEYWORDS`:** War, Sanctions, Attack, Federal Reserve, Rate Hike, Rate Cut, Recession, Bankruptcy, Crisis. כל מה שנשמר הוא כבר `High` או `Critical` (סף מינימלי בכניסה).
- **"חדשות שוברות" בפרונט** = `impact === 'Critical'` **בלבד**. (היה בנוסף סינון תגיות רחב, `MACRO_CRITICAL_TAGS`, שתפס כמעט כל קטגוריה — **הוסר**.) אותן כתבות ממשיכות להופיע גם בפיד הרגיל. הטקסט בכרטיס = בדיוק אותו `summary_he` של הפיד, בלי ניסוח נפרד.
- **גוף כתבה (חדש):** `_clean_rss_body` שומר את תיאור ה-RSS של המו"ל (מנוקה מ-HTML, מתורגם) ל-`body_en`/`body_he`. בדראוור של כתבה: "עוד מהמקור" (אם קיים) + לינק למקור. **לא scraping.** מקורות ישירים (CNBC, MarketWatch, Investing) בדרך כלל נותנים משפט-שניים; פידי Google News בדרך כלל ריקים.
- **תרגום:** jitter+retry, `retry_failed_translations()` תיקון-עצמי (72 שעות אחרונות). חדשות ספציפיות למניה — נתיב תרגום נפרד on-demand.
- **לוח זמנים:** כל שעה, 17 דק' אחרי השעה.

---

## 9. Frontend — index.html

### ניווט — 4 טאבים
בית | פעימת השוק | בדיקה היסטורית | ניתוח מניה

### חדש בסבב הזה
- **שורת מדדים עליונה** (`ticker-tape`, מתחת ל-`<header>`): 7 מכשירים, מספר + נקודות + אחוז (`+62.30 (+0.39%)`), גוללת ברצף אינסופי, נעצרת ב-hover, ממלאת את כל הרוחב. מתעדכנת כל 90 שניות.
  - ⚠️ **לקח (3 סבבי תיקון):** אנימציית `translateX(-50%)` **על אחוזים** בתוך קונטיינר flex "נעלמה" אחרי מחזור אחד. הפתרון היציב: track עם `position:absolute`, **שני מקטעים זהים** (`ticker-seg-a/b`), כל מקטע מוארך (חוזר על 7 הפריטים) עד שרוחבו **הנמדד בפיקסלים** ≥ רוחב המסך, והאנימציה זזה בדיוק ב-`--ticker-shift` = רוחב מקטע אחד בפיקסלים.
- **חדשות שוברות:** רק Critical (ראה סעיף 8).
- **מד פחד/תאווה:** חצי-עיגול SVG עם 5 אזורי צבע ומחוג (`buildFearGreedGaugeSvg`). ⚠️ **לקח:** הנתון נשמר ב-`lastFearGreedData` ו-`applyLang()` קורא ל-`renderFearGreed` מחדש — בלי זה המד "נתקע" על השפה האחרונה שנטענה. **כלל:** כל ווידג'ט חדש שמחזיק נתונים בזיכרון חייב להיות מרונדר מחדש ב-`applyLang()`.
- **סיכום יומי:** כפתור "📰 סיכום יומי" ליד כותרת עמודת החדשות; פותח **מודל גדול וממורכז** (`digest-overlay`/`digest-modal`, עד 860px) — "הנושאים עם הכי הרבה סיקור" **קודם**, אחר כך "קריטי". מסביר בגלוי שזה נפח סיקור ולא AI.
- **רשימת מעקב → כפתור 📈 "מה מתקרב":** מופיע רק במצב "רשימת מעקב" ליד ה-`+`; פותח את אותו מודל גדול עם תוצאות `/api/watchlist-setups`; לחיצה על שורה פותחת את מגירת המניה.
- **Deep Dive → "מבנה טכני נוכחי"** (סעיף 7.1).
- **דראוור כתבה:** "עוד מהמקור" (`body_he`/`body_en`).
- **מקרא מקורות מידע (כפתור ⓘ):** עודכן ל-7 חטיבות — מחירים+שורת מדדים, פונדמנטלס (כולל הסבר כנה על היעדר נתונים), חדשות+סיכום יומי, סריקה+מבנה נוכחי, מד פחד/תאווה (CNN), יומן דוחות, הבהרה.
- **פונדמנטלס — הודעת "אין נתונים":** כבר לא מצהירה "כנראה ETF/מדד" כעובדה, אלא "קורה ב-ETF/מדדים, אבל גם בהנפקות טריות או חברות עם מבנה דיווח חריג".

### ⚠️ תקלה קריטית שנלמדה קודם: RTL + `left`/`right` יחד
לעולם לא להגדיר גם `left` וגם `right` על אותו אלמנט כשה-`left` נקבע דינמית ב-JS (ב-RTL הדפדפן מתעלם מה-`left`).

### שאר העמודים
מפת חום דו-שכבתית לפי סקטור; "פעימת השוק" + יומן דוחות מקובץ לפי יום; "בדיקה היסטורית" עם שמות תבניות ידידותיים ו-equity curve ב-10% מההון לאיתות; פילטר תבניות; פילטר קטגוריות חדשות persisted; תיקוני מובייל (`max-width:480px`).

---

## 10. החלטות ארכיטקטורה מרכזיות

| החלטה | סיבה |
|---|---|
| Neon + Render + GitHub Actions | Railway בתשלום |
| ריפו ציבורי | Actions ללא הגבלה |
| 2 טריגרים לסשן | `schedule` לא אמין |
| `.history()` ראשי, SEC/Wikipedia כ-fallback | `.info` נחסם מ-IP ענן |
| תבניות מבניות > גנריות בציון | המשתמש אנליסט טכני |
| כל תבניות הפריצה עם freshness check | מניעת ניפוח היסטורי |
| yfinance-based earnings, לא X | X API בתשלום |
| equity curve ב-10% מההון | full-stake יוצר קריסה מלאכותית |
| סיווג חדשות לפי מילות מפתח, לא AI | חינמי בלבד עד שהאתר יהפוך למוצר |
| **גוף כתבה = תקציר RSS בלבד, לא scraping** | חוקיות + שבירות |
| **סיכום יומי = נפח סיקור, לא AI** | החלטה מפורשת: AI בעתיד, לא כרגע |
| **TradingView widget נשאר; תרשים סכמטי נפרד** | הווידג'ט החינמי לא ניתן לשרטוט; המשתמש דחה מעבר ל-lightweight-charts |
| **Deep Dive משכפל את גלאי הסורק (לא מייבא)** | שני שירותים נפרדים; מחיר: סיכון drift |
| **סדר עדיפויות ב-Deep Dive = שרשרת ה-elif של הסורק** | התאמה מלאה בין מה שהסריקה הראתה למה שה-Deep Dive מציג |
| **בלי MACD ב-Deep Dive** | המשתמש לא עוסק בו |
| חיבור DB רק לפני כתיבה | Neon auto-suspend |

---

## 11. פיצ'רים שהושלמו ✅ (מצטבר)

- מעבר תשתית Railway → Neon+Render+GitHub Actions
- בדיקה היסטורית, ניתוח מניה, מפת חום treemap לפי סקטור (כולל תיקון RTL)
- 15 תבניות בסורק; ממוצע 150 אדום; freshness-check; ניקוי 82% היסטוריה
- יומן דוחות שבועי (EPS + הכנסות) — **עם חלון מורחב והגנה על דיווחים שכבר יצאו**
- שכתוב סיווג חדשות; תרגום חינמי מחוזק; פילטרי קטגוריות/תבניות
- **[חדש] שורת מדדים עליונה** נגללת ברצף (אומת ע"י המשתמש ✅)
- **[חדש] חדשות שוברות מחמירות** — רק Critical (אומת ✅)
- **[חדש] מד פחד/תאווה מעגלי** + תיקון החלפת שפה (אומת ✅)
- **[חדש] סיכום יומי** כמודל גדול, נושאים חמים קודם (אומת ✅)
- **[חדש] מבנה טכני נוכחי ב-Deep Dive** — 13 תבניות, נרות + קווים + תאריכים, `scan_match`
- **[חדש] סריקת "מה מתקרב" על רשימת המעקב** (📈)
- **[חדש] גוף כתבה מ-RSS** בדראוור
- **[חדש] תיקוני פונדמנטלס:** מקף/נקודה בטיקר (`BRK-B`) + IFRS לחברות זרות + ניסוח כנה
- **[חדש] עדכון מקרא ⓘ**

---

## 12. מגבלות ידועות ⚠️

| מגבלה | פירוט |
|---|---|
| `.info` של yfinance לא אמין מ-Render | פתרון מדורג `.history()` → SEC → Wikipedia → CSV |
| GitHub Actions `schedule` לא מדויק | ממוזג חלקית ע"י 2 טריגרים |
| Neon auto-suspend + branches | חיבור DB בסמוך לכתיבה |
| Render Free נרדם | עלול להיראות כמו CORS/deploy תקוע — לבדוק Console |
| NTM P/E | תלוי `.info`, יכול להישאר "לא זמין" |
| **סריקת רשימת מעקב** | קריאת yfinance נפרדת לכל טיקר (במקביל, 6 threads) — עלולה להיות איטית ב-Render חינמי עם רשימה ארוכה |
| **`body_he`/`body_en`** | רק לכתבות שנקלטו אחרי ה-deploy; פידי Google News לרוב ריקים; איכות/אורך תלויים במו"ל |
| **פונדמנטלס** | ETF/מדד אמיתי עדיין יקבל "אין נתונים" (נכון). חברות עם תגי XBRL חריגים (לא מהרשימות שלנו) עדיין עלולות להחסיר שדות |
| **Deep Dive: כוס בלי זיהוי ידית** | רק צורת הכוס |
| **drift אפשרי** בין גלאי הסורק לעותק ב-`api_server.py` | טופל ב-v6 ל-14 הגלאים שנמדדו (7.1), אבל זה עדיין שני עותקים — כל שינוי בסורק דורש עדכון תאום + `drift_test.py`. **הקו בגרף הוא התאמה לינארית (לא בהכרח נוגע בפתילים)** לקו מגמה עולה/יורד, כי זה חוק הסורק |
| תרגום חינמי, CNN Fear & Greed | לא-רשמיים, יכולים להישבר |
| סיווג חדשות לפי מילות מפתח | דיוק מוגבל; החלטה מודעת |

---

## 13. TODO עתידי 🔮

**הכי מבטיח להמשך:**
- [ ] **חיבור backtest אמיתי ל"מתקרב"** — `/api/pattern-stats` / `/api/backtest` כבר מכילים win-rate לכל תבנית; לחבר כך שה"פוטנציאל %" בדיפ-דייב יגובה בנתונים היסטוריים ("כשמניה הייתה במרחק כזה מפריצה כזו, ב-X% מהמקרים זה קרה תוך Y ימים") במקום נוסחת מדידת גובה בלבד
- [ ] **אימות בפועל** של כל מה שנבנה ב-sandbox בלי גישה לשירותים חיים (ראה סעיף 15)
- [ ] סיכום יומי **עם AI אמיתי** (כשיוחלט לשלם) — מבנה התשובה של `/api/daily-digest` תוכנן כך שאפשר להחליף/להעשיר בלי לשנות את הפרונט
- [ ] ניסוח קצר וממוקד ייעודי ל"חדשות שוברות" (כרגע אותו טקסט של הפיד) — רק אם המשתמש ירצה

**ישנים:**
- [ ] טבלת watchlist מלאה (score/P/E/RS לכל המעקב)
- [ ] סיווג חדשות מבוסס AI
- [ ] פילוח הכנסות לפי תחום עסקי (מקור בתשלום)
- [ ] Alerts באימייל/webhook; Push notifications
- [ ] מדריך נרות יפניים ל"פעימת השוק"
- [ ] רק אם עוברים למוצר רב-משתמשים: הרשמה, רישוי Yahoo, rate limiting, pytest, ניטור

---

## 14. איך לפתוח שיחה חדשה

1. **קודם — push ל-GitHub** של כל 4 הקבצים ששונו בסבב הזה: `index.html`, `api_server.py`, `pipeline_a_scanner.py`, `pipeline_b_news_aggregator.py` (+ המתן שה-deploy ב-Render/Vercel יסתיים; ב-Render כדאי לוודא ידנית שה-deploy האחרון באמת עלה — ראה תקלת CORS).
2. בשיחה החדשה: צרף `CLAUDE.md` הזה.
3. בקש מ-Claude למשוך את הקוד העדכני **ישירות מ-GitHub** (`raw.githubusercontent.com/sabatonissim/swing-dashboard/main/...`) — או, אם לא בטוח שה-push עבר, העלה את 4 הקבצים ידנית.
4. תאר מה לשנות/לבדוק.
5. בסיום שינוי משמעותי: "עדכן את CLAUDE.md".

---

## 15. מצב אימות — מה נבדק ומה עדיין לא

**חשוב:** כל הפיתוח בסבב הזה נעשה ב-sandbox **בלי גישה לשירותים חיים** (yfinance, SEC, Neon, Render חסומים). הקוד נבדק רק ע"י: קומפילציה (`py_compile`, `node --check`) + בדיקות ריצה על **נתוני מחיר סינתטיים** (בלי קריסות, פלט הגיוני). **לא** נבדק מול נתונים אמיתיים.

**אומת ע"י המשתמש באתר החי ✅:** שורת המדדים, חדשות שוברות, מד פחד/תאווה + החלפת שפה, גודל וסדר הסיכום היומי, **וה-deploy האחרון ב-Render עלה תקין (v5).**

**נמצא ע"י המשתמש בבדיקה של v5 (וטופל ב-v6, ממתין לאימות חי):** (1) `BRK.B` לא נמצא ב-Deep Dive; `BRK-B` הציג גרף לא קשור (סמל TradingView שגוי) ומחיר/ממוצע לא תואמים; (2) מניית ADR החזירה "הטיקר לא נמצא"; (3) KDP: סריקה = התאוששות מקו מגמה עולה, Deep Dive = תחתית כפולה.

**עדיין לא אומת ⏳ (לבדוק אחרי ה-push של v6):**
0. **[v6]** `BRK.B` ו-`BRK-B` (גם בחיפוש העליון וגם ב-Deep Dive): אותו גרף NYSE נכון, מחיר וממוצע הגיוניים. פונדמנטלס של BRK-B — לא נבדק מול SEC אמיתי. ADR: לבדוק שוב ולציין איזה טיקר (הסיבה המדויקת לא אובחנה; נוסף retry והודעת שגיאה מדויקת). KDP: אותה תבנית ואותה רמה כמו בכרטיס הסריקה. גרף ההשוואה בין מניות (`/api/price-compare` שוחזר). **הבדיקות ב-v6 היו על נתונים סינתטיים ועל Yahoo מדומה — לא מול שירותים חיים.**
1. **IFF ועוד מניות:** האם המבנה הטכני ב-Deep Dive תואם עכשיו את כרטיס הסריקה (אותה תבנית, אותה רמה — למשל $82.97)?
2. **7 התבניות החדשות** (52w high, דגל שורי, צלב זהב, תחתית כפולה, RSI, momentum + ההרחבות) — האם מופיעות ומשורטטות סבירות על מניות אמיתיות?
3. **תאריכי העוגן והציר** בתרשים הסכמטי.
4. **סריקת רשימת המעקב (📈)** — נכונות ומהירות.
5. **גוף כתבה** — יופיע רק אחרי ריצת pipeline_b הבאה ורק לכתבות חדשות.
6. **יומן דוחות** — האם הדיווח של "יום רביעי שעבר" חזר (הגנה על `eps_actual`)? והאם `revenue_actual` מתמלא (לוג "still missing revenue_actual")?
7. **פונדמנטלס** — מניות עם מקף (`BRK-B`) ו-ADR זרים (IFRS) — האם עכשיו יש נתונים? ואיך נראים המספרים שנמשכו מ-IFRS (לא נבדקו מול SEC אמיתי).

---

*עדכון אחרון: 29 בספטמבר 2026 | גרסה: v6 — נרמול טיקרים (BRK-B/TradingView), התאמה מלאה בין הסורק ל-Deep Dive (`scan_signal` + חוקי `_scanner_rule_*`), שחזור `/api/price-compare`, retry ל-`/api/lookup`, תיקון באג בחירת רמה בסורק, ניקוי קוד OpenAI/סושיאל המת, תיעוד endpoints ו-tables שלא היו מתועדים*
