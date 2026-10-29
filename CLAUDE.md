# CLAUDE.md — Swing Desk (תקציר עבודה)

> **מסמך חי ומקוצר** (נטען בכל שיחה — לשמור קצר, ~25 אלף תווים לכל היותר). הפירוט המלא לא נמחק, הוא ב:
> - **`docs/REFERENCE.md`** — ארכיטקטורה מלאה, קבצים, מסד נתונים, כל ה-API, הסורק (תבניות, יומן דוחות), החדשות, הפרונט, טבלת החלטות, מגבלות, TODO, תכנון לוח האירועים.
> - **`docs/HISTORY.md`** — כל סבב/גרסה (v5–v18): מה נמצא, מה תוקן, איך נבדק, לקחים. לחפש שם (Grep) לפני שנוגעים באזור שכבר טופל.
> **כלל:** פרט חדש → לקובץ ה-docs המתאים; כאן רק מה שצריך בכל שיחה. ⚠️ **הריפו ציבורי — בלי סודות** (`JWT_SECRET`, `DATABASE_URL`, טוקנים).

---

## 0. כללי עבודה עם המשתמש (חובה)
- המשתמש **לא מפתח**. לכתוב לו **תמיד בעברית פשוטה**, עם צעדים של "ללחוץ על כך וכך".
- התיקייה המקומית `C:\Users\sabat\נסים\דשבורד` **לא מחוברת ל-Git** — המשתמש מעלה ל-GitHub ידנית. **בסוף כל שינוי: רשימת קבצים להעלאה + סדר + מה לבדוק באתר.** קבצי workflow נמצאים מקומית ב-`WORKFLOW\` ונכנסים ב-GitHub ל-`.github/workflows/`. `ארכיון\` = ישן, לא בשימוש.
- **לא לפרוס** ל-Render/Vercel (המשתמש עושה). **לפני שינוי גדול — להסביר ולקבל אישור.**
- בלי הרשמה לשירותים חדשים ובלי תשלום; בלי מפתחות/סיסמאות בצ'אט.
- תוכן האתר **עובדתי ואנליטי, בלי המלצות קנייה/מכירה**.
- **בסוף כל סבב — לעדכן את CLAUDE.md** (תקציר) ואת `docs/HISTORY.md` (פירוט).
- **אין Python במחשב** (גם לא node). בדיקות: PowerShell (גישה לרשת, SEC, Yahoo מהבית עובד), הדפדפן המובנה על האתר החי (הזרקת JS לבדיקה, בלי לשנות את האתר), והעתקים של לוגיקה ב-PowerShell/JS על נתונים אמיתיים. לקרוא שוב כל שינוי פייתון בעיון — אין קומפיילר.

## 1. מה זה ואיפה
Swing Desk — דשבורד מסחר סווינג (סורק תבניות טכניות, חדשות מאקרו + סיכום AI, לוח אירועים כלכליים, יומן דוחות, ניתוח מניה, תיק דמו, התחברות Google). שוק אמריקאי.
- אתר: https://swing-desk-tau.vercel.app (Vercel, **נפרס אוטומטית מ-`main`** בגיטהאב). API: https://swing-dashboard-3btx.onrender.com (Render חינמי, נפרס אוטומטית מ-push).
- DB: Neon Postgres, branch **`live`** (לא `production`/`old-production`).
- משימות רקע: GitHub Actions (ריפו ציבורי `sabatonissim/swing-dashboard` → דקות ללא הגבלה), מופעלות ע"י **`scheduler.py` בתוך השרת** (אומת עובד 26.10: `/api/scheduler/status`) — לא ע"י `schedule` של GitHub (מאחר שעות).

## 2. קבצים (כולם בשורש הריפו)
| קובץ | רץ ב | תפקיד |
|---|---|---|
| `index.html` | Vercel | כל הפרונט (קובץ יחיד, vanilla JS) |
| `api_server.py` | Render | FastAPI. **מכיל עותק "תאום" של גלאי הסורק** לניתוח המניה (Deep Dive) |
| `pipeline_a_scanner.py` | Actions (`stock-scanner.yml`, `earnings-refresh.yml` = `--quick`) | סורק תבניות, יומן דוחות + הכנסות מהודעות SEC, `stock_estimates` (צפי EPS) |
| `pipeline_b_news_aggregator.py` | Actions (`news-aggregator.yml`) | חדשות RSS → סיווג → תרגום → `macro_news`; מפעיל `ai_digest` |
| `ai_digest.py`, `llm_provider.py` | Actions | סיכום AI יומי מצטבר (NVIDIA חינמי, ספק ניתן להחלפה) |
| `econ_calendar.py` | Render | לוח אירועים כלכליים (לוחות BLS/BEA/Census/FOMC + ערכים מהמסד) |
| `econ_refresh.py` | Actions (`econ-refresh.yml`) | מוריד סדרות "בפועל/קודם" ושומר ב-`econ_snapshots`. FRED לא עונה מאף מקום → BLS API, NY Fed, DBnomics, DOL, Census |
| `scheduler.py` | Render | מפעיל workflows בשעות ET מדויקות (`GITHUB_DISPATCH_TOKEN`) |
| **`translation_fixes.py`** | Render + Actions | תיקוני תרגום משותפים. **להעלות לפני/יחד עם `api_server.py`** — בלעדיו השרת לא עולה |
| **`news_links.py`** | Render + Actions | קישור Google News → כתובת הכתבה האמיתית (`article_url`). **כמו translation_fixes — בלעדיו השרת לא עולה** |
| **`push_alerts.py`** (v19) | Render + Actions | התראות Push: מנויי מכשירים, התראות מחיר, איתות על מניה מרשימת המעקב. **בלעדיו השרת לא עולה** (כמו translation_fixes) |
| `sw.js`, `manifest.json`, `icon-192.png`, `icon-512.png`, `icon-maskable-512.png` (v19) | Vercel | אפליקציה להתקנה (PWA) + קבלת התראות. `sw.js` לא שומר במטמון את ה-API, והעמוד "רשת קודם" |
| `cleanup_stale_signals.py` | Actions (ידני) | תחזוקה |
| `vercel.json` | Vercel | כותרות אבטחה (CSP מצומצם נאכף; מלא ב-Report-Only) |
| `favicon.svg`, `favicon-32.png`, `apple-touch-icon.png`, `logo.png`, `og-image.png` | Vercel | לוגו S (לבן על שחור), תמונת שיתוף 1200×630 |
| `docs/REFERENCE.md`, `docs/HISTORY.md` | — | תיעוד מפורט (ראה למעלה) |

## 3. כללי קוד קריטיים (שבירה = באגים שכבר קרו)
**סורק ↔ Deep Dive (תאומים):** כל שינוי בגלאי בסורק → אותו שינוי בפונקציה התאומה ב-`api_server.py` (`_scanner_rule_*`, `fit_trendline`↔`_fit_trendline`, `find_cup`↔`_find_cup`, קבועי `TRENDLINE_*`↔`_TL_*`). אחרת הסריקה ו-Deep Dive מציגים תבניות שונות (מקרה KDP). חיפוש קווי מגמה: עד **250** נרות (מקסימום, לא אורך כפוי); **אף שפל לא חוצה את הקו ביותר מ-0.5%** (`TRENDLINE_TOLERANCE`↔`_TL_TOLERANCE`). אזורי תמיכה/התנגדות (`_sr_zones`, מלבן בגרף) — תצוגה בלבד, אין תאום בסורק. **מחירים אמיתיים (`auto_adjust=False`) בסורק וב-Deep Dive** — כמו TradingView; מחירים מתואמי דיבידנד הזיזו פתילים (KO ‎−0.58%). פריצת התנגדות אופקית: רמה = ≥2 שיאים (`MIN_LEVEL_TOUCHES`, תאומים); פריצה = **הסגירה הראשונה מעל הרמה מאז הנגיעה האחרונה** (סורק: היום; Deep Dive: עד 20 נרות, אחרת "מתקרב" לרמה שמעל). קו אופקי בגרף יושב על הפתיל של הנגיעה הראשונה — **ראש הפתיל** לקו התנגדות, תחתיתו לקו עם `"wick": "low"` (תמיכה); מוסתר אם בטווח 2.5% מאזור.
**מספר שגוי גרוע מריק:** הכנסות/EPS רק מתקופה מאומתת; הכנסה מהודעת רווחים רק בטווח 0.8–1.25 מהצפי השמור; P/E רק מחישוב אמין. לא להציג רבעון קודם כאילו חדש.
**Yahoo `.info` חסום מ-Render** (תמיד): מפסק זרם 15 דק' ב-`_get_info_with_retry`. שווי שוק/P/E מ-SEC (מניות × מחיר; P/E = שווי ÷ רווח 4 רבעונים). **Forward P/E (NTM)** מטבלת `stock_estimates` שהסורק ממלא (שם `.info` עובד): `f=ימים שנותרו בשנת הכספים/365`, `NTM EPS = f·EPS(שנה נוכחית)+(1−f)·EPS(הבאה)`.
**SEC XBRL:** לבחור את יחידת המידה עם הכי הרבה רשומות (לא הראשונה — KO מכיל יחידה ישנה `pure`).
**פרונט:**
- כל טקסט חיצוני (חדשות, שמות חברות, תיאורים, AI) דרך `escH()`; קישורים דרך `safeUrl()`.
- הפריסה **LTR קבועה בשתי השפות**; עברית מקבלת `rtl-text` אוטומטית (`hebDir` + MutationObserver). "שורות טקסט" (flex שכל ילדיו inline) מקבלות `rtl-row` → בעברית מתחילות מימין. **לא** להשתמש ב-`margin/padding/inset/border-inline-*` (מתהפכים בתוך rtl-text) — רק left/right.
- כל ווידג'ט שמחזיק נתונים חייב להיות מרונדר מחדש ב-`applyLang()` (בלי לבנות מחדש דברים כבדים — `loadFromApi` קורא לה כל 20 שנ').
- טקסט על רקע ענבר: `var(--on-amber)`. צבעים רק ממשתני `:root` (כהה) / `:root[data-theme="light"]`.
- לא לשים `color` inline על `.compare-btn`. SVG של הגרף: `direction:ltr`.
- **צבעי גרף הניתוח (בקשת המשתמש):** כל מבנה (קווים אופקיים, קווי מגמה, אזורים, כוס/W) = `var(--struct)` צהוב בהיר; ממוצע 150 = `var(--ma150)` כחול TradingView (`#2962ff`) **בכל הגרפים** (גם ב-3 ווידג'טי TradingView); ממוצעים אחרים = `var(--sky)`. בגרף: התבנית הראשית + תבנית נוספת אחת בלבד.
- כל ווידג'ט TradingView עם `autosize:true` (גודל קבוע = גרף חתוך אחרי שינוי גודל חלון, HISTORY §27.3י).
- גרף TradingView בניתוח מניה: `_ddMountChart` אידמפוטנטי (`data-tv-key`), ResizeObserver, ושומר שבונה מחדש פעם אחת אחרי 8 שנ' אם לא הופיע.
- Deep Dive נטען בהדרגה (מחיר/גרף קודם; סקטור/מבנה טכני/פונדמנטלס מתמלאים; `_ddLoadId` מבטל תשובות ישנות).
**חדשות מרכזיות (פס אדום):** רק לפי סוגי אירועים + ≥3 מקורות (אחד מרכזי) או הודעה רשמית — `_KEY_EVENT_RULES` ב-`api_server.py`, `/api/key-news`; "Critical" באתר = שייך לאירוע כזה (לא התווית השמורה). לא לחזור למילים בודדות. HISTORY §27.3יא.
**תרגום:** Google מתרגם משפט שלם; לפני — `EN_REWRITES`, אחרי — `HE_FIXES` (ב-`translation_fixes.py`). **אסור** להחזיר מסכות placeholder (Google קרא "Q1Q" כרבעון ראשון). תרגום שגוי חדש באתר → להוסיף לרשימות.
**אבטחה:** SQL רק עם פרמטרים; `/api/article-preview` רק לכתובות שב-`macro_news`; admin עם `X-Admin-Token` (`hmac.compare_digest`); workflows עם `permissions: contents: read`.
**Neon:** לפתוח חיבור DB סמוך לכתיבה (auto-suspend).
**זיכרון Render (512MB, קרס 26.10):** קובץ companyfacts של SEC = 2–8MB, ~פי 10 בזיכרון. `_get_fundamentals_cached`: single-flight לכל טיקר + עד 2 בניות במקביל (`_FUND_MAX_PARALLEL`) + תקרת קאש. השוואת סקטור **לא** בונה פונדמנטלס לעמיתים (קאש או `stock_estimates`). לא להוסיף מקומות שמריצים `_build_fundamentals` לרשימת מניות.
**בדיקות מהמחשב הביתי:** לא להריץ עשרות קריאות API ברצף — Cloudflare של Render חוסם את ה-IP (429 `Cf-Mitigated: challenge`).

