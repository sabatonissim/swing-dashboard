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
| `cleanup_stale_signals.py` | Actions (ידני) | תחזוקה |
| `vercel.json` | Vercel | כותרות אבטחה (CSP מצומצם נאכף; מלא ב-Report-Only) |
| `favicon.svg`, `favicon-32.png`, `apple-touch-icon.png`, `logo.png`, `og-image.png` | Vercel | לוגו S (לבן על שחור), תמונת שיתוף 1200×630 |
| `docs/REFERENCE.md`, `docs/HISTORY.md` | — | תיעוד מפורט (ראה למעלה) |

## 3. כללי קוד קריטיים (שבירה = באגים שכבר קרו)
**סורק ↔ Deep Dive (תאומים):** כל שינוי בגלאי בסורק → אותו שינוי בפונקציה התאומה ב-`api_server.py` (`_scanner_rule_*`, `fit_trendline`↔`_fit_trendline`, `find_cup`↔`_find_cup`, קבועי `TRENDLINE_*`↔`_TL_*`). אחרת הסריקה ו-Deep Dive מציגים תבניות שונות (מקרה KDP). חיפוש קווי מגמה: עד **250** נרות (מקסימום, לא אורך כפוי); **אף שפל לא חוצה את הקו ביותר מ-0.5%** (`TRENDLINE_TOLERANCE`↔`_TL_TOLERANCE`). אזורי תמיכה/התנגדות (`_sr_zones`, מלבן בגרף) — תצוגה בלבד, אין תאום בסורק.
**מספר שגוי גרוע מריק:** הכנסות/EPS רק מתקופה מאומתת; הכנסה מהודעת רווחים רק בטווח 0.8–1.25 מהצפי השמור; P/E רק מחישוב אמין. לא להציג רבעון קודם כאילו חדש.
**Yahoo `.info` חסום מ-Render** (תמיד): מפסק זרם 15 דק' ב-`_get_info_with_retry`. שווי שוק/P/E מ-SEC (מניות × מחיר; P/E = שווי ÷ רווח 4 רבעונים). **Forward P/E (NTM)** מטבלת `stock_estimates` שהסורק ממלא (שם `.info` עובד): `f=ימים שנותרו בשנת הכספים/365`, `NTM EPS = f·EPS(שנה נוכחית)+(1−f)·EPS(הבאה)`.
**SEC XBRL:** לבחור את יחידת המידה עם הכי הרבה רשומות (לא הראשונה — KO מכיל יחידה ישנה `pure`).
**פרונט:**
- כל טקסט חיצוני (חדשות, שמות חברות, תיאורים, AI) דרך `escH()`; קישורים דרך `safeUrl()`.
- הפריסה **LTR קבועה בשתי השפות**; עברית מקבלת `rtl-text` אוטומטית (`hebDir` + MutationObserver). "שורות טקסט" (flex שכל ילדיו inline) מקבלות `rtl-row` → בעברית מתחילות מימין. **לא** להשתמש ב-`margin/padding/inset/border-inline-*` (מתהפכים בתוך rtl-text) — רק left/right.
- כל ווידג'ט שמחזיק נתונים חייב להיות מרונדר מחדש ב-`applyLang()` (בלי לבנות מחדש דברים כבדים — `loadFromApi` קורא לה כל 20 שנ').
- טקסט על רקע ענבר: `var(--on-amber)`. צבעים רק ממשתני `:root` (כהה) / `:root[data-theme="light"]`.
- לא לשים `color` inline על `.compare-btn`. SVG של הגרף: `direction:ltr`.
- Deep Dive נטען בהדרגה (מחיר/גרף קודם; סקטור/מבנה טכני/פונדמנטלס מתמלאים; `_ddLoadId` מבטל תשובות ישנות).
**תרגום:** Google מתרגם משפט שלם; לפני — `EN_REWRITES`, אחרי — `HE_FIXES` (ב-`translation_fixes.py`). **אסור** להחזיר מסכות placeholder (Google קרא "Q1Q" כרבעון ראשון). תרגום שגוי חדש באתר → להוסיף לרשימות.
**אבטחה:** SQL רק עם פרמטרים; `/api/article-preview` רק לכתובות שב-`macro_news`; admin עם `X-Admin-Token` (`hmac.compare_digest`); workflows עם `permissions: contents: read`.
**Neon:** לפתוח חיבור DB סמוך לכתיבה (auto-suspend).

