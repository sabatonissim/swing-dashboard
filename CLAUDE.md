# CLAUDE.md — Swing Desk: Project Knowledge Base

> **מסמך חי.** מעדכנים אותו בסיום כל משימה משמעותית.  
> בכל שיחה חדשה: צרף קובץ זה + הקבצים הרלוונטיים. אין צורך בהיסטוריית צ'אט.

---

## 1. חזון ומטרה

**Swing Desk** הוא דשבורד מסחר פיננסי בזמן אמת, המיועד לטריידרים קצרי-טווח (swing trading). המטרה: לספק בממשק אחד את כל המידע הקריטי לקבלת החלטות מסחר — סריקת מניות טכנית, חדשות מאקרו, וגרפים — בלי לנווט בין עשרות מקורות.

**מודל עסקי עתידי:** אתר ציבורי עם פוטנציאל מוניטיזציה (מנויים, פרמיום).

**כתובת האתר:** https://swing-desk-tau.vercel.app

---

## 2. ארכיטקטורה כללית

```
[GitHub Repository: sabatonissim/swing-dashboard]
         |
         ├── Vercel (Frontend)
         │     └── index.html  ← האתר הציבורי
         │
         └── Railway (Backend)
               ├── swing-dashboard  ← FastAPI API (תמיד דלוק)
               ├── PIPELINE-מניות  ← Cron: סריקה פתיחה+סגירה
               ├── PIPELINE-חדשות  ← Cron: כל חצי שעה
               └── Postgres         ← מסד הנתונים
```

**עיקרון:** שינוי קוד → push ל-GitHub → Railway ו-Vercel מתעדכנים אוטומטית.

---

## 3. קבצי הפרויקט

| קובץ | איפה רץ | תפקיד |
|---|---|---|
| `index.html` | Vercel | הדשבורד — ממשק המשתמש המלא |
| `api_server.py` | Railway (swing-dashboard) | FastAPI — מחזיר נתונים מ-Postgres לפרונט |
| `pipeline_a_scanner.py` | Railway (PIPELINE-מניות) | סורק טכני — מזהה תבניות, כותב ל-Postgres |
| `pipeline_b_news_aggregator.py` | Railway (PIPELINE-חדשות) | שולף חדשות RSS, מתרגם, כותב ל-Postgres |
| `requirements.txt` | Railway (כל השירותים) | תלויות Python |
| `database_schema.sql` | רפרנס בלבד | סכמת ה-DB (הטבלאות נוצרות אוטומטית בקוד) |

---

## 4. טכנולוגיות

### Frontend
- **Vanilla HTML/CSS/JS** — קובץ אחד, בלי framework
- **TradingView Widget** (חינמי) — גרפים בתוך המגירה וגרפי מדדים
- **Chart.js** (CDN, חינמי) — גרפי הפונדמנטלס במגירת מניה
- **RTL עברית כברירת מחדל**, עם toggle לאנגלית

### Backend
- **FastAPI + Uvicorn** — Python API
- **psycopg2** — חיבור ל-Postgres
- **yfinance** — נתוני שוק בזמן אמת (חינמי, ללא API key)
- **feedparser** — שליפת RSS
- **urllib (built-in)** — תרגום ל-Google Translate ללא API key

### Infrastructure
- **Railway** — API + Cron Jobs + Postgres
- **Vercel** — אחסון הפרונט (חינמי)
- **GitHub** — source control + trigger לdeploy אוטומטי

### מה לא בשימוש (ולמה)
- ❌ **OpenAI** — הוחלף בתרגום חינמי. הקרדיט נגמר ולא רוצים לשלם כרגע
- ❌ **SQLite** — הוחלף ב-Postgres כי Railway לא שומר קבצים מקומיים
- ❌ **deep-translator** — הוחלף ב-urllib ישיר (יותר אמין ב-Railway)
- ❌ **Node.js / CLI** — לא הותקן, כל העלאה היא דרך GitHub web UI

---

## 5. מסד הנתונים (Postgres ב-Railway)

### טבלאות

