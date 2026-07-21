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
- עמודות מפתח: `ticker`, `trigger_text_he/en`, `swing_score` (0-100), `entry_price`, `support_level`, `resistance_targets` (JSON), `ai_summary_he/en`, `breakout_volume_pct`, `timestamp`

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
2. **52-Week High Breakout** (78+) — פריצת שיא שנתי
3. **Bull Flag** (72+) — עמוד 8%+ + דגל צר + פריצה
4. **Ascending Triangle** (68+) — התנגדות שטוחה + תמיכות עולות
5. **Momentum Surge** (62+) — +5% ב-5 ימים + נפח עולה
6. **Descending Trendline Break** (55+) — פריצת קו מגמה יורד

### ציון
מבוסס על עוצמת הנפח (לא OpenAI). `min(100, base_score + vol_pct/5)`

---

## 8. Pipeline B — אגרגטור חדשות

### לוח זמנים
`*/30 * * * 1-5` — כל חצי שעה בימי חול

### מקורות RSS
Reuters Top/Business, CNBC Top/Markets, Yahoo Finance, Seeking Alpha, MarketWatch, Investing.com

### תרגום
`urllib` ישיר ל-Google Translate API הציבורי — חינמי, ללא API key.
- `summary_he` = תרגום עברי
- `summary_en` = כותרת מקורית באנגלית

---

## 9. Frontend — index.html

### מבנה הדף
1. **Header** — לוגו, חיפוש (דסקטופ), כפתור שפה EN/עב, שעון
2. **Mobile Search Bar** — מופיע רק מתחת ל-900px
3. **Breaking News Bar** — חדשות קריטיות בלבד (CPI/ריבית/Fed/גאופוליטי/תעסוקה)
4. **Market Charts** — SPY/QQQ/VIX/IWM/Bitcoin, החלפה בלחיצה
5. **Split Board:** ימין=מניות, שמאל=חדשות+פילטר סקטורים
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
- [x] סורק טכני עם 6 תבניות מוכחות (Cup&Handle, Bull Flag, Ascending Triangle, 52W High, Momentum, Trendline)
- [x] Pipeline חדשות עם RSS מ-8 מקורות + תרגום עברית
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
| yfinance rate limit | בסריקת 35+ מניות לפעמים timeouts — מניות שנכשלות מדולגות |
| Google Translate חינמי | לא רשמי, יכול להיחסם — fallback: כותרת באנגלית |
| Railway free tier | $5 קרדיט ראשוני, אח"כ ~$5-20/חודש |
| CORS | מוגדר ל-swing-desk-tau.vercel.app בלבד — לעדכן ב-api_server.py אם כתובת משתנה |

---

## 15. איך לפתוח שיחה חדשה

1. צרף קובץ זה (`CLAUDE.md`)
2. צרף את הקוד הרלוונטי (`index.html` / `pipeline_a_scanner.py` וכו')
3. תאר מה אתה רוצה לשנות
4. Claude ימשיך מיד ללא היסטוריית שיחה

**לעדכון הקובץ:** בקש "עדכן את CLAUDE.md" בסיום כל שינוי משמעותי.

---

*עדכון אחרון: יולי 2026 | גרסה: MVP v1.2*