## 4. משתני סביבה (שמות בלבד — הערכים סודיים)
Render: `DATABASE_URL`, `JWT_SECRET` (≥32), `ALLOWED_ORIGINS`, `GITHUB_DISPATCH_TOKEN`, `ADMIN_TOKEN` (אופציונלי), `GOOGLE_CLIENT_ID` (אופציונלי), `TRUSTED_PROXY_HOPS`, `ENABLE_DOCS`, **`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`** (v19 התראות; נוצרו מקומית לקובץ `C:\Users\sabat\נסים\מפתחות-התראות-סודי.txt` — מחוץ לריפו).
GitHub Secrets: `DATABASE_URL`, `LLM_API_KEY`, **`VAPID_PRIVATE_KEY`** (v19); Variables: `LLM_MODELS` (מומלץ: `nvidia/nemotron-3.5-lightning-30b-a3b,nvidia/nemotron-3-ultra-550b-a55b` — לבדוק ב-probe), `LLM_BASE_URL`, `DIGEST_TZ`, `AI_DIGEST_MIN_HOURS`. אופציונלי: `FRED_API_KEY`, `BLS_API_KEY` (המשתמש לא נרשם).

## 5. טבלאות עיקריות
`scanned_stocks` (איתותים + `sim_*` לתיק דמו), `earnings_calendar` (`revenue_period_end = report_date` ⇒ הכנסה מהודעת SEC), `macro_news` (`summary_he/en`, `body_*`, `source_name`), `ai_daily_digests`, `econ_events`, `econ_snapshots` (`series-<id>` + snapshot הלוח), `universe_movers`, `users`, `watchlist` (אישי), `analytics_events`, `scheduler_runs`, **`stock_estimates` (חדש v18)**, **`push_subscriptions`, `price_alerts`, `push_log` (v19)**, **`watch_earnings` (v20d)**. פירוט: REFERENCE §5.

