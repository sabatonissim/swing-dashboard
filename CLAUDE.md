# CLAUDE.md — Swing Desk: Project Knowledge Base

> **מסמך חי.** מעדכנים אותו בסיום כל משימה משמעותית — רק כשמתבקש במפורש ("עדכן את CLAUDE.md").
> בכל שיחה חדשה: צרף קובץ זה + הקבצים הרלוונטיים. אין צורך בהיסטוריית צ'אט.

---

## 1. חזון ומטרה

**Swing Desk** הוא דשבורד מסחר פיננסי בזמן אמת, המיועד לטריידרים קצרי-טווח (swing trading), עם דגש על **ניתוח טכני מבוסס תבניות מחיר אמיתיות** — לא רק אינדיקטורים גנריים. המטרה: לספק בממשק אחד את כל המידע הקריטי לקבלת החלטות מסחר — סריקת מניות טכנית ברמה גבוהה, חדשות מאקרו, גרפי סקטורים ומדדים, בדיקה היסטורית (Backtesting), וניתוח מעמיק למניה בודדת — בלי לנווט בין עשרות מקורות.

**מודל עסקי עתידי:** אתר ציבורי עם פוטנציאל מוניטיזציה (מנויים, פרמיום).

**כתובת האתר הנוכחית (Frontend):** https://swing-desk-tau.vercel.app
**כתובת ה-API הנוכחית (Backend):** https://swing-dashboard-3btx.onrender.com

---

## 2. ארכיטקטורה כללית (מאוגוסט 2026, מעבר מ-Railway)

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
               ├── stock-scanner.yml   — מריץ pipeline_a_scanner.py
               ├── news-aggregator.yml — מריץ pipeline_b_news_aggregator.py
               └── keep-render-awake.yml — פינג ל-/api/health כל 13 דק' כדי ש-Render לא ירדם
