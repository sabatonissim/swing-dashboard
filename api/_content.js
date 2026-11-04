// v35: the text of the site's own pages (api/page.js). A name starting with "_" is not an address of its own on Vercel.
// Rule for everything here: it describes and explains, it never recommends (CLAUDE.md §0), and on the site it is "scans", not "signals".
const LEGAL_CONTACT_EMAIL = '';   // <-- put your contact email here (required for a public product)
const LEGAL_LAST_UPDATED = '2026-11-04';
const PUBLISHED = '2026-11-04';

const mail = (lang) => (LEGAL_CONTACT_EMAIL
  ? `<a href="mailto:${LEGAL_CONTACT_EMAIL}">${LEGAL_CONTACT_EMAIL}</a>`
  : (lang === 'he' ? '(כתובת מייל ליצירת קשר טרם הוגדרה)' : '(contact email not set)'));

const DISCLAIMER = {
  en: 'Information only, for education and illustration. Not investment advice, a recommendation or an offer to buy or sell any security. Trading involves a risk of loss.',
  he: 'מידע כללי בלבד, ללימוד ולהמחשה. אינו ייעוץ השקעות, המלצה או הצעה לקנות או למכור נייר ערך. מסחר כרוך בסיכון להפסד.',
};

// what the scanner stores in scanned_stocks.pattern_type -> the same names as PATTERN_LABELS in index.html, and its guide
const PATTERNS = {
  cup_handle: { slug: 'cup-and-handle', en: 'Cup & Handle', he: 'כוס וידית' },
  cup_no_handle: { slug: 'cup-and-handle', en: 'Cup (No Handle)', he: 'כוס ללא ידית' },
  double_bottom: { slug: 'double-bottom', en: 'Double Bottom', he: 'תחתית כפולה' },
  '52w_high': { slug: '52-week-high-breakout', en: '52-Week High Breakout', he: 'פריצת שיא 52 שבועות' },
  horizontal_resistance_breakout: { slug: 'horizontal-resistance-breakout', en: 'Horizontal Resistance Breakout', he: 'פריצת התנגדות אופקית' },
  golden_cross: { slug: 'golden-cross', en: 'Golden Cross', he: 'גולדן קרוס' },
  ma150_breakout: { slug: '150-day-moving-average', en: '150-Day MA Breakout', he: 'פריצת ממוצע 150 יום' },
  bull_flag: { slug: 'bull-flag', en: 'Bull Flag', he: 'דגל שורי' },
  ascending_triangle: { slug: 'ascending-triangle', en: 'Ascending Triangle', he: 'משולש עולה' },
  ascending_trendline: { slug: 'ascending-trendline-support', en: 'Ascending Trendline Support', he: 'תמיכת קו מגמה עולה' },
  ma150_support_bounce: { slug: '150-day-moving-average', en: '150-Day MA Support Bounce', he: 'תמיכת ממוצע 150 יום' },
  horizontal_level_bounce: { slug: 'horizontal-support-bounce', en: 'Horizontal Support Bounce', he: 'תמיכה אופקית' },
  descending_trendline_breakout: { slug: 'descending-trendline-breakout', en: 'Descending Trendline Breakout', he: 'פריצת קו מגמה יורד' },
  macd_cross: { slug: 'macd-crossover', en: 'MACD Bullish Crossover', he: 'חציית MACD חיובית' },
  momentum_surge: { slug: 'momentum-surge', en: 'Momentum Surge', he: 'זינוק מומנטום' },
  rsi_bounce: { slug: 'rsi-oversold-bounce', en: 'RSI Oversold Bounce', he: 'התאוששות RSI' },
};

const SECTOR_HE = {
  'Information Technology': 'טכנולוגיה', 'Health Care': 'בריאות', Financials: 'פיננסים', 'Consumer Discretionary': 'צריכה מחזורית',
  'Consumer Staples': 'צריכה בסיסית', Energy: 'אנרגיה', Industrials: 'תעשייה', Materials: 'חומרי גלם', 'Real Estate': 'נדל"ן',
  Utilities: 'תשתיות', 'Communication Services': 'תקשורת',
};