## 6. סטטוס ופתוח (עודכן 28.10.2026 — v20)
**v20 (28.10, HISTORY §27.3טו, רק `index.html`):** ~~שינויי גרף בטלפון~~ — **בוטלו לבקשת המשתמש** ("גרף רגיל עם ממוצע ו-ווליום כמו קודם"): לא להסתיר נפח/להוסיף כפתורי טווח בגרפי TradingView בלי אישור; **v20b:** בטלפון רק גובה גדול יותר (~420–460px), גרף רגיל; key-news נטען במקביל; מטמון 10 דק' ל-`/api/pattern-stats` (2.8→~0.4 שנ'); **v20c פתיחה מיידית:** `restoreSnapshot()` מצייר את הנתונים האחרונים מ-`localStorage` (`swing_snapshot_v1`, עד 4 ימים) עם "מוצגים נתונים מ-HH:MM" עד שהשרת עונה — נתון חדש שנטען לדף הבית צריך גם `saveSnapshot`; **v20d התראות דוחות לרשימת מעקב:** `earnings_alerts_job` (quick refresh) + טבלה `watch_earnings` + `push_alerts.notify_watchlist_earnings` — תזכורת לפני ותוצאה אחרי, אותו מתג `watch_signals`; **לבדוק בלוג earnings-refresh** `watch earnings:`; **v20e:** תגית "דוח בעוד X ימים" מתחת לטקסט שורת הסורק (`earningsSoonBadgeHtml`, ימי UTC מיום הסריקה); **התראה ~40 דק' לפני נתון מאקרו חשוב** (thread `_macro_alert_loop` בשרת, עמודה `push_subscriptions.watch_macro`, מתג נפרד, כבוי כברירת מחדל). ב-`api_server.py` ‏`datetime` מיובא כ-`dt`! **v20f פרה-מרקט:** "הכי זזות" לפני הפתיחה (`marketState=="PRE"` בציטוטי הסורקים של Yahoo) = תזוזות פרה-מרקט (`_fetch_premarket_movers`, ≥$2B, `session:"pre"`), אחרי הפתיחה כרגיל; **יומן דוחות = פס ימים** (יום אחד בכל פעם, לפני פתיחה/אחרי סגירה) + **פרטי דוח בלחיצה** (תגובת המניה מול S&P מ-`/api/price-compare`, 4 רבעוני EPS, הכנסות/רווח מול שנה קודמת ומכפיל מ-`/api/fundamentals` — בלי שינוי שרת); כותרות סעיפים אחידות (בלי אותיות רווחות/נקודות צבע), הוסרו אימוג'י צבעוניים אחרונים.
**v19 (27.10, HISTORY §27.3יב):** בלי נתוני דמו מזויפים בטעינה (`apiLoadState`); פיד חדשות בלי כותרות "מניות לקנות" ובלי כפילויות (`_feed_clean`); כלל CPI מחמיר (Loblaw); תיקוני תרגום (crypto→"אנוסים" ועוד); הסבר תמיכה/התנגדות מזכיר את האזור בגרף; תקרות גרסה ב-`requirements.txt`. **כיווני מוצר: HISTORY §27.5.**
**v19c (HISTORY §27.3יד):** גרף סקטורים — לחיצה = בחירה להשוואה (לא הסתרה); תקציר כתבה מתורגם (שרת: gtx→dict→MyMemory, כישלון לא נשמר; אחרת תרגום בדפדפן `translateHeBrowser`). Google gtx חוסם כתובת אחרי רצף בקשות — `clients5…dict-chrome-ex` הוא הגיבוי.
**כלל חדש:** אסור להציג באתר נתונים מומצאים כאילו אמיתיים — במצב טעינה/שגיאה מציגים הודעה, לא דמו.
**v19b (27.10, HISTORY §27.3יג) — "מוצר":** עיצוב בלי אימוג'י/💡 חוזר, כותרות טבעיות; טלפון: סרגל ניווט תחתון, סורק אחרי האירועים (8 שורות + "הצג הכל"); **PWA** (התקנה, `?ticker=`/`?page=` קישורים); **סינון סקטור** (`scanned_stocks.sector`, ה-API משלים מרשימת S&P); **סיכום יומי קצר** (שורה + מדדים אמיתיים + 3–4 נקודות + "מה בהמשך" מלוח האירועים; פרומפט קצר ב-`ai_digest.py`); **התראות Push** (מחיר + איתות ברשימת מעקב; `push_alerts.py`, `/api/push/*`, `/api/alerts`); כפתור שיתוף בניתוח מניה. עיצוב: לא להחזיר אימוג'י לממשק; הסבר "למה זה חשוב" רק בפתיחת ידיעה.
**בוצע היום (פירוט: HISTORY §27):** פענוח הכנסות מהודעות SEC (טבלאות, צפי שמור, VZ/BX תוקנו); מקורות "בפועל" בלי מפתח לכל 6 סדרות FRED; לוח חודשי מיום שני; תיקוני תצוגה; תמונת שיתוף; Deep Dive הדרגתי; EPS/P/E מ-SEC (יחידות); מפסק `.info`; אבטחה (article-preview, escH, permissions); קו מגמה ראשי + הצמדת קווים אופקיים לפתיל; תרגום חדש; יישור שורות עברית לימין; **Forward P/E (NTM)**; קווי מגמה עד 250 נרות (סורק+Deep Dive), גרף שמתארך לפי הקו; **קו מגמה מחמיר (0.5%) + אזורי תמיכה/התנגדות כמלבן** (HISTORY §27.3ה); **קו התנגדות על ראש הפתיל + פריצה רק בפעם הראשונה** (KO/ANET, HISTORY §27.3ט); גרף מדדים `autosize` (§27.3י); **חדשות מרכזיות לפי אירועים + קישור אמיתי לכתבות Google News** (§27.3יא).
**לבדוק:** בלוג news-aggregator — `article links: decoded N ... failed M` (v18e תיקן באג קריאה של 800KB — §27.3יא; אם עדיין הכל נכשל — חסימה אמיתית).
**ממתין לבדיקה אחרי העלאה:** ראה HISTORY §27.4 (רשימת בדיקות מלאה).
**פתוח:**
1. Forward P/E יופיע רק אחרי ריצת **סריקה מלאה** (לא quick); לוודא בלוג `stock estimates: saved N`. אם 0 עם צפי — לבדוק שמות שדות `.info` (`forwardEps`, `epsCurrentYear`, `nextFiscalYearEnd`).
2. שינויי קווי מגמה בסורק (250 נרות + סבולת 0.5%): לעקוב אחרי כמות איתותי קו מגמה — אם מעט מדי/הרבה מדי, לכוונן `MIN_TOUCHES`/`TOLERANCE` (בשני התאומים). (ההסבר של תמיכה אופקית מול האזור — תוקן v19.) שמות חברות מתורגמים (First Solar, MAR→"מרץ") — עדיין פתוח.
3. מקורות חלופיים ללוח: DOL claims עצר ב-26.9 (בצד DOL); DBnomics מתעדכן ~יום אחרי.
4. אבטחה להמשך: קיבוע מדויק של גרסאות `requirements.txt` (v19 = תקרות בלבד; לקבע לפי לוג הבנייה של Render), ביטול טוקנים/בדיקת משתמש בכל בקשה, אכיפת CSP מלא.
5. מוצר (חוסמים לפני ציבור): דומיין + שם, רישוי נתוני Yahoo/חדשות/תרגום, NVIDIA בתשלום, עו"ד (שיווק השקעות, פרטיות), `LEGAL_CONTACT_EMAIL`, SEO (REFERENCE/HISTORY §25.3).
6. תחזוקה שנתית (דצמבר–ינואר): `BLS_STATIC`, `BEA_STATIC`, `CENSUS_STATIC`, `FOMC_DECISIONS` ב-`econ_calendar.py`.

