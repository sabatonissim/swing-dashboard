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
| `/api/market-movers` | GET | הכי זזות היום — כל השוק האמריקאי (yf.screen day_gainers/day_losers), cache 3 דק' |
| `/api/fundamentals/{ticker}` | GET | דוחות רבעוניים, EPS מול תחזית, רצועת P/E היסטורית (yfinance, timeout 20 שנ') |
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
4. **Market Charts + Movers strip** — SPY/QQQ/VIX/IWM/Bitcoin (בעמודה, ~62% רוחב) לצד רצועת "הכי זזות היום" (~38% רוחב). **עודכן:** הרצועה כבר לא מוגבלת ל-90 הטיקרים שהסורק עוקב אחריהם — שולפת עכשיו את "day_gainers"/"day_losers" האמיתיים של כל השוק האמריקאי דרך `yf.screen()` (Yahoo, חינמי), עם cache בזיכרון ל-3 דקות כדי לא להעמיס על Yahoo. **תוקן 22/7:** תוצאה חלקית/מוחלשת (למשל אם רק אחד מבין שני ה-screeners הצליח) כבר לא דורסת cache טוב קודם — יש סף איכות מינימלי (5+ תוצאות) לפני שמעדכנים את ה-cache, כדי למנוע את התופעה של "מראה כמה מניות ואז קופץ למניה אחת".
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
- [x] דשבורד פונדמנטלי במגירת מניה (חיפוש + סריקה): Revenue/Net Income/FCF, Net Debt, שולי רווח גולמי, מניות במחזור, EPS בפועל מול תחזית, רצועת P/E היסטורית מול ממוצע±סטיית תקן — הכל מ-yfinance חינמי, גרפים עם Chart.js (CDN), גלילה אופקית לכל הרבעונים הזמינים

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
| yfinance rate limit | בסריקת 35+ מניות לפעמים timeouts — מניות שנכשלות מדולגות. **תוקן 22/7:** כל בקשת yfinance רצה עכשיו עם timeout קשיח (15 שנ') + השהיה של 0.3 שנ' בין טיקרים, כדי שקריאה תקועה לא תקריס את כל הקונטיינר (זו הייתה סיבת ה-Crash של הסריקה בערב) |
| Google Translate חינמי | לא רשמי, יכול להיחסם — fallback: כותרת באנגלית |
| Railway free tier | $5 קרדיט ראשוני, אח"כ ~$5-20/חודש |
| CORS | מוגדר ל-swing-desk-tau.vercel.app בלבד — לעדכן ב-api_server.py אם כתובת משתנה |
| טיקר SQ | Block Inc שינתה טיקר מ-SQ ל-XYZ ב-2025 — עודכן ב-Universe |

---

## 15. איך לפתוח שיחה חדשה

1. צרף קובץ זה (`CLAUDE.md`)
2. צרף את הקוד הרלוונטי (`index.html` / `pipeline_a_scanner.py` וכו')
3. תאר מה אתה רוצה לשנות
4. Claude ימשיך מיד ללא היסטוריית שיחה

**לעדכון הקובץ:** בקש "עדכן את CLAUDE.md" בסיום כל שינוי משמעותי.

---

*עדכון אחרון: יולי 2026 | גרסה: MVP v1.2*