const POPULAR = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'AVGO', 'BRK-B', 'JPM', 'V', 'MA', 'UNH', 'XOM', 'LLY',
  'COST', 'WMT', 'HD', 'KO', 'PEP', 'NFLX', 'AMD', 'INTC', 'CRM', 'ORCL', 'ADBE', 'BAC', 'DIS', 'NKE', 'BA'];

const GROUPS = [
  { id: 'basics', en: 'Start here', he: 'מתחילים כאן' },
  { id: 'patterns', en: 'Chart patterns the scanner looks for', he: 'תבניות גרף שהסורק מחפש' },
  { id: 'measures', en: 'Strength, trend and volatility', he: 'עוצמה, מגמה ותנודתיות' },
  { id: 'market', en: 'Market and company data', he: 'נתוני שוק וחברות' },
];

const ARTICLES = [...require('./_learn_patterns.js'), ...require('./_learn_market.js')];

const DOCS = {
  about: {
    en: {
      short: 'About',
      heading: 'About Swing Desk',
      title: 'About Swing Desk: What It Is and Where the Data Comes From',
      description: 'Swing Desk is a free dashboard for swing trading in US stocks: a rules-based scanner, market news and calendars. How it works and where each figure comes from.',
      body: `<p class="lead">Swing Desk is a dashboard for swing trading in US stocks. It shows what a person following the market checks every day in one place, and it says where each figure comes from.</p>
<h2>What is on the site</h2>
<ul>
<li><b>Scanner.</b> Fixed rules run over hundreds of large US stocks during the trading day and list the ones where a chart pattern has just completed. Each rule is written out in the <a href="/learn">guides</a>.</li>
<li><b>A page for each stock.</b> A company profile from its annual report, the chart, the current technical setup, fundamentals from SEC filings, earnings dates and results, insider transactions and news.</li>
<li><b>The market.</b> Market breadth, the yield curve, a sector heatmap, an economic calendar, an earnings calendar, and what large funds report holding.</li>
<li><b>News.</b> Headlines gathered from financial publishers, each linking to the original article, with a daily summary written by an AI model and labelled as such.</li>
<li><b>Your own tools.</b> After signing in with Google: a watchlist, a record of your trades, and price alerts.</li>
</ul>
<h2>What it is not</h2>
<p>Swing Desk is not an investment adviser and does not recommend buying or selling anything. A scan says that a stock's chart meets a written rule on a given day. It says nothing about whether the stock suits anyone, or about what will happen next. Past statistics shown on the site are measured results with simplifying assumptions (no fees, slippage or taxes), and they do not promise anything about the future.</p>
<h2>How scans are measured</h2>
<p>The site keeps a public record of what its scans did afterwards: the share of scans that were higher after 10 and 20 trading days, next to the S&P 500 over the same days. Only scans from November 3, 2026 onward are counted, because that is when the current rules took effect, so the sample is still small.</p>
<h2 id="data">Where the data comes from</h2>
<ul>
<li><b>Prices, volume, market cap:</b> Yahoo Finance, delayed by up to 15 minutes.</li>
<li><b>Fundamentals:</b> SEC EDGAR, from what the companies themselves file.</li>
<li><b>Company profiles:</b> the business section of the annual report filed with the SEC (10-K or 20-F), summarized by an AI model. When a company has no such section, the Wikipedia summary. The source is named on the page, and every number in a summary must appear in the source.</li>
<li><b>Earnings:</b> dates from the Nasdaq and Yahoo Finance calendars; results from Yahoo Finance and from the company's press release filed with the SEC.</li>
<li><b>Economic calendar:</b> the published schedules and data of the BLS, BEA, Census Bureau, Department of Labor and the Federal Reserve, and the DBnomics archive of official statistics.</li>
<li><b>Insider transactions and fund holdings:</b> SEC Form 4 and Form 13F.</li>
<li><b>Yield curve:</b> the US Treasury's published rates.</li>
<li><b>News:</b> publishers' RSS feeds and Google News. Headlines and excerpts belong to their publishers. Hebrew versions are machine translations and may be inaccurate.</li>
<li><b>Fear &amp; Greed:</b> CNN's published index. <b>Charts:</b> TradingView.</li>
</ul>
<h2>Contact</h2>
<p>${mail('en')}</p>
<p class="dim">${DISCLAIMER.en}</p>`,
    },
    he: {
      short: 'אודות',
      heading: 'אודות Swing Desk',
      title: 'אודות Swing Desk: מה זה ומאיפה מגיעים הנתונים',
      description: 'Swing Desk הוא דשבורד חינמי למסחר סווינג במניות אמריקאיות: סורק לפי כללים, חדשות שוק ולוחות אירועים. איך זה עובד ומאיפה מגיע כל נתון.',
      body: `<p class="lead">Swing Desk הוא דשבורד למסחר סווינג במניות אמריקאיות. הוא מרכז במקום אחד את מה שמי שעוקב אחרי השוק בודק כל יום, ואומר מאיפה מגיע כל נתון.</p>
<h2>מה יש באתר</h2>
<ul>
<li><b>סורק.</b> כללים קבועים רצים על מאות מניות אמריקאיות גדולות במהלך יום המסחר, ומציגים את המניות שבהן תבנית גרף הושלמה זה עתה. כל כלל כתוב במלואו ב<a href="/he/learn">מדריכים</a>.</li>
<li><b>עמוד לכל מניה.</b> פרופיל חברה מתוך הדוח השנתי שלה, גרף, המבנה הטכני הנוכחי, נתונים פונדמנטליים מדיווחים ל-SEC, מועדי דוחות ותוצאות, עסקאות בעלי עניין וחדשות.</li>
<li><b>השוק.</b> רוחב השוק, עקום התשואות, מפת חום לפי סקטור, לוח אירועים כלכליים, יומן דוחות, ומה שקרנות גדולות מדווחות שהן מחזיקות.</li>
<li><b>חדשות.</b> כותרות ממו"לים פיננסיים, עם קישור לכתבה המקורית, וסיכום יומי שכותב מודל AI ומסומן ככזה.</li>
<li><b>כלים אישיים.</b> אחרי התחברות עם Google: רשימת מעקב, רישום העסקאות שלך והתראות מחיר.</li>
</ul>
<h2>מה זה לא</h2>
<p>Swing Desk אינו יועץ השקעות ואינו ממליץ לקנות או למכור דבר. סריקה אומרת שהגרף של מניה עומד בכלל כתוב ביום מסוים. היא לא אומרת אם המניה מתאימה למישהו, ולא מה יקרה בהמשך. הסטטיסטיקות שמוצגות באתר הן תוצאות מדודות עם הנחות מפשטות (בלי עמלות, החלקות ומסים), ואינן מבטיחות דבר לגבי העתיד.</p>
<h2>איך מודדים את הסריקות</h2>
<p>האתר שומר תיעוד גלוי של מה שקרה אחרי הסריקות: כמה מהן היו גבוהות יותר אחרי 10 ו-20 ימי מסחר, לצד מדד S&P 500 באותם ימים. נספרות רק סריקות מ-3 בנובמבר 2026 ואילך, כי אז נכנסו לתוקף הכללים הנוכחיים, ולכן המדגם עדיין קטן.</p>
<h2 id="data">מאיפה מגיעים הנתונים</h2>
<ul>
<li><b>מחירים, נפח מסחר, שווי שוק:</b> Yahoo Finance, בעיכוב של עד 15 דקות.</li>
<li><b>נתונים פונדמנטליים:</b> SEC EDGAR, מתוך מה שהחברות עצמן מגישות.</li>
<li><b>פרופיל חברה:</b> פרק העסקים בדוח השנתי שהוגש ל-SEC (טופס 10-K או 20-F), בסיכום של מודל AI. כשאין פרק כזה, התקציר בוויקיפדיה. המקור מצוין בעמוד, וכל מספר בסיכום חייב להופיע במקור.</li>
<li><b>דוחות כספיים:</b> מועדים מלוחות Nasdaq ו-Yahoo Finance; תוצאות מ-Yahoo Finance ומהודעת החברה ל-SEC.</li>
<li><b>לוח כלכלי:</b> לוחות הזמנים והנתונים שמפרסמים BLS, BEA, לשכת המפקד, משרד העבודה והפדרל ריזרב, וארכיון הסטטיסטיקה הרשמית DBnomics.</li>
<li><b>עסקאות בעלי עניין ואחזקות קרנות:</b> טפסים 4 ו-13F של SEC.</li>
<li><b>עקום התשואות:</b> השיעורים שמפרסם משרד האוצר האמריקאי.</li>
<li><b>חדשות:</b> פידי RSS של מו"לים ו-Google News. הכותרות והקטעים שייכים למו"לים. הגרסאות בעברית הן תרגום מכונה ועשויות להיות לא מדויקות.</li>
<li><b>מדד פחד ותאווה:</b> המדד שמפרסמת CNN. <b>גרפים:</b> TradingView.</li>
</ul>
<h2>יצירת קשר</h2>
<p>${mail('he')}</p>
<p class="dim">${DISCLAIMER.he}</p>`,
    },
  },
  privacy: {
    en: {
      short: 'Privacy',
      heading: 'Privacy Policy',
      title: 'Privacy Policy | Swing Desk',
      description: 'What information Swing Desk collects, why, who processes it, how long it is kept, and how to download or delete your data.',
      body: `<p class="dim">Last updated: <bdi>${LEGAL_LAST_UPDATED}</bdi></p>
<p><b>What we collect.</b> (1) If you sign in with Google: your email address, name and profile picture, as provided by Google, and the last-login time. (2) What you add yourself while signed in: your watchlist, the trades you record (prices, quantities, dates, notes), your price alerts, and, if you turn on notifications, your device's notification address and your notification choices. (3) If you allow it in the consent banner: anonymous usage events (page views, tickers or news categories opened) tied to a random identifier stored in your browser, not to your name or email. (4) Technical data processed by our hosting providers (such as IP address in server logs). On a first visit your country, as seen by our hosting provider, chooses the site's language; it is not stored.</p>
<p><b>Browser storage.</b> We store in your browser: your language and theme, your filters and selected news categories, recent data so that the site opens quickly, the sign-in token (needed to keep you signed in), and, only if you allow analytics, the random identifier.</p>
<p><b>Why.</b> To operate the site, keep your personal watchlist, trades and alerts, send the notifications you asked for, secure the service and, with your consent, understand which features are used and improve the service.</p>
<p><b>Service providers.</b> Hosting and database (Render, Neon, Vercel; servers may be outside your country, including the US), Google (sign-in, fonts), TradingView (embedded charts), and the market-data and news sources listed on the <a href="/about#data">About page</a>. These providers may process technical data such as your IP address according to their own policies.</p>
<p><b>Retention.</b> Account data is kept until you delete your account. Raw anonymous usage events are deleted after 180 days.</p>
<p><b>Your rights.</b> From the profile menu you can download a copy of your data and permanently delete your account with everything saved in it (watchlist, trades, alerts, notification devices). You may also contact us to request access, correction or deletion.</p>
<p><b>Security.</b> We use encrypted connections and limit access to the database, but no system is completely secure.</p>
<p><b>Changes to this policy.</b> We may update this policy as the service develops. Material changes, such as a new use of your data, will be published here with a new "last updated" date and, where required by law, will ask for your consent.</p>
<p><b>Contact.</b> ${mail('en')}</p>`,
    },
    he: {
      short: 'פרטיות',
      heading: 'מדיניות פרטיות',
      title: 'מדיניות פרטיות | Swing Desk',
      description: 'איזה מידע Swing Desk אוסף, למה, מי מעבד אותו, כמה זמן הוא נשמר, ואיך להוריד או למחוק את המידע שלך.',
      body: `<p class="dim">עודכן לאחרונה: <bdi>${LEGAL_LAST_UPDATED}</bdi></p>
<p><b>איזה מידע נאסף.</b> (1) אם התחברת עם Google: כתובת המייל, השם ותמונת הפרופיל כפי ש-Google מוסרת, וזמן ההתחברות האחרון. (2) מה שהוספת בעצמך כשאתה מחובר: רשימת המעקב, העסקאות שרשמת (מחירים, כמויות, תאריכים, הערות), התראות המחיר, ואם הפעלת התראות, כתובת ההתראות של המכשיר והבחירות שלך לגביהן. (3) אם אישרת בבאנר ההסכמה: אירועי שימוש אנונימיים (צפייה בעמודים, בטיקרים או בקטגוריות חדשות) המקושרים למזהה אקראי בדפדפן, לא לשם או למייל שלך. (4) מידע טכני שמעובד אצל ספקי האחסון (למשל כתובת IP ביומני שרת). בכניסה הראשונה המדינה שלך, כפי שספק האחסון רואה אותה, קובעת את שפת האתר; היא לא נשמרת.</p>
<p><b>אחסון בדפדפן.</b> נשמרים בדפדפן שלך: השפה וערכת הנושא, המסננים וקטגוריות החדשות שבחרת, נתונים אחרונים כדי שהאתר ייפתח מהר, אסימון ההתחברות (נדרש כדי להישאר מחובר), ורק אם אישרת סטטיסטיקה, המזהה האקראי.</p>
<p><b>למה.</b> להפעלת האתר, לשמירת רשימת המעקב, העסקאות וההתראות שלך, לשליחת ההתראות שביקשת, לאבטחת השירות ובהסכמתך, להבין באילו תכונות משתמשים ולשפר את השירות.</p>
<p><b>ספקי שירות.</b> אחסון ומסד נתונים (Render, Neon, Vercel; השרתים עשויים להיות מחוץ לישראל, כולל בארה"ב), Google (התחברות, גופנים), TradingView (גרפים משובצים), ומקורות נתוני השוק והחדשות המפורטים ב<a href="/he/about#data">עמוד האודות</a>. ספקים אלה עשויים לעבד מידע טכני כמו כתובת IP לפי מדיניותם.</p>
<p><b>שמירה.</b> נתוני החשבון נשמרים עד שתמחק את החשבון. אירועי שימוש אנונימיים גולמיים נמחקים לאחר 180 יום.</p>
<p><b>הזכויות שלך.</b> מתפריט הפרופיל אפשר להוריד עותק של המידע ולמחוק לצמיתות את החשבון עם כל מה שנשמר בו (רשימת מעקב, עסקאות, התראות, מכשירים להתראות). אפשר גם לפנות אלינו בבקשה לעיון, תיקון או מחיקה.</p>
<p><b>אבטחה.</b> אנו משתמשים בחיבורים מוצפנים ומגבילים גישה למסד הנתונים, אך אין מערכת מאובטחת לחלוטין.</p>
<p><b>שינויים במדיניות.</b> ייתכן שנעדכן מדיניות זו ככל שהשירות מתפתח. שינויים מהותיים, כגון שימוש חדש במידע שלך, יפורסמו כאן עם תאריך עדכון חדש, ואם החוק דורש, נבקש את הסכמתך מחדש.</p>
<p><b>יצירת קשר.</b> ${mail('he')}</p>`,
    },
  },
  terms: {
    en: {
      short: 'Terms',
      heading: 'Terms of Use',
      title: 'Terms of Use | Swing Desk',
      description: 'The terms for using Swing Desk: the site provides information only, not investment advice; past results do not guarantee future results; data may be delayed or wrong.',
      body: `<p class="dim">Last updated: <bdi>${LEGAL_LAST_UPDATED}</bdi></p>
<p><b>Information only.</b> Everything on this site (prices, scores, chart patterns, statistics, simulated "demo portfolio" results, economic calendar and AI-generated summaries) is general information for illustration and education. It is <b>not investment advice, an investment recommendation, marketing, or an offer to buy or sell any security</b>, and it does not take into account your personal situation. We are not a licensed investment adviser. Consult a licensed professional before making decisions.</p>
<p><b>Past performance.</b> Historical and back-tested results, including pattern success rates and the demo portfolio, are simulations with simplifying assumptions (no fees, slippage or taxes) and do not guarantee future results. Trading involves a risk of loss, including loss of the entire investment.</p>
<p><b>AI content.</b> AI summaries are generated automatically from news headlines and company filings, may contain errors or omissions, and are not a forecast or recommendation. Check the original sources.</p>
<p><b>Data accuracy and availability.</b> Data comes from third parties, may be delayed, incomplete or wrong, and may be unavailable at times. The service is provided "as is" without warranties, and to the extent permitted by law we are not liable for losses arising from its use.</p>
<p><b>Third-party content.</b> News items link to their original publishers; headlines and excerpts belong to their owners. Names and logos of third parties belong to them. Automatic translations may be inaccurate.</p>
<p><b>Acceptable use.</b> Do not scrape, overload, reverse-engineer or attack the service, or use it in violation of law. We may limit or block access to protect the service.</p>
<p><b>Changes.</b> We may change the service or these terms; continued use means you accept the updated terms.</p>
<p><b>Contact.</b> ${mail('en')}</p>`,
    },
    he: {
      short: 'תנאי שימוש',
      heading: 'תנאי שימוש',
      title: 'תנאי שימוש | Swing Desk',
      description: 'התנאים לשימוש ב-Swing Desk: האתר מספק מידע בלבד ולא ייעוץ השקעות; תוצאות עבר אינן מבטיחות תוצאות בעתיד; נתונים עשויים להתעכב או להיות שגויים.',
      body: `<p class="dim">עודכן לאחרונה: <bdi>${LEGAL_LAST_UPDATED}</bdi></p>
<p><b>מידע בלבד.</b> כל מה שמוצג באתר (מחירים, ציונים, תבניות גרף, סטטיסטיקות, תוצאות "תיק דמו" מדומה, לוח אירועים כלכלי וסיכומי AI) הוא מידע כללי להמחשה ולמטרות לימוד. הוא <b>אינו ייעוץ השקעות, המלצה להשקעה, שיווק השקעות או הצעה לקנות או למכור נייר ערך</b>, ואינו מביא בחשבון את מצבך האישי. איננו יועץ השקעות מורשה. לפני קבלת החלטות יש להיוועץ באיש מקצוע מורשה.</p>
<p><b>ביצועי עבר.</b> תוצאות היסטוריות ובדיקות עבר, כולל שיעורי הצלחה של תבניות ותיק הדמו, הן סימולציות עם הנחות מפשטות (ללא עמלות, החלקות ומסים) ואינן מבטיחות תוצאות בעתיד. מסחר כרוך בסיכון להפסד, לרבות אובדן מלוא ההשקעה.</p>
<p><b>תוכן AI.</b> סיכומי AI נוצרים אוטומטית מכותרות חדשות ומדיווחי חברות, עלולים להכיל טעויות או השמטות, ואינם תחזית או המלצה. יש לבדוק במקורות המקוריים.</p>
<p><b>דיוק וזמינות הנתונים.</b> הנתונים מגיעים מצדדים שלישיים, עשויים להיות מאוחרים, חלקיים או שגויים, ולעיתים לא זמינים. השירות ניתן "כמות שהוא" וללא אחריות, ובמידה המותרת בדין איננו אחראים להפסדים הנובעים משימוש בו.</p>
<p><b>תוכן צד שלישי.</b> פריטי חדשות מקשרים למו"לים המקוריים; הכותרות והקטעים שייכים לבעליהם. שמות ולוגואים של צדדים שלישיים שייכים להם. תרגומים אוטומטיים עשויים להיות לא מדויקים.</p>
<p><b>שימוש הולם.</b> אין לסרוק, להעמיס, לפרוץ לאחור או לתקוף את השירות, ואין להשתמש בו בניגוד לדין. אנו רשאים להגביל או לחסום גישה כדי להגן על השירות.</p>
<p><b>שינויים.</b> אנו עשויים לשנות את השירות או את התנאים; המשך שימוש מהווה הסכמה לתנאים המעודכנים.</p>
<p><b>יצירת קשר.</b> ${mail('he')}</p>`,
    },
  },
};

module.exports = { DISCLAIMER, PATTERNS, SECTOR_HE, POPULAR, GROUPS, ARTICLES, DOCS, PUBLISHED, LEGAL_CONTACT_EMAIL };