## 7. איפה למצוא מה (docs)
| נושא | איפה |
|---|---|
| סכמת DB מלאה, כל ה-endpoints | REFERENCE §5, §6 |
| הסורק: universe, 15 תבניות, סדר עדיפויות, כוס, יומן דוחות, Deep Dive §7.1 | REFERENCE §7 |
| חדשות, סיווג, Critical | REFERENCE §8; תרגום — `translation_fixes.py` + HISTORY §27.3ג |
| פרונט: עמודים, התחברות, מצב בהיר, בורר השוואה | REFERENCE §9; HISTORY §19, §26 |
| החלטות ארכיטקטורה (למה) / מגבלות / TODO | REFERENCE §10, §12, §13 |
| לוח אירועים — תכנון, ספקים, "בפועל" | REFERENCE §16; HISTORY §22, §26.7, §27 |
| תיק דמו / בדיקה היסטורית | HISTORY §18 |
| התחברות Google, תזמון (`scheduler.py`) | HISTORY §19 |
| AI (ספק, הגנות, probe, סיכום מצטבר) | HISTORY §20, §23 |
| אבטחה/משפטי/פרטיות | HISTORY §21, §27.3ב-ג |
| Vercel↔GitHub, SEO, לוגו, סריקות אתר | HISTORY §23–§26 |
| מה אומת ומה לא, לפי גרסה | HISTORY §15 ואילך |