**`scanned_stocks`** — תוצאות הסריקה
- עמודות מפתח: `ticker`, `trigger_text_he/en`, `swing_score` (0-100), `entry_price`, `support_level`, `resistance_targets` (JSON), `ai_summary_he/en`, `breakout_volume_pct`, `change_pct` (שינוי יומי % של המניה המסומנת, לא קשור לרצועת "הכי זזות" — ראה סעיף 9), `timestamp`

**`macro_news`** — חדשות מאקרו
- עמודות מפתח: `category_tag`, `summary_he`, `summary_en`, `impact_level` (High/Critical), `source_url`, `timestamp`

**`ui_strings`** — מילון תרגומים (לא בשימוש פעיל)

**`analytics_events`** — מעקב קליקים
- עמודות: `event_type` (view_stock/view_macro/search_stock/page_view), `entity_id`, `session_id`, `timestamp`

### משתני סביבה ב-Railway
- `SWING_DB_PATH` = כתובת Postgres המלאה (`postgresql://...`)
- `OPENAI_API_KEY` = קיים אבל לא בשימוש כרגע

---

## 6. API Endpoints

Base URL: `https://swing-dashboard-production-438f.up.railway.app`

| Endpoint | Method | תיאור |
|---|---|---|
| `/api/health` | GET | בדיקת חיות + חיבור DB |
| `/api/stocks` | GET | מניות מסומנות מה-DB |
| `/api/macro-news` | GET | חדשות מאקרו מה-DB |
| `/api/lookup/{ticker}` | GET | נתוני שוק חיים (yfinance) |
| `/api/market-movers` | GET | הכי זזות היום — כל השוק האמריקאי (yf.screen day_gainers/day_losers), cache 3 דק', עם fallback ל-`universe_movers` (טבלה שהסורק מעדכן מה-90 טיקרים שלו) אם ה-screener של יאהו חסום/נכשל |
| `/api/fundamentals/{ticker}` | GET | דוחות רבעוניים (Revenue/NI/FCF/שולי רווח/חוב נטו/מניות במחזור) מ-**SEC EDGAR** (חינמי, ממשלתי, לא חסום), EPS מדולל + רצועת P/E היסטורית (מחיר מ-yfinance `.history()`), timeout 20 שנ' |
| `/api/stock-news/{ticker}` | GET | חדשות ספציפיות לטיקר (yfinance) |
| `/api/track` | POST | רישום event לאנליטיקס |
| `/api/analytics/top-interest` | GET | אילו טיקרים/נושאים הכי נצפו |
| `/api/analytics/summary` | GET | סיכום יומי של events |

---

## 7. Pipeline A — סורק מניות טכני

### לוח זמנים
- `30 13 * * 1-5` — פתיחת שוק (16:30 ישראל)
- `40 19 * * 1-5` — 20 דק' לפני סגירה (22:40 ישראל)

### Universe
~35 מניות: תוכנה, שבבים, פינטק, AI, אנרגיה + ETFs (QQQ, SPY, ARKK)

### פילטרי כניסה
- מחיר > $10 | נפח 20 יום > 1.5M | שווי שוק > $1.5B | NYSE/NASDAQ בלבד

### תבניות זיהוי (לפי עדיפות)
1. **Cup & Handle** (80+) — ירידה מעוגלת + ידית + פריצה
2. **Double Bottom (W)** (79+) — שתי תחתיות דומות + פריצת קו הצוואר
3. **52-Week High Breakout** (78+) — פריצת שיא שנתי
4. **Golden Cross** (74+) — ממוצע 50 יום חוצה מעל ממוצע 200 יום
5. **Bull Flag** (72+) — עמוד 8%+ + דגל צר + פריצה
6. **Ascending Triangle** (68+) — התנגדות שטוחה + תמיכות עולות
7. **MACD Bullish Crossover** (64+) — קו MACD חוצה מעל קו האיתות
8. **Momentum Surge** (62+) — +5% ב-5 ימים + נפח עולה
9. **RSI Oversold Bounce** (58+) — התאוששות מ-RSI מתחת ל-30
10. **Descending Trendline Break** (55+) — פריצת קו מגמה יורד