```

**עיקרון:** שינוי קוד → push ל-GitHub → Render ו-Vercel מתעדכנים אוטומטית (build+deploy). קבצי ה-workflows רצים לפי לוח הזמנים שמוגדר בהם, בלי תלות בשרת חי.

### ⚠️ למה הריפו ציבורי (החלטה מכוונת, לא שגיאה)
GitHub Actions מגביל ריפו **פרטי** ל-2,000 דקות ריצה/חודש בחינם. ריפו **ציבורי** מקבל דקות **ללא הגבלה**. הסודות (`DATABASE_URL` וכו') לא נחשפים — שמורים ב-GitHub Secrets מוצפנים, בנפרד לגמרי מהקוד.

### ⚠️ תקלת CORS אמיתית שקרתה — לזכור לעתיד
פעם אחת האתר נשאר תקוע לצמיתות על "מתחבר לשרת..." **למרות** שלוגי Render הראו בקשות מצליחות (200 OK). הסיבה: הבקשות המוצלחות בלוג היו רק מפינג ה-keep-alive האוטומטי (IP קבוע, קצב קבוע) — לא מהדפדפן בפועל. הדפדפן קיבל שגיאת **CORS** (`No 'Access-Control-Allow-Origin' header`), כי ה-deploy האחרון ב-Render **לא באמת עלה** (נתקע/נכשל בשקט) והשרת המשיך להריץ קוד ישן עם קונפיגורציית CORS שונה — **גם כשה-`ALLOWED_ORIGINS` וגם ברירת המחדל בקוד היו תקינים לגמרי**. **אבחון:** F12 → Console בדפדפן (לא רק לוגי Render!) → לחפש שגיאת CORS. **תיקון:** Render → Deploys → "Deploy latest commit" ידנית כדי לוודא שהקוד העדכני באמת רץ.

---

## 3. קבצי הפרויקט

| קובץ | איפה רץ | תפקיד |
|---|---|---|
| `index.html` | Vercel | הדשבורד — ממשק המשתמש המלא, קובץ יחיד |
| `api_server.py` | Render | FastAPI — מחזיר נתונים מ-Postgres/yfinance/SEC לפרונט |
| `pipeline_a_scanner.py` | GitHub Actions (`stock-scanner.yml`) | סורק טכני — מזהה תבניות, כותב ל-Postgres |
| `pipeline_b_news_aggregator.py` | GitHub Actions (`news-aggregator.yml`) | שולף חדשות RSS, מתרגם, כותב ל-Postgres |
| `requirements.txt` | Render + GitHub Actions | תלויות Python |
| `.github/workflows/*.yml` | GitHub Actions | תזמון סריקה/חדשות/keep-alive |

---

## 4. טכנולוגיות

### Frontend
- **Vanilla HTML/CSS/JS** — קובץ אחד, בלי framework, בלי build process
- **TradingView Widget** — גרפים בתוך המגירה, עמוד "ניתוח מניה"
- **Chart.js 4.5.0** — גרפי פונדמנטלס, סקטורים, equity curve, פילוח תקופתי
- **Treemap מותאם-אישית (vanilla JS, squarified algorithm)** — מפת החום, ראה סעיף 9
- **RTL עברית כברירת מחדל**, toggle לאנגלית
- **אין שימוש ב-localStorage לנתונים עסקיים** (רק מחשבון סיכונים + העדפת שפה)

### Backend
- **FastAPI + Uvicorn**
- **psycopg2-binary** — Postgres
- **yfinance** — נתוני שוק (`.history()` אמין; `.info` לא אמין מ-Render — ראה סעיף 12)
- **SEC EDGAR API ישיר** (`data.sec.gov`) — מקור אמין למכפילים/שווי שוק/EPS כש-`.info` חסום; גם fallback לתיאור חברה
- **Wikipedia REST API** — fallback לתיאור חברה עשיר יותר (חינמי, בלי API key)
- **feedparser** — RSS
- **pandas.read_csv** — S&P 500 + סקטור/תת-סקטור GICS (מקור אמין, לא `.info`)

### Infrastructure
- **Render** — Free Web Service ל-API
- **Neon** — Postgres חינמי לצמיתות, auto-suspend אחרי ~5 דק'
- **Vercel** — הפרונט
- **GitHub** — ציבורי, + Actions למחליף cron

### מה לא בשימוש (ולמה)
- ❌ **OpenAI** — לא בשימוש בשום pipeline
- ❌ **VIX** — הוסר לגמרי, Fear & Greed משמש תחליף
- ❌ **cron-job.org / UptimeRobot / שירותי פינג חיצוניים** — נשקל כמה פעמים, נדחה כל פעם ע"י המשתמש (מעדיף למזער הרשמות/תשלומים)

---

## 5. מסד הנתונים (Postgres, Neon)

### ⚠️ נקודות קריטיות תפעוליות
- **Neon branches**: הנתונים ב-branch `import-2026-08...`, לא ב-`production`/`main`. חשוב לזכור בכל שינוי DB.
- **Neon auto-suspend**: מכבה חישוב אחרי ~5 דק' חוסר פעילות. כל קוד שעושה עבודה ארוכה לפני כתיבה ל-DB **חייב לפתוח חיבור בסמוך לכתיבה**, לא מראש (זו הייתה תקלה אמיתית שתוקנה).

### `scanned_stocks` — תוצאות הסריקה
עמודות עיקריות: `ticker`, `trigger_text_he/en`, `pattern_type`, `swing_score` (0-100), `entry_price`, `support_level`, `resistance_targets` (JSON), `sma50/150/200`, `trend_stage`, `atr_value/pct`, `rs_rating` (1-99), `days_to_earnings`, `forward_return_10d/20d`, `ai_summary_he/en`, `breakout_volume_pct`, `change_pct`, `exchange`, `market_cap`, `avg_volume_20d`, `timestamp`.
`/api/stocks` מחזיר **שורה אחת לכל טיקר** (המופע העדכני, `DISTINCT ON`) + `repeat_count_14d` — לא שורה גולמית לכל ריצת סריקה (זה היה גורם לאותן מניות "תופסות" את כל הרשימה).

### `universe_movers` — heatmap + fallback ל"הכי זזות"
`ticker` (PK), `change_pct`, `close_price`, `in_sp500`, `in_nasdaq100`, **`market_cap`** (עמודה חדשה — נאספת כעת בסריקה עבור **כל** היקום, לא רק טיקרים שסומנו; מתמלאת בהדרגה קדימה, לא רטרואקטיבית), `timestamp`.

### `sec_cik_cache` — טבלה חדשה
שומרת את מיפוי טיקר→CIK של SEC ב-Postgres (לא רק בזיכרון) — כדי ש-cold start של Render לא יצטרך להוריד מחדש קובץ ~1MB מ-SEC בכל פעם שה-container מתעורר.

### `watchlist`
`ticker` (PK), `added_at`.

### משתני סביבה נדרשים
- `DATABASE_URL` — Neon branch `import-2026-08...`
- `ALLOWED_ORIGINS` — `https://swing-desk-tau.vercel.app` (Render Environment). ⚠️ גם אם זה תקין, לוודא שה-deploy עצמו רץ בפועל (ראה סעיף 2).

---

## 6. API Endpoints עיקריים

| Endpoint | תיאור |
|---|---|
| `/api/health` | בדיקת חיות + DB |
| `/api/stocks` | מניות מסומנות — dedup לפי טיקר + `repeat_count_14d` |
| `/api/macro-news`, `/api/macro-news/search` | חדשות מאקרו + חיפוש DB אמיתי |
| `/api/lookup/{ticker}` | נתוני שוק חיים; `market_cap`/`pe_ratio` נופלים ל-fundamentals (SEC) כש-`.info` חסום; `description` נופל ל-Wikipedia ואז SEC |
| `/api/sector-comparison/{ticker}?period=1M\|3M\|YTD\|1Y` | תשואת המניה מול ETF הסקטור **וגם** ETF תת-הסקטור המדויק (SOXX/IGV/וכו') — מוצגים זה לצד זה, בלי עמודות "מול" (המשתמש מחשב הפרש בעצמו); עד 4 מתחרי תת-סקטור עם P/E + שווי שוק + דירוג-זולות; טווח זמן נבחר (לא רק 1Y קבוע) |
| `/api/fundamentals/{ticker}` | דוחות SEC EDGAR — `trailing_pe`/`forward_pe` (LTM/NTM), `market_cap` (תגי `us-gaap` **ו-`dei`** ממוזגים), FCF/Revenue/NI + **`_ltm` (סכום 4 רבעונים) לכל אחד** |
| `/api/heatmap?index=sp500\|nasdaq100` | כולל כעת `market_cap` לכל טיקר (treemap sizing) |
| `/api/backtest?pattern=...&horizon=10\|20` | equity curve, drawdown, פילוח RS, **`monthly_breakdown`/`quarterly_breakdown`** חדשים |
| `/api/backtest/patterns` | רשימת תבניות עם איתותים בשלים |
| `/api/pattern-stats` | win rate + תשואה ממוצעת לכל תבנית — מוצג גם כטבלת השווואה מלאה בעמוד הבדיקה ההיסטורית, לא רק כתג |
| `/api/price-compare` | סדרת מחירים מנורמלת לאחוזים, עד 3 טיקרים, לגרף ההשוואה |
| `/api/signal-history/{ticker}` | היסטוריית איתותים לטיקר, כולל RS Rating/Swing Score עדכניים |
| `/api/market-movers`, `/api/sector-performance`, `/api/fear-greed`, `/api/watchlist`, `/api/stock-news/{ticker}` | ללא שינוי מהותי |

---

## 7. Pipeline A — סורק מניות טכני

### לוח זמנים
`stock-scanner.yml`: 16:40 + 22:50 שעון ישראל בקירוב (UTC לא מתאים אוטומטית לשעון קיץ/חורף — לעדכן ידנית מסביב לסוף אוקטובר/מרץ). GitHub Actions `schedule` **לא מדויק** — סטיות של שעות אפשריות, המשתמש בחר לקבל את זה במקום שירות פינג חיצוני.

### Universe
S&P 500 + Nasdaq 100 מלאים + רשימת תוספות. פילטרים: מחיר > $10, נפח 20 יום > 1.5M, שווי שוק > $1.5B, NYSE/NASDAQ.
**`market_cap` נאסף כעת עבור כל היקום** (לא רק טיקרים שסומנו) — נשמר ב-`universe_movers`, ומזין את ה-heatmap.

### תבניות — 13 גלאים, מחולקים לשתי רמות ציון

**רמה 1 — מבניות (רצפה 65-69, עד 99-100):** Cup & Handle (מלא, או **ללא ידית** — פריצה ישירה מהכוס, טקסט הוגן שמבדיל בין השניים), Double Bottom, פריצת שיא 52 שבועות, **פריצת התנגדות אופקית** (רמה שנבדקה — מגע בודד גם נחשב, ציון עולה עם כמות המגעים), Golden Cross, Bull Flag, משולש עולה, **קפיצה על קו מגמה עולה**, **קפיצה על תמיכה אופקית** (אותו עיקרון — ריבוי מגעים = ציון גבוה יותר).

**רמה 2 — אינדיקטורים גנריים (תקרה נמוכה, מובטח נמוך מרמה 1):** MACD Crossover (≤60), Momentum Surge (≤58), RSI Bounce (≤55).

**הפרדה מובטחת בין הרמות** — לא רק בממוצע: הרצפה של רמה 1 (65-69) גבוהה מהתקרה של רמה 2 (55-60), ללא קשר לבונוס נפח. זו החלטה מפורשת: המשתמש הוא בעיקר אנליסט טכני שמעדיף מבנה גרף מאומת (תמיכה/התנגדות/קו מגמה/כוס) על פני אינדיקטור גנרי.

**דרישת נפח:** חלה רק על 52w-high, Bull Flag, משולש עולה, Double Bottom, ושבירת קו מגמה יורד. **לא חלה** על התבניות המבוססות תמיכה/התנגדות/כוס (החלטה מפורשת — הרמה עצמה נחשבת מספיק חזקה).

**דירוג RS** עדיין מתאים ציון ב-±8/-15 מעל כל זה — ציר נפרד (מנהיגות שוק), לא "רעש" אינדיקטור.

### תיקון קריטי: ניהול חיבור DB (Neon auto-suspend)
`main()` פותח חיבור רק לפני הכתיבה, לא לפני הסריקה, עם retry אוטומטי.

---

## 8. Pipeline B — אגרגטור חדשות
ללא שינוי מהותי מהסבב הקודם. לוח זמנים: כל שעה, ב-17 דק' אחרי השעה (הוחלט אחרי ש-`*/30` התגלה לא אמין ב-GitHub Actions).

---

## 9. Frontend — index.html

### ניווט — 4 טאבים
בית | פעימת השוק | בדיקה היסטורית | ניתוח מניה

### מפת חום — **treemap אמיתי (לא רשת אחידה)**
נבנה מחדש כאלגוריתם squarified treemap (Bruls/Huizing/van Wijk) בג'אווהסקריפט טהור — גודל כל אריח פרופורציוני לשווי השוק (לא כל הטיקרים באותו גודל). גובה קבוע (חצי מהרוחב, 360-640px) כדי שלא "יעמיס על כל המסך" ללא קשר לכמות הטיקרים. מגיב לשינוי גודל חלון (`ResizeObserver`).

### עמוד "בדיקה היסטורית" — הורחב
מלבד equity curve/drawdown/פילוח RS/טבלת איתותים (מהסבב הקודם): **טבלת "השוואת תבניות"** חדשה בראש העמוד — כל התבניות זו לצד זו (win rate, תשואה ממוצעת, n), ממוינת אוטומטית, לחיצה על שורה מסננת אליה. **פילוח חודשי/רבעוני** חדש — גרף עמודות עם טאבים, מראה אם התבנית עובדת טוב יותר בתקופות מסוימות.

### עמוד "ניתוח מניה" (Deep Dive) — שופר משמעותית
- **P/E**: NTM ו-LTM מוצגים **זה לצד זה תמיד** (לא toggle בלחיצה — זה בלבל כשהערך המוחלף היה "לא זמין")
- **מניה מול סקטור**: ETF הסקטור **ו**-ETF תת-הסקטור המדויק (SOXX/IGV/וכו') **באותה שורה**, בלי עמודות הפרש; טאבים 1M/3M/YTD/1Y
- **מתחרי תת-סקטור**: עד 4, עם P/E + שווי שוק + דירוג-זולות ("מדורג Nth מתוך M")
- **פונדמנטלס**: גרפים מתחילים מהנתונים העדכניים (לא מהישנים) — תוקן עם `ResizeObserver` שמתקן מיקום גלילה כל עוד הקונטיינר משנה גודל (במקום השהיה קבועה שניחשה לא נכון)
- **תיאור חברה**: Wikipedia API כמקור ראשון (תקציר אמיתי), SEC (שם + סיווג SIC) כ-fallback
- **השוואת עד 3 מניות**: גרף מחירים מנורמל לאחוזים (טאבי תקופה), שורות RS Rating + Swing Score, לא רק פונדמנטלס

### תיקון אמין: retry על כל קריאות ה-API
כל נקודת fetch (דשבורד ראשי, heatmap, פונדמנטלס, sector-comparison) עברה מקריאה בודדת ל-**retry עם המתנה גדלה** (מטפל ב-cold start של Render/Neon) + במקרים הרלוונטיים fetch **מקביל** (לא סדרתי) כדי לא לחצות timeout.

---

## 10. החלטות ארכיטקטורה מרכזיות

| החלטה | סיבה |
|---|---|
| Neon + Render + GitHub Actions (לא Railway) | Railway היה תקופת ניסיון בתשלום |
| ריפו GitHub ציבורי | דקות Actions ללא הגבלה; הסודות עדיין מוגנים |
| GitHub Actions בתדירות נמוכה | `schedule` לא אמין בתדירות גבוהה — המשתמש קיבל את הפשרה |
| `.history()` ראשי, `.info`/SEC/Wikipedia כ-fallback מדורג | `.info` נחסם לעיתים קרובות מ-IP ענן |
| תבניות מבניות > אינדיקטורים גנריים בציון, מובטח מתמטית | המשתמש הוא בעיקר אנליסט טכני, לא רוצה שווליום/MACD יעקפו תמיכה/התנגדות מאומתת |
| `/api/stocks` מחזיר טיקר ייחודי (לא שורה גולמית לכל ריצה) | מניעת "אותן מניות תופסות את כל הרשימה" |
| חיבור DB נפתח רק ממש לפני כתיבה | Neon auto-suspend |

---

## 11. פיצ'רים שהושלמו ✅ (מצטבר, כולל הסבב הזה)

- מעבר תשתית מלא Railway → Neon+Render+GitHub Actions
- עמודי "בדיקה היסטורית" ו"ניתוח מניה" מלאים
- **טבלת השוואת תבניות + פילוח חודשי/רבעוני** בבדיקה ההיסטורית
- **מפת חום כ-treemap אמיתי** (גודל = שווי שוק)
- **4 תבניות טכניות חדשות**: קפיצה על תמיכה אופקית, פריצת התנגדות אופקית, קאפ-בלי-ידית, + ציון מובטח גבוה יותר לכל התבניות המבניות מול אינדיקטורים גנריים
- **NTM/LTM P/E** זה לצד זה; תוקנו באגי SEC אמיתיים (תג `dei` חסר לשווי שוק, P/E לא השתמש בחישוב SEC הקיים)
- **תיאור חברה מ-Wikipedia** (fallback אמיתי, לא רק סיווג SIC יבש)
- **השוואת סקטור/תת-סקטור** מחדש: ETF תת-סקטור מדויק, טווחי זמן נבחרים, בלי עמודות הפרש
- **השוואת עד 3 מניות** עם גרף מנורמל + RS/Swing Score
- `/api/stocks` דה-דופליקציה לפי טיקר + `repeat_count_14d`
- **תיקון קריטי CORS**: deploy תקוע ב-Render גרם לחסימת דפדפן למרות תשובות 200 בלוגים — תוקן, ותועד לזיהוי מהיר בעתיד
- **תיקון קריטי**: `sec_cik_cache` ב-Postgres — cold start לא מוריד מחדש קובץ SEC כל פעם
- ריבוי תיקוני retry/timeout ברחבי הפרונט (dashboard, heatmap, fundamentals, sector-comparison) לטיפול ב-cold start

---

## 12. מגבלות ידועות ⚠️

| מגבלה | פירוט |
|---|---|
| **`.info` של yfinance לא אמין מ-Render** | חסום/מוגבל מ-IP ענן. פתרון מדורג: `.history()` → SEC EDGAR → Wikipedia (לתיאור) → CSV S&P500 (לסקטור). לא מושלם למניות מחוץ ל-S&P 500. |
| **GitHub Actions `schedule` לא מדויק** | סטיות של שעות אפשריות. נדחה פתרון חיצוני (cron-job.org) ביודעין. |
| **Neon auto-suspend + branches** | כל קוד עתידי חייב לפתוח חיבור DB בסמוך לכתיבה; לזכור ה-branch הנכון. |
| **Render Free נרדם** | `keep-render-awake.yml` לא מובטח 100% (תלוי בדיוק GitHub Actions). **תזכורת:** deploy שנתקע יכול לגרום לתקלה שנראית כמו "עדיין ישן" אבל היא בעצם CORS/קוד ישן — לבדוק Console בדפדפן, לא רק לוגי Render. |
| **NTM P/E** | תלוי בתחזית אנליסטים (`.info` בלבד) — אין מקור SEC/חינמי חלופי. יכול להישאר "לא זמין" למניות רבות; LTM (SEC-based) תמיד אמין יותר. |
| **`market_cap` בהיקום המלא (heatmap)** | מתמלא קדימה מהסריקה הבאה בלבד — לא רטרואקטיבי. מניות ישנות יקבלו גודל ברירת מחדל זמנית בטרימאפ. |
| Backtest/פילוח תקופתי ריקים בהתחלה | דורש 14-28+ יום שהאיתותים "יבשילו" — תקין. |
| VIX | הוסר לגמרי, אין תוכנית לחזור |
| Google Translate חינמי, CNN Fear & Greed לא-רשמי | לא-רשמיים, יכולים להישבר בלי התראה |

---

## 13. TODO עתידי 🔮

- [ ] **טבלת watchlist מלאה** (score/P/E/RS Rating לכל מניות המעקב יחד) — נדחה במפורש, "אולי בהמשך"
- [ ] פילוח הכנסות לפי תחום עסקי — דורש מקור בתשלום
- [ ] Alerts באימייל/webhook
- [ ] מדריך נרות יפניים ל"פעימת השוק"
- [ ] Push notifications לפריצות
- [ ] עתיד רחוק: OpenAI לסיכומים, Reddit/X sentiment, ריבוי משתמשים

---

## 14. איך לפתוח שיחה חדשה

1. צרף קובץ זה (`CLAUDE.md`)
2. צרף את הקוד הרלוונטי — לרוב `index.html` + `api_server.py` + `pipeline_a_scanner.py` (הקבצים שהשתנו הכי הרבה); `pipeline_b_news_aggregator.py` רק אם משנים חדשות
3. **חשוב:** משוך את הגרסאות העדכניות ביותר **מ-GitHub עצמו**, לא קבצים ישנים שכבר יש לך מקומית — כמה סבבי עדכונים קרו באותה שיחה
4. תאר מה אתה רוצה לשנות
5. בסיום שינוי משמעותי: בקש "עדכן את CLAUDE.md"

---

*עדכון אחרון: אוגוסט 2026 | גרסה: MVP v3.1 — תבניות תמיכה/התנגדות + treemap + תיקוני SEC/CORS*