## 4. משתני סביבה (שמות בלבד — הערכים סודיים)
Render: `DATABASE_URL`, `JWT_SECRET` (≥32), `ALLOWED_ORIGINS`, `GITHUB_DISPATCH_TOKEN`, `ADMIN_TOKEN` (אופציונלי), `GOOGLE_CLIENT_ID` (אופציונלי), `TRUSTED_PROXY_HOPS`, `ENABLE_DOCS`.
GitHub Secrets: `DATABASE_URL`, `LLM_API_KEY`; Variables: `LLM_MODELS` (מומלץ: `nvidia/nemotron-3.5-lightning-30b-a3b,nvidia/nemotron-3-ultra-550b-a55b` — לבדוק ב-probe), `LLM_BASE_URL`, `DIGEST_TZ`, `AI_DIGEST_MIN_HOURS`. אופציונלי: `FRED_API_KEY`, `BLS_API_KEY` (המשתמש לא נרשם).

## 5. טבלאות עיקריות
`scanned_stocks` (איתותים + `sim_*` לתיק דמו), `earnings_calendar` (`revenue_period_end = report_date` ⇒ הכנסה מהודעת SEC), `macro_news` (`summary_he/en`, `body_*`, `source_name`), `ai_daily_digests`, `econ_events`, `econ_snapshots` (`series-<id>` + snapshot הלוח), `universe_movers`, `users`, `watchlist` (אישי), `analytics_events`, `scheduler_runs`, **`stock_estimates` (חדש v18)**. פירוט: REFERENCE §5.

## 6. סטטוס ופתוח (עודכן 26.10.2026 — v18)
**בוצע היום (פירוט: HISTORY §27):** פענוח הכנסות מהודעות SEC (טבלאות, צפי שמור, VZ/BX תוקנו); מקורות "בפועל" בלי מפתח לכל 6 סדרות FRED; לוח חודשי מיום שני; תיקוני תצוגה; תמונת שיתוף; Deep Dive הדרגתי; EPS/P/E מ-SEC (יחידות); מפסק `.info`; אבטחה (article-preview, escH, permissions); קו מגמה ראשי + הצמדת קווים אופקיים לפתיל; תרגום חדש; יישור שורות עברית לימין; **Forward P/E (NTM)**; קווי מגמה עד 250 נרות (סורק+Deep Dive), גרף שמתארך לפי הקו; **קו מגמה מחמיר (0.5%) + אזורי תמיכה/התנגדות כמלבן** (HISTORY §27.3ה).
**ממתין לבדיקה אחרי העלאה:** ראה HISTORY §27.4 (רשימת בדיקות מלאה).
**פתוח:**
1. Forward P/E יופיע רק אחרי ריצת **סריקה מלאה** (לא quick); לוודא בלוג `stock estimates: saved N`. אם 0 עם צפי — לבדוק שמות שדות `.info` (`forwardEps`, `epsCurrentYear`, `nextFiscalYearEnd`).
2. שינויי קווי מגמה בסורק (250 נרות + סבולת 0.5%): לעקוב אחרי כמות איתותי קו מגמה — אם מעט מדי/הרבה מדי, לכוונן `MIN_TOUCHES`/`TOLERANCE` (בשני התאומים). ההסבר הטקסטואלי של "ריבאונד מתמיכה אופקית" עדיין מציג את ממוצע האשכול (למשל $86.07) גם כשהגרף מציג אזור (85.13–85.68).
3. מקורות חלופיים ללוח: DOL claims עצר ב-26.9 (בצד DOL); DBnomics מתעדכן ~יום אחרי.
4. אבטחה להמשך: הצמדת גרסאות `requirements.txt`, ביטול טוקנים/בדיקת משתמש בכל בקשה, אכיפת CSP מלא.
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