### Universe
הורחב מ-~48 ל-~90 טיקרים (הוספת פיננסים, תעשייה, הגנה, קמעונאות, נדל"ן, תעופה ועוד).

### ציון
מבוסס על עוצמת הנפח (לא OpenAI). `min(100, base_score + vol_pct/5)`

---

## 8. Pipeline B — אגרגטור חדשות

### לוח זמנים
`*/30 * * * 1-5` — כל חצי שעה בימי חול

### מקורות RSS
Reuters Top/Business, CNBC Top/Markets, Yahoo Finance, Seeking Alpha, MarketWatch, Investing.com

### סינון רלוונטיות (עודכן)
מטריצת מילות מפתח דו-שכבתית ב-`matches_high_impact`:
- **Tier 1** (תמיד רלוונטי): CPI/PPI/Fed/ריבית/אינפלציה/תעסוקה/GDP/מיתון/גאופוליטיקה/שוק/דוחות/M&A/סחורות/קריפטו
- **Tier 2** (מילים עמומות — AI, Tech, Chip, שמות חברות): נספרות רק אם יש **גם** מילת הקשר שוק בכותרת (stock/shares/market/nasdaq/earnings/rally/%...). מונע התאמות לא רלוונטיות (למשל חדשות פרס נובל שמזכירות "AI" בלי הקשר שוק).
- כל ההתאמות עם `\b` (word boundary) — מונע התאמת substring שגויה (למשל "Meta" בתוך "metabolic").
- כשיש מספר התאמות, נבחרת זו המוקדמת ביותר בכותרת (ולא לפי סדר הרשימה) — כדי שה-tag ישקף את מה שהכותרת באמת עוסקת בו.

### תרגום
`urllib` ישיר ל-Google Translate API הציבורי — חינמי, ללא API key. `sl=en` קבוע (לא auto).
- **מילון מונחים קבוע** (`TERM_GLOSSARY`): מונחי מאקרו נפוצים (Jobless Claims, Non-Farm Payrolls, Fed, Rate Hike/Cut, CPI/PPI/GDP וכו') מוחלפים ב-placeholder לפני התרגום ומוחזרים אחרי עם תרגום עברי קבוע ונכון — כדי לא להסתמך על ניחוש התלוי-הקשר של Google (למשל "jobless" שתורגם לא עקבי).
- `summary_he` = תרגום עברי
- `summary_en` = כותרת מקורית באנגלית

---

## 9. Frontend — index.html

### מבנה הדף
1. **Header** — לוגו, חיפוש (דסקטופ), כפתור שפה EN/עב, שעון
2. **Mobile Search Bar** — מופיע רק מתחת ל-900px
3. **Breaking News Bar** — חדשות קריטיות בלבד (CPI/ריבית/Fed/גאופוליטי/תעסוקה)
4. **Market Charts + Movers strip** — SPY/QQQ/VIX/IWM/Bitcoin (בעמודה, ~62% רוחב) לצד רצועת "הכי זזות היום בשוק" (~38% רוחב). שולפת "day_gainers"/"day_losers" האמיתיים של כל השוק האמריקאי דרך `yf.screen()` (Yahoo, חינמי), עם cache בזיכרון ל-3 דקות. **תוקן 22/7:** תוצאה חלקית/מוחלשת (רק אחד מבין שני ה-screeners הצליח) כבר לא דורסת cache טוב קודם — סף איכות מינימלי (5+ תוצאות) לפני עדכון ה-cache. **תוקן 25/7:** (1) תוקנה כותרת מטעה שעדיין אמרה "מהסריקה" למרות שזה כבר כל השוק. (2) נוסף **fallback אמין**: אם `yf.screen()` נכשל/חסום לגמרי (סביר שיאהו חוסם endpoint זה מ-Railway, כמו עם ה-fundamentals — endpoint שונה מ-`.history()` שכן עובד), הסורק (`pipeline_a_scanner.py`) שומר `change_pct` לכל טיקר בעולם המעקב שלו (לא רק המסומנים) בטבלת `universe_movers`, וה-API נופל אליה כמוצא אחרון — כיסוי צר יותר (90 טיקרים במקום כל השוק) אבל אמין, כי הוא משתמש ב-endpoint שכבר הוכח כעובד.
5. **Split Board:** ימין=מניות (מחולק ל"סריקה אחרונה" ו"ימים קודמים", דה-דופ לפי טיקר, שולף עד 60 שורות), שמאל=חדשות+פילטר סקטורים
6. **Drawer** — מגירה צדדית: מניה (גרף+נתונים+חדשות) / חדשות (תקציר+סקטורים)

### שפה
- ברירת מחדל: עברית RTL
- Toggle שומר ב-localStorage
- כל נתוני ה-API מגיעים עם שדות `_he` ו-`_en`
- נתוני דמו דו-לשוניים

### API Connection
```javascript
const API_BASE = "https://swing-dashboard-production-438f.up.railway.app";
```
- Polling כל 20 שניות
- Fallback לנתוני דמו אם API לא זמין
- Status indicator בפינה שמאל-תחתית

---

## 10. החלטות ארכיטקטורה מרכזיות

| החלטה | סיבה |
|---|---|
| Vanilla JS (לא React) | פשטות, קובץ אחד, אין build process |
| Postgres (לא SQLite) | Railway לא שומר קבצים מקומיים בין deployments |
| yfinance (לא API בתשלום) | חינמי, מספיק ל-MVP |
| urllib לתרגום | deep-translator נכשל ב-Railway, urllib built-in ואמין |
| Vercel לפרונט | חינמי לחלוטין לקבצים סטטיים |
| GitHub כ-source of truth | כל push מעדכן Railway ו-Vercel אוטומטית |
| ללא OpenAI | עלות. ניתן לחזור כשיש הכנסה |

---

## 11. פיצ'רים שהושלמו ✅

- [x] דשבורד split-screen (מניות + חדשות)
- [x] סורק טכני עם 10 תבניות מוכחות (Cup&Handle, Double Bottom, Bull Flag, Ascending Triangle, 52W High, Golden Cross, MACD Crossover, Momentum, RSI Bounce, Trendline)
- [x] Pipeline חדשות עם RSS מ-8 מקורות, סינון דו-שכבתי (Tier1/Tier2+market-context) ותרגום עברית עם מילון מונחים קבוע
- [x] גרף TradingView בלחיצה על מניה
- [x] חיפוש חופשי לכל טיקר (real-time)
- [x] חדשות ספציפיות לטיקר בתוך המגירה
- [x] Breaking News Bar (חדשות קריטיות למעלה)
- [x] גרפי מדדים עם החלפה (SPY/QQQ/VIX/IWM/Bitcoin)
- [x] פילטר סקטורים בעמודת החדשות
- [x] Toggle שפה עברית/אנגלית (כולל דמו דו-לשוני)
- [x] חיפוש במובייל (בר נפרד)
- [x] אנליטיקס קליקים
- [x] Disclaimer פיננסי
- [x] פריסה חיה: Railway (API) + Vercel (Frontend)
- [x] Cron Jobs אוטומטיים
- [x] אכיפת אישור נפח אמיתי (volume confirmation) לתבניות פריצה — קבוע היה קיים אך לא נאכף בפועל
- [x] מגירת מניה: סיבת ההתאמה מוצגת מיד מתחת לגרף
- [x] דשבורד פונדמנטלי במגירת מניה (חיפוש + סריקה): Revenue/Net Income/FCF, Net Debt, שולי רווח גולמי, מניות במחזור, EPS מדולל, רצועת P/E היסטורית מול ממוצע±סטיית תקן — גרפים עם Chart.js (CDN), גלילה אופקית לכל הרבעונים הזמינים. **עודכן 25/7 — מקור הנתונים הוחלף ל-SEC EDGAR:** התברר ש-yfinance's `.info`/`quarterly_financials`/`get_earnings_dates()` פונים ל-endpoint של יאהו (quoteSummary) שנחסם/מוגבל מ-Railway — אותה בעיה בדיוק כמו ב-market movers. במקום זה: כל נתוני הדוחות (Revenue/NI/Gross Profit/EPS/Cash Flow/חוב/מניות) נשלפים עכשיו מ-**SEC EDGAR XBRL companyfacts API** (`data.sec.gov`) — API רשמי, חינמי, ממשלתי, לא דורש מפתח, ולא כפוף לאותה חסימה. ממפה טיקר ל-CIK דרך `company_tickers.json` של ה-SEC (cache 24 שעות), ומסנן נכון בין נתונים רבעוניים (10-Q) לשנתיים מצטברים (10-K) לפי אורך התקופה (~90 יום). רצועת ה-P/E עדיין משתמשת ב-yfinance `.history()` (ה-endpoint שכן עובד באמינות) לצמוד למחיר, בשילוב EPS מ-SEC. תחזיות אנליסטים (EPS vs Estimate) אין ב-SEC — מנסה yfinance כבונוס best-effort, ואם זה לא זמין, מציג את ה-EPS המדווח בפועל בלבד (מ-SEC) במקום להסתיר את כל הסקשן. ⚠️ **User-Agent ל-SEC:** `SEC_HEADERS` ב-`api_server.py` הוא string גנרי מומצא (לא מייל אמיתי של אף אחד) — זה מספיק לפי מדיניות ה-SEC (הם לא מאמתים את הכתובת), **אין צורך להכניס מייל אישי**. **תוקן 26/7 — שני באגים אמיתיים שנמצאו מלוגים בפועל:** (1) `merge_asof` ברצועת ה-P/E נכשל עם `incompatible merge keys ... dtype('<M8[s]') and dtype('<M8[us]')` — pandas מנתח תאריכים ברזולוציות שונות (שניות/מיקרו/ננו) תלוי במקור, ו-merge_asof דורש dtype זהה בדיוק. תוקן עם `.astype("datetime64[ns]")` מפורש על שני הצדדים. (2) נוסף sanitizer (`_json_safe`) שמחליף כל NaN/Infinity ב-None לפני החזרת ה-JSON — Python מקודד NaN/Infinity כ-tokens לא-תקניים שה-JSON spec לא מכיר, ו-`fetch().json()` בדפדפן זורק שגיאה על זה (בדיוק כמו "לא נטען" למרות 200 OK מהשרת). **תוקן 26/7 (המשך) — הבאג האמיתי היה ב-frontend:** גם אחרי שהשרת החזיר 200 OK תקין, הדשבורד עדיין הראה "לא ניתן לטעון". הסתבר ש-`renderFundamentals(data)` נקרא בתוך אותו try/catch של ה-fetch, כך ששגיאת JS בזמן רינדור (למשל בבניית גרף) נבלעה בשקט והוצגה כ"לא ניתן לטעון" הכללי בלי שום עקבות ל-debug. תוקן: fetch/parse ורינדור מופרדים לשני try/catch עם `console.error`, כל בניית גרף עטופה ב-`safeChart()` כך שגרף בודד שנכשל לא מפיל את כל הסקשן, וגרסת ה-CDN של Chart.js הוחלפה ל-4.5.0 (מאומתת בפועל שקיימת ב-cdnjs).

---

## 12. פיצ'רים חלקיים / בעיות ידועות ⚠️

| בעיה | פירוט | פתרון מוצע |
|---|---|---|
| VIX לא עובד | ניסינו CBOE:VIX, TVC:VIX, INDEX:VIX — כולם נכשלים ב-widget חינמי | להחליף ל-UVXY (ETF) |
| פילטר סקטורים | סקטור נלקח מ-category_tag, לא שדה ייעודי | להוסיף שדה sector ל-Pipeline B |
| DB ריק בהתחלה | עד הסריקה הראשונה בשעות מסחר — מוצגים נתוני דמו | תקין, ידוע |

---

## 13. TODO עתידי 🔮

### עדיפות גבוהה
- [ ] החלף VIX ב-UVXY
- [ ] הוסף שדה `sector` אמיתי ל-macro_news
- [ ] הרחב Universe ל-100+ מניות

### עדיפות בינונית
- [ ] עמוד Deep Dive מלא לכל מניה
- [ ] Push notifications לפריצות
- [ ] Fear & Greed Index
- [ ] חיפוש חדשות

### עתיד רחוק
- [ ] OpenAI לסיכומים חכמים (כשיש הכנסה)
- [ ] Reddit/X social sentiment
- [ ] גרסת פרמיום + משתמשים + Watchlist

---

## 14. מגבלות ידועות

| מגבלה | פירוט |
|---|---|
| yfinance rate limit | בסריקת 35+ מניות לפעמים timeouts — מניות שנכשלות מדולגות. **תוקן 22/7:** כל בקשת yfinance רצה עכשיו עם timeout קשיח (15 שנ') + השהיה של 0.3 שנ' בין טיקרים. **תוקן 24/7:** נוסף circuit breaker — אחרי 8 timeouts רצופים הסריקה נעצרת מוקדם (במקום להמשיך לצבור threads נטושים שיכולים לגרום ל-OOM ולקריסה שקטה בלי traceback, שזו כנראה הסיבה לקריסות החוזרות דווקא בערב) |
| Google Translate חינמי | לא רשמי, יכול להיחסם — fallback: כותרת באנגלית |
| Railway free tier | $5 קרדיט ראשוני, אח"כ ~$5-20/חודש |
| CORS | מוגדר ל-swing-desk-tau.vercel.app בלבד — לעדכן ב-api_server.py אם כתובת משתנה |
| טיקר SQ | Block Inc שינתה טיקר מ-SQ ל-XYZ ב-2025 — עודכן ב-Universe |
| numpy → psycopg2 | **תוקן 24/7:** `check_breakout_volume()` החזיר `numpy.float64` (לא `float` רגיל) כי `round()` על ערך numpy מחזיר numpy. psycopg2 לא יודע להתאים טיפוס numpy כפרמטר, ונופל בחזרה על `repr()` שלו — למשל `np.float64(76.0)` — ומכניס את זה כטקסט גולמי ל-SQL, מה שגרם ל-`psycopg2.errors.InvalidSchemaName: schema "np" does not exist` וקריסת כל ריצת הסריקה. תוקן במקור (`float()` מפורש) + נוספה פונקציית הגנה `_native()` שממירה כל טיפוס numpy לטיפוס Python רגיל לפני שהוא מגיע ל-`upsert_scan_result`, כך שאותה בעיה לא תחזור משדה אחר בעתיד |

---

## 15. איך לפתוח שיחה חדשה

1. צרף קובץ זה (`CLAUDE.md`)
2. צרף את הקוד הרלוונטי (`index.html` / `pipeline_a_scanner.py` וכו')
3. תאר מה אתה רוצה לשנות
4. Claude ימשיך מיד ללא היסטוריית שיחה

**לעדכון הקובץ:** בקש "עדכן את CLAUDE.md" בסיום כל שינוי משמעותי.

---

**תוקן 27/7 — צפיפות הנתונים (הבעיה החשובה ביותר):** תרשימי Revenue/NI/FCF הראו הרבה רבעונים ריקים. הסיבה: הרבה חברות מדווחות סעיפי תזרים מזומנים (ולעיתים גם רווח) כ**מצטבר משוקלל מתחילת השנה (YTD)** ברבעונים אמצעיים, לא כרבעון בודד נקי — הפילטר הקודם (75-105 יום) פסל את כל אלה והשאיר חור. עכשיו `_sec_quarterly_series` **גם גוזר** רבעונים חסרים מהפרשי YTD עוקבים (לדוגמה Q4 = סך שנתי מלא פחות 9 חודשים מצטברים) — בדיוק כמו שכלים מקצועיים עושים. נבדק עם סימולציה מלאה (כולל שנתיים רצופות ורבעון בודד אמיתי) — עובד נכון.

**תוקן 27/7 — מיקום במגירה:** מגירת החיפוש החופשי (`showTickerDrawer`) הוזזה כך שהנתונים הפונדמנטליים מופיעים **מיד אחרי הגרף** (לפני הגייג' של המחיר ולפני נתוני השוק), בדיוק כמו במגירת הסריקה — כדי שההתנהגות תהיה עקבית בשני המקומות.

⚠️ **המקרא (legend) וההיררכיה בתרשימים** כבר מיושמים בקוד כ-HTML קבוע מחוץ לאזור הגלילה (לא legend מובנה של Chart.js) — אמור להיות תמיד גלוי בלי גלילה. אם זה עדיין נראה שבור אחרי ההעלאה, כדאי לוודא ריענון קשיח בדפדפן / שה-deploy ב-Vercel/Railway באמת החליף את הקובץ הישן.

**תוקן 28/7 — "הנתונים נטענו אך הצגתם נכשלה" בכל מניה:** נמצא הבאג המדויק — `Chart.defaults.color = ...` רץ **בלי הגנה** מיד אחרי בניית ה-HTML, ואם ספריית Chart.js לא נטענה (חסימת סקריפט, חוסם פרסומות, בעיית רשת ל-cdnjs.cloudflare.com) — זה קורס בדיוק באותה שורה בכל מניה, כי זו נקודת הכשל הראשונה שמשתמשת ב-`Chart`. תוקן: (1) נוספה בדיקה מפורשת שה-`Chart` בכלל קיים לפני שמשתמשים בו, עם הודעה ברורה למשתמש אם לא. (2) נוסף **CDN גיבוי**: אם cdnjs נכשל לטעון את Chart.js, נטען אוטומטית מ-jsdelivr במקום. אם זה עדיין נכשל אחרי זה — זה כמעט בטוח בעיית רשת/חסימה אצל המשתמש הספציפי, לא באג בקוד.

---

**תוקן 28/7 — קונסול הראה `Cannot read properties of undefined (reading 'length')` בכל מניה:** ה-error המדויק מהקונסול (`renderFundamentals`, בשורה עם `data.quarter_labels.length`) הראה ש-`quarter_labels` הגיע `undefined` למרות `has_fundamentals:true`. הסיבה: יש **cache בזיכרון של 15 דקות** (`_fundamentals_cache`, מפתח=טיקר) בשרת — טיקר ספציפי (כמו SHOP) יכול להישאר "תקוע" עם תשובה ישנה/פגומה מגרסת קוד קודמת עד שה-cache פג, גם אם שאר הקוד כבר תוקן. תוקן משני הכיוונים: (1) **frontend** — `renderFundamentals` בודק עכשיו במפורש ש-`quarter_labels` הוא מערך תקין לפני שהוא נוגע בו, ואם לא — מציג "אין נתונים" במקום לקרוס. (2) **backend** — נוספה `_is_valid_fundamentals_shape()` שבודקת את המבנה של כל רשומת cache לפני שמגישים אותה; רשומה פגומה נחשבת אוטומטית כ-cache miss ומחושבת מחדש, כך שהיא לא יכולה "להיתקע" לכל משך ה-TTL.

**תוקן 28/7 (המשך) — WMT (מניה רגילה לגמרי) הראה "אין נתונים", בלי אזהרה בלוג:** התגלה פער אבחוני אמיתי — `_sec_company_facts` היה **בולע בשקט** את קוד השגיאה בפועל מ-SEC (403/429/5xx וכו') בלי לתעד אותו בכלל, אז אי אפשר היה לדעת אם SEC חסם/הגביל את הבקשה או שבאמת אין נתונים. תוקן: (1) קוד הסטטוס בפועל מתועד עכשיו תמיד בלוג. (2) נוספו **3 ניסיונות עם המתנה גוברת** (backoff) גם ל-`_get_cik_map` וגם ל-`_sec_company_facts`, למקרה של כשל זמני/rate-limit מצד SEC — חוץ מ-404 אמיתי (טיקר לא קיים) שלא כדאי לנסות שוב. נבדק עם סימולציה (403,403,200→מצליח בניסיון 3; 404→לא מנסה שוב).

*עדכון אחרון: יולי 2026 | גרסה: MVP v1.2*

---

**תוקן 28/7 (המשך) — WMT תקוע על "אין נתונים" בלי אף שורת אזהרה בלוג:** אין `[warn]` בלוג = `_build_fundamentals` בכלל לא רץ הפעם — כלומר זה הוגש מה-**cache**, לא מחישוב טרי. הבעיה: cache "אין נתונים" (מכשל חד-פעמי/זמני שקרה מתישהו באמצע הבדיקות שלנו) נחשב "תקין מבנית" ולכן הוגש כמות שהוא למשך כל 15 הדקות, גם אחרי שהבעיה שגרמה לו כבר תוקנה. תוקן: לתוצאה שלילית ("אין נתונים") יש עכשיו TTL הרבה יותר קצר (45 שניות בלבד) לעומת תוצאה מוצלחת (15 דקות) — כך שאם זו הייתה תקלה חולפת, הניסיון הבא (אחרי חצי דקה) יחשב את זה מחדש באמת במקום להישאר תקוע.
