// v35: the guides (/learn/...), part 1: where to start and the chart patterns. The rules under "The rules the scanner checks"
// are the code of pipeline_a_scanner.py in words: when a rule changes there, change it here too. Facts and definitions only.
const QUALITY_EN = 'Like most scans on the site, it is listed only when the stock also passes the quality rule: <a href="/learn/rs-rating">RS</a> of at least 90, or RS of at least 70 together with a <a href="/learn/stage-analysis">Stage 2</a> uptrend and <a href="/learn/atr">ATR</a> of at least 3%.';
const QUALITY_HE = 'כמו רוב הסריקות באתר, היא מוצגת רק כשהמניה עומדת גם בכלל האיכות: <a href="/he/learn/rs-rating">RS</a> של 90 לפחות, או RS של 70 לפחות יחד עם מגמת עלייה ב<a href="/he/learn/stage-analysis">שלב 2</a> ו-<a href="/he/learn/atr">ATR</a> של 3% לפחות.';
const VOLUME_EN = 'Volume on the day of the move must be at least 20% above the stock\'s 20-day average.';
const VOLUME_HE = 'נפח המסחר ביום התנועה חייב להיות גבוה ב-20% לפחות מהממוצע של 20 הימים האחרונים.';

module.exports = [
  {
    slug: 'what-is-swing-trading',
    group: 'basics',
    related: ['position-sizing', 'stage-analysis', 'rs-rating', 'cup-and-handle'],
    en: {
      short: 'What is swing trading',
      title: 'What Is Swing Trading? Time Frames, Tools and Risks',
      description: 'Swing trading means holding a stock for days to weeks to capture one price move. How it differs from day trading and investing, and what swing traders look at.',
      body: `<p class="lead">Swing trading is a style of trading that tries to capture one move in a stock's price, holding the position for a few days to a few weeks.</p>
<h2>How it differs from other styles</h2>
<ul>
<li><b>Day trading:</b> positions are opened and closed within the same session, so nothing is held overnight. A swing position is held overnight, which also means exposure to news and earnings reports that arrive while the market is closed.</li>
<li><b>Long-term investing:</b> the question is what a company will be worth years from now. A swing trader may look at the same company, but the question is about the next few weeks of its chart.</li>
</ul>
<h2>What swing traders usually look at</h2>
<ul>
<li><b>The trend.</b> Is the stock rising or falling over the past months? Moving averages and <a href="/learn/stage-analysis">stage analysis</a> are common ways to describe it.</li>
<li><b>A defined level.</b> Chart patterns such as the <a href="/learn/cup-and-handle">cup and handle</a> or a <a href="/learn/horizontal-resistance-breakout">horizontal resistance</a> mark a price where a move is considered to have started, and a price where the idea is considered wrong.</li>
<li><b>Strength and volatility.</b> <a href="/learn/rs-rating">Relative strength</a> compares a stock with the market, and <a href="/learn/atr">ATR</a> shows how far it typically moves in a day.</li>
<li><b>The calendar.</b> Earnings reports and economic data can move a stock more than any chart pattern. The <a href="/learn/expected-move-earnings">expected move</a> shows how much the options market is pricing in.</li>
</ul>
<h2>The risks</h2>
<p>Patterns fail often, and a stock can open far from its previous close, past any level that was planned. Fees, slippage and taxes reduce results, and frequent trading makes them matter more. Trading can lose money, including the whole amount invested. <a href="/learn/position-sizing">Position sizing</a> is how traders limit what one wrong trade costs, but it does not remove the risk.</p>
<h2>Where Swing Desk fits in</h2>
<p>Swing Desk automates the routine part: fixed rules go over hundreds of large US stocks during the trading day and list those where a pattern has just completed, next to the trend, the relative strength and the next earnings date. It does not say what anyone should do with that list.</p>`,
    },
    he: {
      short: 'מה זה מסחר סווינג',
      title: 'מה זה מסחר סווינג? טווחי זמן, כלים וסיכונים',
      description: 'מסחר סווינג הוא החזקת מניה לימים עד שבועות כדי לתפוס תנועת מחיר אחת. במה הוא שונה ממסחר יומי ומהשקעה ארוכת טווח, ועל מה סוחרי סווינג מסתכלים.',
      body: `<p class="lead">מסחר סווינג הוא סגנון מסחר שמנסה לתפוס תנועה אחת במחיר של מניה, בהחזקה של כמה ימים עד כמה שבועות.</p>
<h2>במה הוא שונה מסגנונות אחרים</h2>
<ul>
<li><b>מסחר יומי:</b> הפוזיציות נפתחות ונסגרות באותו יום מסחר, ושום דבר לא נשאר פתוח ללילה. פוזיציית סווינג כן נשארת פתוחה בלילה, ולכן חשופה גם לחדשות ולדוחות שמתפרסמים כשהשוק סגור.</li>
<li><b>השקעה ארוכת טווח:</b> השאלה היא כמה תהיה שווה החברה בעוד שנים. סוחר סווינג יכול להסתכל על אותה חברה, אבל השאלה שלו נוגעת לשבועות הקרובים בגרף.</li>
</ul>
<h2>על מה סוחרי סווינג מסתכלים בדרך כלל</h2>
<ul>
<li><b>המגמה.</b> האם המניה עלתה או ירדה בחודשים האחרונים? ממוצעים נעים ו<a href="/he/learn/stage-analysis">ניתוח שלבים</a> הם דרכים נפוצות לתאר זאת.</li>
<li><b>רמה מוגדרת.</b> תבניות כמו <a href="/he/learn/cup-and-handle">כוס וידית</a> או <a href="/he/learn/horizontal-resistance-breakout">התנגדות אופקית</a> מסמנות מחיר שבו התנועה נחשבת כמי שהתחילה, ומחיר שבו הרעיון נחשב שגוי.</li>
<li><b>עוצמה ותנודתיות.</b> <a href="/he/learn/rs-rating">עוצמה יחסית</a> משווה מניה לשוק, ו-<a href="/he/learn/atr">ATR</a> מראה כמה היא זזה בדרך כלל ביום.</li>
<li><b>לוח האירועים.</b> דוחות ונתונים כלכליים יכולים להזיז מניה יותר מכל תבנית גרף. <a href="/he/learn/expected-move-earnings">התנודה הצפויה</a> מראה כמה שוק האופציות מתמחר.</li>
</ul>
<h2>הסיכונים</h2>
<p>תבניות נכשלות לעיתים קרובות, ומניה יכולה להיפתח רחוק ממחיר הסגירה הקודם, מעבר לכל רמה שתוכננה. עמלות, החלקות ומסים מקטינים את התוצאות, ובמסחר תכוף הם משמעותיים יותר. מסחר עלול להפסיד כסף, עד כדי כל הסכום שהושקע. <a href="/he/learn/position-sizing">גודל פוזיציה</a> הוא הדרך שבה סוחרים מגבילים את העלות של עסקה שגויה אחת, אבל הוא לא מבטל את הסיכון.</p>
<h2>איפה Swing Desk נכנס לתמונה</h2>
<p>Swing Desk עושה אוטומטית את החלק השגרתי: כללים קבועים עוברים על מאות מניות אמריקאיות גדולות במהלך יום המסחר, ומציגים את המניות שבהן תבנית הושלמה זה עתה, לצד המגמה, העוצמה היחסית ומועד הדוח הבא. הוא לא אומר מה לעשות עם הרשימה הזאת.</p>`,
    },
  },
  {
    slug: 'position-sizing',
    group: 'basics',
    related: ['atr', 'what-is-swing-trading', 'expected-move-earnings'],
    en: {
      short: 'Position sizing',
      title: 'Position Sizing: How the Risk of a Single Trade Is Calculated',
      description: 'How many shares does a fixed risk allow? The formula behind position sizing, a worked example, and why a stop price is not a guaranteed exit.',
      body: `<p class="lead">Position sizing is the calculation of how many shares a trade can hold so that, if the stock reaches a chosen exit price, the loss stays within a chosen amount.</p>
<h2>The calculation</h2>
<p>It takes four numbers: the account size, the share of the account allowed to be at risk in one trade, the entry price and the exit price (the stop).</p>
<ul>
<li>Amount at risk = account size × risk percentage</li>
<li>Risk per share = entry price − stop price</li>
<li>Number of shares = amount at risk ÷ risk per share</li>
</ul>
<h2>A worked example</h2>
<p>These numbers only show the arithmetic. An account of $20,000 with 1% at risk gives $200. With an entry at $50 and a stop at $47, the risk per share is $3, so $200 ÷ $3 gives 66 shares, a position worth $3,300. The same $200 with a stop at $49 would allow 200 shares, a position worth $10,000. The closer the stop, the larger the position for the same risk.</p>
<h2>Why volatility matters</h2>
<p>A stop placed closer than the stock's usual daily range is more likely to be reached by ordinary movement. <a href="/learn/atr">ATR</a> is a common way to see that range. A stock with a 5% ATR and a 2% stop can reach the stop on a normal day.</p>
<h2>What this does not cover</h2>
<ul>
<li><b>Gaps.</b> A stop is a price, not a guarantee. After an earnings report or overnight news a stock can open far below the stop, and the loss is then larger than planned.</li>
<li><b>Several positions at once.</b> Stocks in the same sector often fall together, so the risk across positions adds up.</li>
<li><b>Fees, slippage and taxes</b> are not part of the formula.</li>
</ul>
<h2>On Swing Desk</h2>
<p>The site has a position size calculator in the details of each scan and on each stock page that does this arithmetic. It starts from the current price and a nearby technical level, and every number can be changed. These are starting points for the arithmetic, not suggested prices. The account size and risk percentage are stored only in your browser.</p>`,
    },
    he: {
      short: 'גודל פוזיציה',
      title: 'גודל פוזיציה: איך מחשבים את הסיכון בעסקה אחת',
      description: 'כמה מניות מתאימות לסיכון קבוע? הנוסחה שמאחורי חישוב גודל פוזיציה, דוגמה מספרית, ולמה מחיר סטופ אינו יציאה מובטחת.',
      body: `<p class="lead">חישוב גודל פוזיציה קובע כמה מניות יכולה להחזיק עסקה, כך שאם המניה תגיע למחיר היציאה שנבחר, ההפסד יישאר בתוך סכום שנבחר.</p>
<h2>החישוב</h2>
<p>צריך ארבעה מספרים: גודל החשבון, החלק מהחשבון שמותר לסכן בעסקה אחת, מחיר הכניסה ומחיר היציאה (הסטופ).</p>
<ul>
<li>הסכום בסיכון = גודל החשבון × אחוז הסיכון</li>
<li>הסיכון למניה = מחיר הכניסה − מחיר הסטופ</li>
<li>מספר המניות = הסכום בסיכון ÷ הסיכון למניה</li>
</ul>
<h2>דוגמה מספרית</h2>
<p>המספרים כאן רק מדגימים את החשבון. חשבון של 20,000 דולר עם סיכון של 1% נותן 200 דולר. עם כניסה ב-50 דולר וסטופ ב-47 דולר, הסיכון למניה הוא 3 דולר, ולכן 200 ÷ 3 נותן 66 מניות, פוזיציה בשווי 3,300 דולר. אותם 200 דולר עם סטופ ב-49 דולר יאפשרו 200 מניות, פוזיציה בשווי 10,000 דולר. ככל שהסטופ קרוב יותר, הפוזיציה גדולה יותר עבור אותו סיכון.</p>
<h2>למה התנודתיות חשובה</h2>
<p>סטופ שקרוב יותר מהטווח היומי הרגיל של המניה יושג ביתר קלות על ידי תנועה רגילה. <a href="/he/learn/atr">ATR</a> היא דרך נפוצה לראות את הטווח הזה. מניה עם ATR של 5% וסטופ של 2% יכולה להגיע לסטופ ביום רגיל.</p>
<h2>מה זה לא מכסה</h2>
<ul>
<li><b>פערים.</b> סטופ הוא מחיר, לא התחייבות. אחרי דוח או חדשות בלילה מניה יכולה להיפתח הרבה מתחת לסטופ, ואז ההפסד גדול מהמתוכנן.</li>
<li><b>כמה פוזיציות יחד.</b> מניות מאותו סקטור יורדות לעיתים קרובות יחד, ולכן הסיכון בין הפוזיציות מצטבר.</li>
<li><b>עמלות, החלקות ומסים</b> אינם חלק מהנוסחה.</li>
</ul>
<h2>ב-Swing Desk</h2>
<p>באתר יש מחשבון גודל פוזיציה בפרטי כל סריקה ובכל עמוד מניה שעושה את החשבון הזה. הוא מתחיל מהמחיר הנוכחי ומרמה טכנית קרובה, ואפשר לשנות כל מספר. אלה נקודות התחלה לחשבון, לא מחירים מוצעים. גודל החשבון ואחוז הסיכון נשמרים רק בדפדפן.</p>`,
    },
  },
  {
    slug: 'cup-and-handle',
    group: 'patterns',
    related: ['52-week-high-breakout', 'horizontal-resistance-breakout', 'bull-flag', 'rs-rating'],
    en: {
      short: 'Cup and handle',
      title: 'Cup and Handle Pattern: What It Looks Like and How It Is Detected',
      description: 'A rounded decline and recovery back to the old high, then a small pullback. The cup and handle pattern, and the exact rules the Swing Desk scanner checks.',
      body: `<p class="lead">The cup and handle is a chart pattern in which a stock falls from a high, curves back up to about the same level, pulls back a little, and then closes above that level.</p>
<h2>What it looks like</h2>
<p>The <b>cup</b> is a rounded U shape: a gradual decline, a bottom that takes time, and a gradual recovery. A sharp V is usually not counted, because a rounded bottom suggests that selling ran out slowly. The <b>handle</b> is a short, shallow pullback near the top of the right side. The top of the cup, called the rim, is the level everyone watches.</p>
<h2>Why traders watch it</h2>
<p>The rim is a price where the stock turned down before, so people who bought near it may be waiting to get out at no loss. The pattern is often read this way: the recovery and the handle absorb that supply, and a close above the rim shows it has run out. That is an interpretation, not a law.</p>
<h2>The rules the scanner checks</h2>
<ul>
<li>It searches the last 130 trading days for a rim, a decline and a recovery.</li>
<li>The decline from the rim is between 12% and 35%. The right side recovers to within 5% of the rim.</li>
<li>The whole cup lasts between 20 and 110 trading days, and its low sits in the middle part of that span (between 20% and 80%), with a rounded bottom.</li>
<li>The breakout is fresh: no more than 5 trading days old and no more than 8% above the rim.</li>
<li>A <b>handle</b> is a pullback of at least 2% and no more than half the cup's depth, lasting up to 25 trading days. Without one, the same breakout is listed as "Cup (No Handle)", with a lower score.</li>
</ul>
<p>Volume is shown but not required for this pattern, because the level itself has already been tested. ${QUALITY_EN}</p>
<h2>What it does not tell you</h2>
<p>A breakout can reverse within days. The pattern says nothing about the company's next earnings report, and a report in the coming days can overwhelm the chart. The site's measured results for each pattern are on the statistics page.</p>`,
    },
    he: {
      short: 'כוס וידית',
      title: 'תבנית כוס וידית: איך היא נראית ואיך מזהים אותה',
      description: 'ירידה מעוגלת וחזרה לשיא הישן, ואז תיקון קטן. תבנית הכוס והידית, והכללים המדויקים שהסורק של Swing Desk בודק.',
      body: `<p class="lead">כוס וידית היא תבנית גרף שבה מניה יורדת משיא, מתעגלת חזרה לערך לאותה רמה, מתקנת מעט, ואז נסגרת מעל הרמה הזאת.</p>
<h2>איך היא נראית</h2>
<p>ה<b>כוס</b> היא צורת U מעוגלת: ירידה הדרגתית, תחתית שלוקחת זמן, והתאוששות הדרגתית. צורת V חדה בדרך כלל לא נחשבת, כי תחתית מעוגלת מרמזת שהמכירות נגמרו לאט. ה<b>ידית</b> היא תיקון קצר ורדוד ליד ראש הצד הימני. ראש הכוס, שנקרא השפה, הוא הרמה שכולם מסתכלים עליה.</p>
<h2>למה סוחרים מסתכלים עליה</h2>
<p>השפה היא מחיר שבו המניה כבר הסתובבה כלפי מטה, ולכן מי שקנה בסביבתו עשוי לחכות לצאת בלי הפסד. פירוש נפוץ של התבנית: ההתאוששות והידית סופגות את ההיצע הזה, וסגירה מעל השפה מראה שהוא נגמר. זה פירוש, לא חוק טבע.</p>
<h2>הכללים שהסורק בודק</h2>
<ul>
<li>הוא מחפש ב-130 ימי המסחר האחרונים שפה, ירידה והתאוששות.</li>
<li>הירידה מהשפה היא בין 12% ל-35%. הצד הימני מתאושש עד מרחק של 5% לכל היותר מהשפה.</li>
<li>הכוס כולה נמשכת בין 20 ל-110 ימי מסחר, והשפל שלה נמצא בחלק האמצעי של התקופה (בין 20% ל-80%), עם תחתית מעוגלת.</li>
<li>הפריצה טרייה: לא יותר מ-5 ימי מסחר מאז, ולא יותר מ-8% מעל השפה.</li>
<li><b>ידית</b> היא תיקון של 2% לפחות ולא יותר ממחצית עומק הכוס, שנמשך עד 25 ימי מסחר. בלעדיה אותה פריצה מוצגת כ"כוס ללא ידית", עם ציון נמוך יותר.</li>
</ul>
<p>נפח המסחר מוצג אבל אינו תנאי בתבנית הזאת, כי הרמה עצמה כבר נבחנה. ${QUALITY_HE}</p>
<h2>מה היא לא אומרת</h2>
<p>פריצה יכולה להתהפך תוך ימים. התבנית לא אומרת דבר על הדוח הבא של החברה, ודוח בימים הקרובים יכול לגבור על הגרף. התוצאות המדודות של כל תבנית באתר נמצאות בעמוד הסטטיסטיקה.</p>`,
    },
  },
  {
    slug: 'double-bottom',
    group: 'patterns',
    related: ['horizontal-support-bounce', 'cup-and-handle', 'rsi-oversold-bounce'],
    en: {
      short: 'Double bottom',
      title: 'Double Bottom Pattern: Two Lows and a Neckline',
      description: 'Two lows at about the same price with a rally between them. What the double bottom pattern is, what the neckline means, and the rules the Swing Desk scanner checks.',
      body: `<p class="lead">A double bottom is a chart pattern with two lows at about the same price and a rally between them. It completes when the stock closes above the high of that rally, the level known as the neckline.</p>
<h2>What it looks like</h2>
<p>The stock falls to a low, rises, falls back to about the same low, and rises again. On the chart it looks like the letter W. The second visit to the low is the test: the price where buyers appeared before is reached again, and the stock turns up again.</p>
<h2>Why traders watch it</h2>
<p>Two turns at one price suggest that demand exists at that level. The neckline matters because until the stock closes above it, the pattern is only two lows, and the decline could still continue.</p>
<h2>The rules the scanner checks</h2>
<ul>
<li>It searches the last 60 trading days for two swing lows within 3% of each other, at least 8 trading days apart.</li>
<li>The neckline, the highest point between them, is at least 8% above the lows. A smaller bounce is considered noise.</li>
<li>Today is the first close above the neckline. A stock already above it is no longer listed, because the move has already started.</li>
<li>${VOLUME_EN}</li>
</ul>
<p>${QUALITY_EN}</p>
<h2>What it does not tell you</h2>
<p>Two lows are not proof of a bottom: a stock in a long decline can form this shape and go on to new lows afterwards, so the broader <a href="/learn/stage-analysis">trend</a> is usually read next to it. The first close above the neckline is also often retested from above, and the pattern alone does not say whether that test will hold.</p>`,
    },
    he: {
      short: 'תחתית כפולה',
      title: 'תבנית תחתית כפולה: שני שפלים וקו צוואר',
      description: 'שני שפלים באותו מחיר בערך, ועלייה ביניהם. מהי תחתית כפולה, מה המשמעות של קו הצוואר, ואילו כללים בודק הסורק של Swing Desk.',
      body: `<p class="lead">תחתית כפולה היא תבנית גרף עם שני שפלים באותו מחיר בערך ועלייה ביניהם. היא מושלמת כשהמניה נסגרת מעל השיא של העלייה הזאת, הרמה שנקראת קו הצוואר.</p>
<h2>איך היא נראית</h2>
<p>המניה יורדת לשפל, עולה, יורדת שוב לערך לאותו שפל, ועולה שוב. בגרף זה נראה כמו האות W. הביקור השני בשפל הוא המבחן: המחיר שבו הופיעו קונים בפעם הקודמת מושג שוב, והמניה מסתובבת שוב כלפי מעלה.</p>
<h2>למה סוחרים מסתכלים עליה</h2>
<p>שתי תפניות באותו מחיר מרמזות שיש ביקוש ברמה הזאת. קו הצוואר חשוב כי עד שהמניה נסגרת מעליו, התבנית היא רק שני שפלים, והירידה עדיין יכולה להימשך.</p>
<h2>הכללים שהסורק בודק</h2>
<ul>
<li>הוא מחפש ב-60 ימי המסחר האחרונים שני שפלים מקומיים במרחק של עד 3% זה מזה, ולפחות 8 ימי מסחר ביניהם.</li>
<li>קו הצוואר, הנקודה הגבוהה ביניהם, גבוה ב-8% לפחות מהשפלים. עלייה קטנה יותר נחשבת רעש.</li>
<li>היום הוא הסגירה הראשונה מעל קו הצוואר. מניה שכבר נמצאת מעליו לא מוצגת יותר, כי התנועה כבר התחילה.</li>
<li>${VOLUME_HE}</li>
</ul>
<p>${QUALITY_HE}</p>
<h2>מה היא לא אומרת</h2>
<p>שני שפלים אינם הוכחה לתחתית: מניה בירידה ארוכה יכולה ליצור את הצורה הזאת ולהמשיך אחר כך לשפלים חדשים, ולכן בדרך כלל קוראים לידה גם את <a href="/he/learn/stage-analysis">המגמה</a> הרחבה. גם הסגירה הראשונה מעל קו הצוואר נבחנת לעיתים קרובות מלמעלה, והתבנית לבדה לא אומרת אם המבחן יעמוד.</p>`,
    },
  },
  {
    slug: '52-week-high-breakout',
    group: 'patterns',
    related: ['rs-rating', 'horizontal-resistance-breakout', 'cup-and-handle', 'market-breadth'],
    en: {
      short: '52-week high breakout',
      title: '52-Week High Breakout: What a New One-Year High Means',
      description: 'A stock closing above its highest price of the past year. What a 52-week high breakout is, why it gets attention, and the rule the Swing Desk scanner uses.',
      body: `<p class="lead">A 52-week high breakout happens when a stock closes above the highest price it has traded at during the past year.</p>
<h2>What it means on the chart</h2>
<p>At a new one-year high, every person who bought during that year is showing a profit on the position, so nobody from that period is waiting to sell at break-even. There is no recent resistance above the price to measure against. That is why the level attracts attention, and why many data services publish daily lists of stocks making new highs.</p>
<h2>Why it can look uncomfortable</h2>
<p>A stock at a new high looks expensive compared with its own past, and it is natural to expect a pullback. Price alone says nothing about value: a high can be justified by earnings growth, or it can be the top of a move. The number of stocks making new highs across the market is itself a measure of <a href="/learn/market-breadth">market breadth</a>.</p>
<h2>The rules the scanner checks</h2>
<ul>
<li>Today's close is above the highest intraday high of the previous 251 trading days, which is about one year. Prices are not adjusted for dividends, the same way a regular chart shows them.</li>
<li>${VOLUME_EN}</li>
</ul>
<p>${QUALITY_EN}</p>
<h2>What it does not tell you</h2>
<p>A breakout on one day does not show whether it will hold. If the stock has already risen far above its moving averages, a normal pullback can be large. It is common to read the breakout next to <a href="/learn/rs-rating">relative strength</a> and the date of the next earnings report.</p>`,
    },
    he: {
      short: 'פריצת שיא 52 שבועות',
      title: 'פריצת שיא 52 שבועות: מה אומר שיא שנתי חדש',
      description: 'מניה שנסגרת מעל המחיר הגבוה ביותר שלה בשנה האחרונה. מהי פריצת שיא 52 שבועות, למה היא מושכת תשומת לב, ומה הכלל שהסורק של Swing Desk בודק.',
      body: `<p class="lead">פריצת שיא 52 שבועות קורית כשמניה נסגרת מעל המחיר הגבוה ביותר שבו נסחרה בשנה האחרונה.</p>
<h2>מה זה אומר בגרף</h2>
<p>בשיא שנתי חדש, כל מי שקנה במהלך השנה הזאת נמצא ברווח על הפוזיציה, ולכן אף אחד מאותה תקופה לא מחכה למכור בלי הפסד. אין התנגדות קרובה מעל המחיר שאפשר להימדד מולה. לכן הרמה מושכת תשומת לב, ושירותי נתונים רבים מפרסמים רשימות יומיות של מניות בשיא חדש.</p>
<h2>למה זה יכול להיראות לא נוח</h2>
<p>מניה בשיא חדש נראית יקרה ביחס לעבר של עצמה, וטבעי לצפות לתיקון. מחיר לבדו לא אומר דבר על שווי: שיא יכול לנבוע מצמיחה ברווחים, והוא יכול גם להיות סוף של תנועה. מספר המניות בשיא חדש בכל השוק הוא בעצמו מדד ל<a href="/he/learn/market-breadth">רוחב השוק</a>.</p>
<h2>הכללים שהסורק בודק</h2>
<ul>
<li>מחיר הסגירה היום גבוה מהשיא התוך-יומי של 251 ימי המסחר הקודמים, כשנה. המחירים לא מתואמים לדיבידנד, כפי שהם מוצגים בגרף רגיל.</li>
<li>${VOLUME_HE}</li>
</ul>
<p>${QUALITY_HE}</p>
<h2>מה היא לא אומרת</h2>
<p>פריצה ביום אחד לא מראה אם היא תחזיק. אם המניה כבר התרחקה מאוד מהממוצעים הנעים שלה, גם תיקון רגיל יכול להיות גדול. מקובל לקרוא את הפריצה לצד <a href="/he/learn/rs-rating">העוצמה היחסית</a> ומועד הדוח הבא.</p>`,
    },
  },
  {
    slug: 'horizontal-resistance-breakout',
    group: 'patterns',
    related: ['horizontal-support-bounce', '52-week-high-breakout', 'ascending-triangle'],
    en: {
      short: 'Horizontal resistance breakout',
      title: 'Horizontal Resistance Breakout: When a Tested Level Gives Way',
      description: 'A level that stopped a stock at least twice, and the first close above it. What horizontal resistance is and the rules the Swing Desk scanner checks.',
      body: `<p class="lead">A horizontal resistance is a price where a stock has stopped rising more than once. A breakout is the first close clearly above that price.</p>
<h2>Why a level forms</h2>
<p>Each time a stock turns down from about the same price, more people remember that price: those who bought there and want to get out, and those who sold there and expect it to work again. The more often a level is tested, the more visible it is. Once price closes above it, the same level is often watched from the other side as support.</p>
<h2>The rules the scanner checks</h2>
<ul>
<li>It looks at the last 150 trading days for at least 2 swing highs within 2% of each other. Together they define the level.</li>
<li>Today's close is at least 1% above the level. A smaller margin is too easily reversed by the next day's noise.</li>
<li>It is the first close above the level since the last time the stock touched it. Later closes are not listed again, so a stock that broke out weeks ago does not keep appearing.</li>
</ul>
<p>A high volume day is not required here, since the level has been tested already, but the volume is shown next to the scan. ${QUALITY_EN}</p>
<h2>On the stock page</h2>
<p>The chart on the stock page draws the level at the tip of the wick from its first touch, so it can be compared with what a person would draw by hand. The technical setup section also notes when a stock is close to a level above it but has not crossed yet.</p>
<h2>What it does not tell you</h2>
<p>A close above a level can fail the next day, and failed breakouts are common. The rule looks only at price: it does not know why the level held before, or whether something in the company has changed.</p>`,
    },
    he: {
      short: 'פריצת התנגדות אופקית',
      title: 'פריצת התנגדות אופקית: כשרמה שנבחנה נשברת',
      description: 'רמה שעצרה מניה פעמיים לפחות, והסגירה הראשונה מעליה. מהי התנגדות אופקית, ואילו כללים בודק הסורק של Swing Desk.',
      body: `<p class="lead">התנגדות אופקית היא מחיר שבו מניה הפסיקה לעלות יותר מפעם אחת. פריצה היא הסגירה הראשונה מעל המחיר הזה, במרחק ברור ממנו.</p>
<h2>איך נוצרת רמה</h2>
<p>בכל פעם שמניה מסתובבת כלפי מטה מאותו מחיר בערך, יותר אנשים זוכרים אותו: מי שקנה שם ורוצה לצאת, ומי שמכר שם ומצפה שזה יעבוד שוב. ככל שרמה נבחנת יותר פעמים, היא בולטת יותר. אחרי שהמחיר נסגר מעליה, עוקבים לעיתים קרובות אחרי אותה רמה מהצד השני, כתמיכה.</p>
<h2>הכללים שהסורק בודק</h2>
<ul>
<li>הוא מסתכל על 150 ימי המסחר האחרונים ומחפש לפחות 2 שיאים מקומיים במרחק של עד 2% זה מזה. יחד הם מגדירים את הרמה.</li>
<li>מחיר הסגירה היום גבוה ב-1% לפחות מהרמה. פער קטן יותר מתהפך בקלות מרעש של היום הבא.</li>
<li>זו הסגירה הראשונה מעל הרמה מאז הפעם האחרונה שהמניה נגעה בה. סגירות מאוחרות יותר לא מוצגות שוב, ולכן מניה שפרצה לפני שבועות לא ממשיכה להופיע.</li>
</ul>
<p>כאן לא נדרש נפח מסחר גבוה, כי הרמה כבר נבחנה, אבל הנפח מוצג לצד הסריקה. ${QUALITY_HE}</p>
<h2>בעמוד המניה</h2>
<p>הגרף בעמוד המניה מצייר את הרמה בקצה הפתיל של הנגיעה הראשונה, כך שאפשר להשוות אותה למה שאדם היה מצייר ביד. גם החלק של המבנה הטכני מציין כשמניה קרובה לרמה שמעליה אבל עדיין לא חצתה אותה.</p>
<h2>מה היא לא אומרת</h2>
<p>סגירה מעל רמה יכולה להיכשל למחרת, ופריצות שנכשלות הן דבר נפוץ. הכלל מסתכל רק על המחיר: הוא לא יודע למה הרמה החזיקה קודם, או אם משהו בחברה השתנה.</p>`,
    },
  },
  {
    slug: 'golden-cross',
    group: 'patterns',
    related: ['150-day-moving-average', 'stage-analysis', 'macd-crossover'],
    en: {
      short: 'Golden cross',
      title: 'Golden Cross: When the 50-Day Average Crosses the 200-Day',
      description: 'The 50-day moving average crossing above the 200-day. What a golden cross measures, why it lags, and the rule the Swing Desk scanner uses.',
      body: `<p class="lead">A golden cross is the moment a stock's 50-day moving average crosses above its 200-day moving average. Its opposite, the 50-day crossing below, is called a death cross.</p>
<h2>What it measures</h2>
<p>A moving average is the mean closing price over a set number of days. The 200-day describes roughly the past year, and the 50-day the past ten weeks. When the shorter one rises above the longer one, recent prices have become higher than the prices of the past year on average. It describes a change in the trend that has already happened.</p>
<h2>Why it lags</h2>
<p>Both lines are built from past prices, so by the time they cross, the stock has usually already risen well above its low. In a sideways market the lines can cross back and forth with no lasting trend. That is why a golden cross is read as a description of the trend's state rather than as a timing tool.</p>
<h2>The rules the scanner checks</h2>
<ul>
<li>The 50-day simple moving average crossed above the 200-day simple moving average within the last 3 trading days.</li>
<li>No volume condition: the cross comes from months of prices, not from one day.</li>
</ul>
<p>${QUALITY_EN}</p>
<h2>What it does not tell you</h2>
<p>The cross does not tell how far the price is above the averages, or whether the 200-day itself is rising. A cross with a falling 200-day line can come from a short bounce inside a longer decline. The <a href="/learn/stage-analysis">trend stage</a> shown next to each stock adds that part: it requires the 150-day average to be rising and the averages to be stacked in order.</p>`,
    },
    he: {
      short: 'גולדן קרוס',
      title: 'גולדן קרוס: כשממוצע 50 יום חוצה את ממוצע 200 יום',
      description: 'ממוצע נע של 50 יום שחוצה מעל ממוצע 200 יום. מה מודד גולדן קרוס, למה הוא מאחר, ומה הכלל שהסורק של Swing Desk בודק.',
      body: `<p class="lead">גולדן קרוס הוא הרגע שבו הממוצע הנע של 50 יום של מניה חוצה מעל הממוצע הנע של 200 יום. ההפך, כשממוצע 50 חוצה מתחת, נקרא דת' קרוס.</p>
<h2>מה הוא מודד</h2>
<p>ממוצע נע הוא ממוצע מחירי הסגירה על פני מספר ימים קבוע. ממוצע 200 מתאר בערך את השנה האחרונה, וממוצע 50 את עשרת השבועות האחרונים. כשהקצר עולה מעל הארוך, המחירים האחרונים גבוהים בממוצע ממחירי השנה האחרונה. זה תיאור של שינוי מגמה שכבר קרה.</p>
<h2>למה הוא מאחר</h2>
<p>שני הקווים בנויים ממחירי עבר, ולכן עד שהם נחצים המניה כבר עלתה בדרך כלל הרבה מעל השפל. בשוק שנע הצידה הקווים יכולים להיחצות הלוך ושוב בלי מגמה מתמשכת. לכן קוראים גולדן קרוס כתיאור של מצב המגמה, ולא ככלי לתזמון.</p>
<h2>הכללים שהסורק בודק</h2>
<ul>
<li>הממוצע הנע הפשוט של 50 יום חצה מעל הממוצע הנע הפשוט של 200 יום ב-3 ימי המסחר האחרונים.</li>
<li>אין תנאי של נפח מסחר: החצייה נובעת מחודשים של מחירים, לא מיום אחד.</li>
</ul>
<p>${QUALITY_HE}</p>
<h2>מה הוא לא אומר</h2>
<p>החצייה לא אומרת כמה רחוק המחיר מעל הממוצעים, או אם ממוצע 200 עצמו עולה. חצייה עם ממוצע 200 יורד יכולה לנבוע מקפיצה קצרה בתוך ירידה ארוכה. <a href="/he/learn/stage-analysis">שלב המגמה</a> שמוצג ליד כל מניה מוסיף את החלק הזה: הוא דורש שממוצע 150 יעלה ושהממוצעים יהיו מסודרים לפי הסדר.</p>`,
    },
  },
  {
    slug: '150-day-moving-average',
    group: 'patterns',
    related: ['stage-analysis', 'golden-cross', 'atr', 'ascending-trendline-support'],
    en: {
      short: '150-day moving average',
      title: '150-Day Moving Average: Support Bounces and Breakouts',
      description: 'Why traders watch the 150-day moving average, what a bounce and a reclaim of it mean, and the two sets of rules the Swing Desk scanner uses.',
      body: `<p class="lead">The 150-day moving average is the mean closing price over the last 150 trading days, about seven months. It is a common line for telling a longer uptrend from a longer decline.</p>
<h2>Why this line</h2>
<p>It sits between the faster 50-day and the slower 200-day. The method known as <a href="/learn/stage-analysis">stage analysis</a> was first described with a 30-week average, which is about the same length. A stock trading above a rising 150-day line is generally described as being in an uptrend. On every chart on Swing Desk this line is drawn in the same blue.</p>
<h2>Two things the scanner looks for</h2>
<h3>A bounce from the line (150-Day MA Support Bounce)</h3>
<ul>
<li>The average is rising over the last 20 trading days.</li>
<li>Today's low is within 3% of the average, and the close is above it.</li>
<li>At least 70% of the previous 10 closes were above the average, so this is a pullback in a stock that has been above the line, not a stock crossing it for the first time.</li>
<li>The stock's <a href="/learn/atr">ATR</a> is at least 2.5%. This setup has its own quality rule: in the site's own history, quieter stocks did noticeably worse.</li>
</ul>
<h3>A move back above the line (150-Day MA Breakout)</h3>
<ul>
<li>The stock closes above the average after closing below it the day before, with volume at least 20% above its 20-day average.</li>
<li>If volume on that day was lighter, the stock is listed only if the next day's close is also above the average. Two closes stand in for the volume.</li>
</ul>
<h2>What it does not tell you</h2>
<p>A moving average is an average of past prices, not a floor. Many stocks go straight through it, especially around an earnings report or a sharp market decline. A move back above the line says only that the price has recovered above its seven-month mean. It does not say the decline that came before it has ended.</p>`,
    },
    he: {
      short: 'ממוצע נע 150 יום',
      title: 'ממוצע נע 150 יום: ריבאונד מהממוצע ופריצה מעליו',
      description: 'למה סוחרים עוקבים אחרי ממוצע 150 יום, מה המשמעות של ריבאונד ממנו ושל חזרה מעליו, ושתי קבוצות הכללים שהסורק של Swing Desk בודק.',
      body: `<p class="lead">הממוצע הנע של 150 יום הוא ממוצע מחירי הסגירה ב-150 ימי המסחר האחרונים, כשבעה חודשים. זה קו נפוץ להבחנה בין מגמת עלייה ארוכה לירידה ארוכה.</p>
<h2>למה דווקא הקו הזה</h2>
<p>הוא נמצא בין ממוצע 50 המהיר לממוצע 200 האיטי. השיטה שנקראת <a href="/he/learn/stage-analysis">ניתוח שלבים</a> תוארה במקור עם ממוצע של 30 שבועות, שהוא בערך אותו אורך. מניה שנסחרת מעל קו 150 עולה מתוארת בדרך כלל כמי שנמצאת במגמת עלייה. בכל הגרפים ב-Swing Desk הקו הזה מצויר באותו צבע כחול.</p>
<h2>שני דברים שהסורק מחפש</h2>
<h3>ריבאונד מהקו (תמיכת ממוצע 150 יום)</h3>
<ul>
<li>הממוצע עולה ב-20 ימי המסחר האחרונים.</li>
<li>השפל של היום במרחק של עד 3% מהממוצע, ומחיר הסגירה מעליו.</li>
<li>לפחות 70% מ-10 הסגירות הקודמות היו מעל הממוצע, כלומר זה תיקון במניה שהייתה מעל הקו, ולא מניה שחוצה אותו בפעם הראשונה.</li>
<li>ה-<a href="/he/learn/atr">ATR</a> של המניה הוא 2.5% לפחות. למבנה הזה יש כלל איכות משלו: בהיסטוריה של האתר, מניות שקטות יותר הציגו תוצאות נמוכות באופן ניכר.</li>
</ul>
<h3>חזרה מעל הקו (פריצת ממוצע 150 יום)</h3>
<ul>
<li>המניה נסגרת מעל הממוצע אחרי שנסגרה מתחתיו יום קודם, עם נפח מסחר גבוה ב-20% לפחות מהממוצע של 20 יום.</li>
<li>אם הנפח באותו יום היה נמוך יותר, המניה מוצגת רק אם גם הסגירה של היום הבא מעל הממוצע. שתי סגירות מחליפות את הנפח.</li>
</ul>
<h2>מה הוא לא אומר</h2>
<p>ממוצע נע הוא ממוצע של מחירי עבר, לא רצפה. מניות רבות עוברות דרכו ישר, בעיקר סביב דוח או ירידה חדה בשוק. חזרה מעל הקו אומרת רק שהמחיר התאושש מעל הממוצע של שבעת החודשים. היא לא אומרת שהירידה שקדמה לה הסתיימה.</p>`,
    },
  },
  {
    slug: 'bull-flag',
    group: 'patterns',
    related: ['momentum-surge', 'ascending-triangle', 'cup-and-handle'],
    en: {
      short: 'Bull flag',
      title: 'Bull Flag Pattern: A Sharp Rise and a Shallow Pause',
      description: 'A fast advance followed by a small, orderly pullback. What a bull flag is, what the pole and flag mean, and the rules the Swing Desk scanner checks.',
      body: `<p class="lead">A bull flag is a chart pattern made of a sharp rise, called the pole, and a short and shallow pullback, called the flag. It completes when the stock starts rising again.</p>
<h2>What it looks like</h2>
<p>The pole is a steep advance over days or weeks. After it, the stock drifts sideways or slightly down in a narrow range, usually on less volume than during the advance. On a chart this looks like a flag on a pole.</p>
<h2>Why traders watch it</h2>
<p>A shallow pause after a strong rise suggests that few holders are in a hurry to sell, and that the stock is digesting the move rather than reversing it. A deep pullback gives up the advance, and the shape is no longer described as a flag.</p>
<h2>The rules the scanner checks</h2>
<ul>
<li><b>The pole:</b> a rise of at least 8% over 5 to 15 trading days, somewhere within the last 50 trading days.</li>
<li><b>The flag:</b> at least 4 trading days after the top of the pole, and the pullback gives back less than half of the pole.</li>
<li><b>The breakout:</b> today is the first close at or above every close since the top of the pole. Only that first day is listed.</li>
<li>${VOLUME_EN}</li>
</ul>
<p>${QUALITY_EN}</p>
<h2>What it does not tell you</h2>
<p>After a strong rise a stock has already moved a long way from its averages, so the distance to any support below it can be large. A rise of the pole's size can also come from one piece of news, which the rule cannot tell apart from steady buying. Checking whether the pole began with an earnings report is a common next step.</p>`,
    },
    he: {
      short: 'דגל שורי',
      title: 'תבנית דגל שורי: עלייה חדה ועצירה רדודה',
      description: 'עלייה מהירה ואחריה תיקון קטן ומסודר. מהו דגל שורי, מה המשמעות של העמוד והדגל, ואילו כללים בודק הסורק של Swing Desk.',
      body: `<p class="lead">דגל שורי הוא תבנית גרף שמורכבת מעלייה חדה, שנקראת העמוד, ומתיקון קצר ורדוד, שנקרא הדגל. היא מושלמת כשהמניה מתחילה לעלות שוב.</p>
<h2>איך היא נראית</h2>
<p>העמוד הוא עלייה תלולה לאורך ימים או שבועות. אחריו המניה נעה הצידה או מעט כלפי מטה בטווח צר, בדרך כלל בנפח מסחר נמוך מזה שבעלייה. בגרף זה נראה כמו דגל על עמוד.</p>
<h2>למה סוחרים מסתכלים עליה</h2>
<p>עצירה רדודה אחרי עלייה חזקה מרמזת שמעטים ממהרים למכור, ושהמניה מעכלת את התנועה ולא הופכת אותה. תיקון עמוק מחזיר את העלייה, ואז הצורה כבר לא מתוארת כדגל.</p>
<h2>הכללים שהסורק בודק</h2>
<ul>
<li><b>העמוד:</b> עלייה של 8% לפחות על פני 5 עד 15 ימי מסחר, בתוך 50 ימי המסחר האחרונים.</li>
<li><b>הדגל:</b> לפחות 4 ימי מסחר אחרי ראש העמוד, והתיקון מחזיר פחות ממחצית העמוד.</li>
<li><b>הפריצה:</b> היום הוא הסגירה הראשונה שגבוהה או שווה לכל הסגירות מאז ראש העמוד. רק היום הראשון הזה מוצג.</li>
<li>${VOLUME_HE}</li>
</ul>
<p>${QUALITY_HE}</p>
<h2>מה היא לא אומרת</h2>
<p>אחרי עלייה חזקה המניה כבר התרחקה מאוד מהממוצעים שלה, ולכן המרחק לתמיכה שמתחתיה יכול להיות גדול. עלייה בגודל של העמוד יכולה גם לנבוע מידיעה אחת, והכלל לא יודע להבדיל בינה לבין קנייה רציפה. בדיקה אם העמוד התחיל מדוח היא צעד מקובל בהמשך.</p>`,
    },
  },
  {
    slug: 'ascending-triangle',
    group: 'patterns',
    related: ['horizontal-resistance-breakout', 'bull-flag', 'ascending-trendline-support'],
    en: {
      short: 'Ascending triangle',
      title: 'Ascending Triangle: A Flat Top and Rising Lows',
      description: 'A level the stock keeps stopping at, with each pullback ending higher. What an ascending triangle is and the rules the Swing Desk scanner checks.',
      body: `<p class="lead">An ascending triangle is a chart pattern with a flat top and rising lows: the stock keeps stopping at about the same price, while each pullback ends higher than the last one.</p>
<h2>What it looks like</h2>
<p>A horizontal line can be drawn across the highs, and a rising line under the lows. The two lines come closer together until the stock leaves the shape. The pattern is complete when the stock closes above the flat top.</p>
<h2>Why traders watch it</h2>
<p>The flat top shows sellers at one price. The rising lows show buyers willing to pay more each time they step in. It is often read as the pressure building against a fixed level, and a close above that level as the point where the sellers there have been exhausted.</p>
<h2>The rules the scanner checks</h2>
<ul>
<li>It looks at the last 45 trading days and takes the three highest swing highs. They must be within 2% of each other. Together they define the resistance.</li>
<li>The most recent swing low is higher than the one before it.</li>
<li>Today's close is above the resistance and yesterday's was not, so only the first day is listed.</li>
<li>${VOLUME_EN}</li>
</ul>
<p>${QUALITY_EN}</p>
<h2>How it differs from a plain resistance breakout</h2>
<p>A <a href="/learn/horizontal-resistance-breakout">horizontal resistance breakout</a> looks only at the level. The triangle adds the requirement that the lows are rising, and it is limited to a shorter period. When both apply, the scan shows the pattern with the higher priority.</p>
<h2>What it does not tell you</h2>
<p>Some traders count this as a continuation pattern and others do not: the shape can also break downward through the rising line, and the scanner only looks for the upward case, so a list of scans shows just one of the two outcomes.</p>`,
    },
    he: {
      short: 'משולש עולה',
      title: 'משולש עולה: תקרה שטוחה ושפלים עולים',
      description: 'רמה שהמניה ממשיכה להיעצר בה, וכל תיקון שמסתיים גבוה יותר. מהו משולש עולה, ואילו כללים בודק הסורק של Swing Desk.',
      body: `<p class="lead">משולש עולה הוא תבנית גרף עם תקרה שטוחה ושפלים עולים: המניה ממשיכה להיעצר באותו מחיר בערך, בזמן שכל תיקון מסתיים גבוה יותר מקודמו.</p>
<h2>איך הוא נראה</h2>
<p>אפשר למתוח קו אופקי דרך השיאים וקו עולה מתחת לשפלים. שני הקווים מתקרבים זה לזה עד שהמניה יוצאת מהצורה. התבנית מושלמת כשהמניה נסגרת מעל התקרה השטוחה.</p>
<h2>למה סוחרים מסתכלים עליו</h2>
<p>התקרה השטוחה מראה מוכרים במחיר אחד. השפלים העולים מראים קונים שמוכנים לשלם יותר בכל פעם שהם נכנסים. פירוש נפוץ הוא לחץ שנבנה מול רמה קבועה, וסגירה מעל הרמה כנקודה שבה המוכרים שם נגמרו.</p>
<h2>הכללים שהסורק בודק</h2>
<ul>
<li>הוא מסתכל על 45 ימי המסחר האחרונים ולוקח את שלושת השיאים המקומיים הגבוהים ביותר. הם חייבים להיות במרחק של עד 2% זה מזה. יחד הם מגדירים את ההתנגדות.</li>
<li>השפל המקומי האחרון גבוה מזה שלפניו.</li>
<li>מחיר הסגירה היום מעל ההתנגדות, ואתמול לא, ולכן מוצג רק היום הראשון.</li>
<li>${VOLUME_HE}</li>
</ul>
<p>${QUALITY_HE}</p>
<h2>במה הוא שונה מפריצת התנגדות רגילה</h2>
<p><a href="/he/learn/horizontal-resistance-breakout">פריצת התנגדות אופקית</a> מסתכלת רק על הרמה. המשולש מוסיף דרישה לשפלים עולים, ומוגבל לתקופה קצרה יותר. כששניהם מתקיימים, הסריקה מציגה את התבנית בעלת העדיפות הגבוהה יותר.</p>
<h2>מה הוא לא אומר</h2>
<p>יש סוחרים שרואים בו תבנית המשך ויש שלא: הצורה יכולה להישבר גם כלפי מטה דרך הקו העולה, והסורק מחפש רק את המקרה של עלייה, ולכן רשימת הסריקות מראה רק אחת משתי התוצאות.</p>`,
    },
  },
  {
    slug: 'ascending-trendline-support',
    group: 'patterns',
    related: ['descending-trendline-breakout', 'horizontal-support-bounce', '150-day-moving-average'],
    en: {
      short: 'Ascending trendline support',
      title: 'Ascending Trendline: How a Valid Support Line Is Drawn',
      description: 'A rising line through a stock\'s lows, touched at least three times. What makes a trendline valid, and the strict rules the Swing Desk scanner uses.',
      body: `<p class="lead">An ascending trendline is a rising straight line drawn under a stock's lows. When the stock pulls back to the line and holds, it is called a test of trendline support.</p>
<h2>What makes a line meaningful</h2>
<p>Any two points can be joined by a line, so two touches mean little. The third touch is what shows that the line describes something: the stock returned to it without being told where it was. Longer lines with more touches are generally treated as more significant, and a line that the price has cut through many times is not much of a line.</p>
<h2>The rules the scanner checks</h2>
<ul>
<li>It searches up to 250 trading days back. That is a maximum, not a required length.</li>
<li>The line has at least 3 touches, counted within 1% of the line, and its two anchor points are at least 15 trading days apart.</li>
<li>No low in the period crosses below the line by more than 0.5%. This is strict on purpose: a line that was already broken is not support.</li>
<li>Today's close is between 0% and 3% above the line, and the low of one of the last 3 sessions came within 1.5% of it.</li>
</ul>
<p>A volume condition is not used, since the line itself has been tested. ${QUALITY_EN}</p>
<h2>A note on the math</h2>
<p>A straight line on a price chart looks different on a linear scale and on a logarithmic scale, so hand-drawn lines on another chart can differ from the one the scanner found. The stock page draws the scanner's line, so it can be checked by eye.</p>
<h2>What it does not tell you</h2>
<p>A line that held three times can fail on the fourth test. Lines also get steeper than a stock can sustain: a price rising along a very steep line needs to keep accelerating to stay above it. The rule describes the past shape of the lows, not what the next test will bring.</p>`,
    },
    he: {
      short: 'תמיכת קו מגמה עולה',
      title: 'קו מגמה עולה: איך מציירים קו תמיכה תקף',
      description: 'קו עולה דרך השפלים של מניה, עם שלוש נגיעות לפחות. מה הופך קו מגמה לתקף, והכללים המחמירים שהסורק של Swing Desk בודק.',
      body: `<p class="lead">קו מגמה עולה הוא קו ישר ועולה שמצויר מתחת לשפלים של מניה. כשהמניה מתקנת אל הקו ומחזיקה, זה נקרא מבחן של תמיכת קו המגמה.</p>
<h2>מה נותן לקו משמעות</h2>
<p>אפשר לחבר בקו כל שתי נקודות, ולכן שתי נגיעות אומרות מעט. הנגיעה השלישית היא שמראה שהקו מתאר משהו: המניה חזרה אליו בלי שמישהו אמר לה איפה הוא. קווים ארוכים יותר עם יותר נגיעות נחשבים בדרך כלל למשמעותיים יותר, וקו שהמחיר חתך פעמים רבות הוא לא ממש קו.</p>
<h2>הכללים שהסורק בודק</h2>
<ul>
<li>הוא מחפש עד 250 ימי מסחר אחורה. זה מקסימום, לא אורך נדרש.</li>
<li>לקו יש לפחות 3 נגיעות, שנספרות במרחק של עד 1% ממנו, ולפחות 15 ימי מסחר בין שתי נקודות העיגון שלו.</li>
<li>אף שפל בתקופה לא חוצה את הקו ביותר מ-0.5%. זה מחמיר בכוונה: קו שכבר נשבר אינו תמיכה.</li>
<li>מחיר הסגירה היום גבוה ב-0% עד 3% מהקו, והשפל של אחד מ-3 ימי המסחר האחרונים התקרב אליו עד 1.5%.</li>
</ul>
<p>לא משתמשים בתנאי של נפח מסחר, כי הקו עצמו כבר נבחן. ${QUALITY_HE}</p>
<h2>הערה על החשבון</h2>
<p>קו ישר בגרף מחירים נראה שונה בסקאלה רגילה ובסקאלה לוגריתמית, ולכן קו שמצויר ביד בגרף אחר יכול להיות שונה מזה שהסורק מצא. עמוד המניה מצייר את הקו של הסורק, כך שאפשר לבדוק אותו בעין.</p>
<h2>מה הוא לא אומר</h2>
<p>קו שהחזיק שלוש פעמים יכול להישבר במבחן הרביעי. קווים גם נעשים תלולים יותר ממה שמניה יכולה להחזיק: מחיר שעולה לאורך קו תלול מאוד צריך להמשיך להאיץ כדי להישאר מעליו. הכלל מתאר את הצורה שהייתה לשפלים בעבר, ולא את מה שיביא המבחן הבא.</p>`,
    },
  },
  {
    slug: 'horizontal-support-bounce',
    group: 'patterns',
    related: ['horizontal-resistance-breakout', 'ascending-trendline-support', 'double-bottom'],
    en: {
      short: 'Horizontal support bounce',
      title: 'Horizontal Support: When a Stock Holds a Tested Level',
      description: 'A price where a stock has already turned up, tested again. What horizontal support is, and the rules the Swing Desk scanner checks for a bounce.',
      body: `<p class="lead">Horizontal support is a price where a stock has turned up more than once. A bounce is when the stock returns to that price and closes above it.</p>
<h2>Why a level holds</h2>
<p>Support forms for reasons similar to resistance. People who missed the earlier turn may want to buy near the same price, and those who bought there have a reason to defend the position. A level that used to be resistance often turns into support after a breakout, because the people who sold there were proven wrong.</p>
<h2>The rules the scanner checks</h2>
<ul>
<li>It looks at the last 150 trading days for swing lows within 2% of each other, with at least 2 touches. Together they define the level.</li>
<li>Today's low is within 1.5% of the level, and today's close is above it.</li>
<li>At least 60% of the previous 10 closes were above the level. The stock is pulling back to support from above, and it has not been living below it. A stock under the level that spikes up to it is a different situation.</li>
</ul>
<p>A volume condition is not used, since the level has already been tested. ${QUALITY_EN}</p>
<h2>On the stock page</h2>
<p>The chart on the stock page draws the level at the bottom of the wick from its first touch, in the same color as all the other structures. When several prices cluster closely, it may appear as a zone instead of a single line, and the distance shown is always the price compared with the level (plus means above).</p>
<h2>What it does not tell you</h2>
<p>Each test also uses up buyers at that price, and many traders treat repeated tests as weakening a level rather than strengthening it. A bounce on one day does not show that the level will hold on the next test.</p>`,
    },
    he: {
      short: 'תמיכה אופקית',
      title: 'תמיכה אופקית: כשמניה מחזיקה רמה שנבחנה',
      description: 'מחיר שבו מניה כבר הסתובבה כלפי מעלה, ונבחן שוב. מהי תמיכה אופקית, ואילו כללים בודק הסורק של Swing Desk כדי לזהות ריבאונד.',
      body: `<p class="lead">תמיכה אופקית היא מחיר שבו מניה הסתובבה כלפי מעלה יותר מפעם אחת. ריבאונד הוא כשהמניה חוזרת אל המחיר הזה ונסגרת מעליו.</p>
<h2>למה רמה מחזיקה</h2>
<p>תמיכה נוצרת מסיבות דומות להתנגדות. מי שפספס את התפנית הקודמת עשוי לרצות לקנות סביב אותו מחיר, ומי שקנה שם רוצה להגן על הפוזיציה. רמה שהייתה התנגדות הופכת לעיתים קרובות לתמיכה אחרי פריצה, כי מי שמכר שם התברר כטועה.</p>
<h2>הכללים שהסורק בודק</h2>
<ul>
<li>הוא מסתכל על 150 ימי המסחר האחרונים ומחפש שפלים מקומיים במרחק של עד 2% זה מזה, עם לפחות 2 נגיעות. יחד הם מגדירים את הרמה.</li>
<li>השפל של היום במרחק של עד 1.5% מהרמה, ומחיר הסגירה מעליה.</li>
<li>לפחות 60% מ-10 הסגירות הקודמות היו מעל הרמה. המניה מתקנת אל התמיכה מלמעלה, ולא חיה מתחתיה. מניה מתחת לרמה שקופצת אליה היא מצב אחר.</li>
</ul>
<p>לא משתמשים בתנאי של נפח מסחר, כי הרמה כבר נבחנה. ${QUALITY_HE}</p>
<h2>בעמוד המניה</h2>
<p>הגרף בעמוד המניה מצייר את הרמה בתחתית הפתיל של הנגיעה הראשונה, באותו צבע של כל שאר המבנים. כשכמה מחירים צפופים זה ליד זה, היא יכולה להופיע כאזור ולא כקו בודד, והמרחק המוצג הוא תמיד המחיר ביחס לרמה (פלוס פירושו מעל).</p>
<h2>מה היא לא אומרת</h2>
<p>כל מבחן גם מכלה קונים באותו מחיר, וסוחרים רבים רואים במבחנים חוזרים החלשה של רמה ולא חיזוק שלה. ריבאונד ביום אחד לא מראה שהרמה תחזיק במבחן הבא.</p>`,
    },
  },
  {
    slug: 'descending-trendline-breakout',
    group: 'patterns',
    related: ['ascending-trendline-support', 'horizontal-resistance-breakout', 'stage-analysis'],
    en: {
      short: 'Descending trendline breakout',
      title: 'Descending Trendline Breakout: A Falling Line Is Crossed',
      description: 'A falling line across a stock\'s highs, and the first close above it. What a descending trendline breakout is and the rules the Swing Desk scanner checks.',
      body: `<p class="lead">A descending trendline is a falling straight line drawn across a stock's highs. A breakout is the day the stock closes above it after being at or under it the day before.</p>
<h2>What it shows</h2>
<p>A line of lower highs describes a period in which each rally ended sooner than the last. Crossing it shows that the most recent rally has gone further than that pattern allowed. It does not mean a new uptrend has begun: the stock may simply have moved into a sideways period.</p>
<h2>The rules the scanner checks</h2>
<ul>
<li>It searches up to 250 trading days back for a line of highs with at least 3 touches, counted within 1% of the line, with anchor points at least 15 trading days apart.</li>
<li>No high in the period crosses above the line by more than 0.5%, the same strictness as an <a href="/learn/ascending-trendline-support">ascending trendline</a>.</li>
<li>Yesterday's close was at or below the line, and today's is above it. Only the first day is listed.</li>
<li>${VOLUME_EN}</li>
</ul>
<p>${QUALITY_EN}</p>
<h2>What it does not tell you</h2>
<p>The longer and steeper the decline, the easier it is for a falling line to be crossed by a small rally, because the line keeps dropping toward the price. In such a case the crossing can happen with the stock still far below its moving averages, and the <a href="/learn/stage-analysis">trend stage</a> next to the scan shows that. A breakout from a falling line and an uptrend are not the same thing.</p>`,
    },
    he: {
      short: 'פריצת קו מגמה יורד',
      title: 'פריצת קו מגמה יורד: כשקו יורד נחצה',
      description: 'קו יורד דרך השיאים של מניה, והסגירה הראשונה מעליו. מהי פריצת קו מגמה יורד, ואילו כללים בודק הסורק של Swing Desk.',
      body: `<p class="lead">קו מגמה יורד הוא קו ישר ויורד שמצויר דרך השיאים של מניה. פריצה היא היום שבו המניה נסגרת מעליו אחרי שיום קודם הייתה עליו או מתחתיו.</p>
<h2>מה זה מראה</h2>
<p>קו של שיאים יורדים מתאר תקופה שבה כל עלייה הסתיימה מוקדם יותר מקודמתה. חציית הקו מראה שהעלייה האחרונה הגיעה רחוק יותר ממה שהדפוס הזה אפשר. זה לא אומר שהתחילה מגמת עלייה חדשה: ייתכן שהמניה פשוט עברה לתקופה של תנועה הצידה.</p>
<h2>הכללים שהסורק בודק</h2>
<ul>
<li>הוא מחפש עד 250 ימי מסחר אחורה קו שיאים עם לפחות 3 נגיעות, שנספרות במרחק של עד 1% ממנו, ולפחות 15 ימי מסחר בין נקודות העיגון.</li>
<li>אף שיא בתקופה לא חוצה את הקו ביותר מ-0.5%, אותה חומרה כמו ב<a href="/he/learn/ascending-trendline-support">קו מגמה עולה</a>.</li>
<li>הסגירה של אתמול הייתה על הקו או מתחתיו, והסגירה של היום מעליו. רק היום הראשון מוצג.</li>
<li>${VOLUME_HE}</li>
</ul>
<p>${QUALITY_HE}</p>
<h2>מה היא לא אומרת</h2>
<p>ככל שהירידה ארוכה ותלולה יותר, קל יותר לעלייה קטנה לחצות קו יורד, כי הקו ממשיך לרדת לכיוון המחיר. במקרה כזה החצייה יכולה לקרות כשהמניה עדיין רחוקה מתחת לממוצעים שלה, ו<a href="/he/learn/stage-analysis">שלב המגמה</a> שליד הסריקה מראה זאת. פריצה מקו יורד ומגמת עלייה הם לא אותו דבר.</p>`,
    },
  },
  {
    slug: 'macd-crossover',
    group: 'patterns',
    related: ['rsi-oversold-bounce', 'momentum-surge', 'golden-cross'],
    en: {
      short: 'MACD crossover',
      title: 'MACD Bullish Crossover: What the Two Lines Mean',
      description: 'The MACD line crossing above its signal line. How MACD is calculated from moving averages, what the cross measures, and the rule the Swing Desk scanner uses.',
      body: `<p class="lead">MACD (Moving Average Convergence Divergence) is an indicator of short-term momentum, built from two moving averages. A bullish crossover is the day the MACD line crosses above its signal line.</p>
<h2>How it is calculated</h2>
<ul>
<li><b>The MACD line:</b> the 12-day exponential moving average minus the 26-day exponential moving average.</li>
<li><b>The signal line:</b> the 9-day exponential moving average of the MACD line.</li>
<li><b>The histogram:</b> the gap between the two lines.</li>
</ul>
<p>An exponential average gives more weight to recent days. When the 12-day average is pulling away upward from the 26-day, the MACD line rises.</p>
<h2>What the cross measures</h2>
<p>It measures a change in speed. A cross above the signal line means that the short-term average has started to gain on the longer one faster than it did over the past nine days. It can happen while the stock is falling, as long as it is falling more slowly than before.</p>
<h2>The rules the scanner checks</h2>
<ul>
<li>The standard settings of 12, 26 and 9, on daily closes.</li>
<li>Yesterday the MACD line was at or below the signal line, and today it is above it.</li>
</ul>
<p>${QUALITY_EN}</p>
<h2>What it does not tell you</h2>
<p>This scan does not need a level or a shape. In a stock moving sideways the two lines can cross many times with no lasting move. For that reason it has a low priority: when a chart pattern appears on the same day, that pattern is the one listed. The indicator's values depend on past prices and arrive after the move has begun.</p>`,
    },
    he: {
      short: 'חציית MACD',
      title: 'חציית MACD חיובית: מה אומרים שני הקווים',
      description: 'קו ה-MACD שחוצה מעל קו הסיגנל שלו. איך מחשבים MACD מממוצעים נעים, מה החצייה מודדת, ומה הכלל שהסורק של Swing Desk בודק.',
      body: `<p class="lead">MACD (ראשי תיבות של Moving Average Convergence Divergence) הוא אינדיקטור של מומנטום קצר טווח, שבנוי משני ממוצעים נעים. חצייה חיובית היא היום שבו קו ה-MACD חוצה מעל קו הסיגנל שלו.</p>
<h2>איך מחשבים אותו</h2>
<ul>
<li><b>קו ה-MACD:</b> הממוצע הנע המעריכי של 12 יום פחות הממוצע הנע המעריכי של 26 יום.</li>
<li><b>קו הסיגנל:</b> הממוצע הנע המעריכי של 9 יום של קו ה-MACD.</li>
<li><b>ההיסטוגרמה:</b> הפער בין שני הקווים.</li>
</ul>
<p>ממוצע מעריכי נותן משקל גדול יותר לימים האחרונים. כשממוצע 12 מתרחק כלפי מעלה מממוצע 26, קו ה-MACD עולה.</p>
<h2>מה החצייה מודדת</h2>
<p>היא מודדת שינוי במהירות. חצייה מעל קו הסיגנל אומרת שהממוצע הקצר התחיל לצבור יתרון על הארוך מהר יותר מאשר בתשעת הימים האחרונים. זה יכול לקרות גם כשהמניה יורדת, כל עוד היא יורדת לאט יותר מקודם.</p>
<h2>הכללים שהסורק בודק</h2>
<ul>
<li>ההגדרות הרגילות של 12, 26 ו-9, על מחירי סגירה יומיים.</li>
<li>אתמול קו ה-MACD היה על קו הסיגנל או מתחתיו, והיום הוא מעליו.</li>
</ul>
<p>${QUALITY_HE}</p>
<h2>מה היא לא אומרת</h2>
<p>הסריקה הזאת לא צריכה רמה או צורה. במניה שנעה הצידה שני הקווים יכולים להיחצות פעמים רבות בלי תנועה מתמשכת. לכן היא בעדיפות נמוכה: כשתבנית גרף מופיעה באותו יום, היא זו שמוצגת. ערכי האינדיקטור תלויים במחירי עבר ומגיעים אחרי שהתנועה כבר התחילה.</p>`,
    },
  },
  {
    slug: 'momentum-surge',
    group: 'patterns',
    related: ['bull-flag', 'rs-rating', 'macd-crossover'],
    en: {
      short: 'Momentum surge',
      title: 'Momentum Surge: A Fast Rise on Rising Volume',
      description: 'A stock up at least 5% in a week with heavier trading than usual. What a momentum surge is, and the rule the Swing Desk scanner checks.',
      body: `<p class="lead">A momentum surge is a short period in which a stock rises fast and trades noticeably more than usual. It has no chart shape: it is defined by speed and volume only.</p>
<h2>Why volume is part of it</h2>
<p>A rise on normal volume can come from a lack of sellers. A rise on higher volume shows more shares changing hands, so more participants are involved in the move. Heavy trading is often caused by news, an earnings report or a change in the sector, and the volume alone does not say which.</p>
<h2>The rules the scanner checks</h2>
<ul>
<li>The stock is up at least 5% over the last 5 trading days.</li>
<li>Its average daily volume over those 5 days is at least 15% above the average of the 20 trading days before them.</li>
</ul>
<p>Both conditions are measured from daily closing data. ${QUALITY_EN}</p>
<h2>Why it is low on the list</h2>
<p>The scan does not point at a level, so there is no specific price at which it would be proven wrong. It is also listed only when no pattern with a higher priority appears on the same day. A surge that later pauses in a narrow range can turn into a <a href="/learn/bull-flag">bull flag</a>, which does have a defined level.</p>
<h2>What it does not tell you</h2>
<p>After a sharp rise a stock has moved far from its recent average price, so even an ordinary pullback can be large. The rule does not check why the stock rose. Reading the headline and the date of the next earnings report on the stock page is a common next step. A surge caused by a takeover offer, for example, behaves very differently from one that comes from steady demand.</p>`,
    },
    he: {
      short: 'זינוק מומנטום',
      title: 'זינוק מומנטום: עלייה מהירה בנפח מסחר עולה',
      description: 'מניה שעלתה 5% לפחות בשבוע, במסחר ער מהרגיל. מהו זינוק מומנטום, ומה הכלל שהסורק של Swing Desk בודק.',
      body: `<p class="lead">זינוק מומנטום הוא פרק זמן קצר שבו מניה עולה מהר ונסחרת הרבה יותר מהרגיל. אין לו צורה בגרף: הוא מוגדר רק לפי מהירות ונפח מסחר.</p>
<h2>למה נפח המסחר הוא חלק מזה</h2>
<p>עלייה בנפח רגיל יכולה לנבוע ממחסור במוכרים. עלייה בנפח גבוה מראה שיותר מניות מחליפות ידיים, ולכן יותר משתתפים מעורבים בתנועה. מסחר ער נובע לעיתים קרובות מחדשות, מדוח או משינוי בסקטור, והנפח לבדו לא אומר מה מהם.</p>
<h2>הכללים שהסורק בודק</h2>
<ul>
<li>המניה עלתה 5% לפחות ב-5 ימי המסחר האחרונים.</li>
<li>נפח המסחר היומי הממוצע ב-5 הימים האלה גבוה ב-15% לפחות מהממוצע של 20 ימי המסחר שלפניהם.</li>
</ul>
<p>שני התנאים נמדדים מנתוני סוף יום. ${QUALITY_HE}</p>
<h2>למה הוא נמוך ברשימה</h2>
<p>הסריקה לא מצביעה על רמה, ולכן אין מחיר מסוים שבו היא תתברר כשגויה. היא גם מוצגת רק כשאין באותו יום תבנית בעדיפות גבוהה יותר. זינוק שנעצר אחר כך בטווח צר יכול להפוך ל<a href="/he/learn/bull-flag">דגל שורי</a>, שלו יש רמה מוגדרת.</p>
<h2>מה הוא לא אומר</h2>
<p>אחרי עלייה חדה המניה התרחקה מאוד מהמחיר הממוצע האחרון שלה, ולכן גם תיקון רגיל יכול להיות גדול. הכלל לא בודק למה המניה עלתה. קריאת הכותרת ומועד הדוח הבא בעמוד המניה הם צעד מקובל בהמשך. זינוק שנובע למשל מהצעת רכישה מתנהג אחרת לגמרי מזה שנובע מביקוש רציף.</p>`,
    },
  },
  {
    slug: 'rsi-oversold-bounce',
    group: 'patterns',
    related: ['macd-crossover', 'double-bottom', 'horizontal-support-bounce'],
    en: {
      short: 'RSI oversold bounce',
      title: 'RSI Oversold Bounce: Back Above 30',
      description: 'RSI measures the recent balance of gains and losses. What an oversold reading means, what a move back above 30 shows, and the rule the Swing Desk scanner uses.',
      body: `<p class="lead">RSI (Relative Strength Index) is a 0-to-100 measure of how a stock's recent gains compare with its recent losses. A reading below 30 is commonly called oversold. An oversold bounce is the move back above 30.</p>
<h2>How it is calculated</h2>
<p>Over a standard period of 14 trading days, RSI compares the average size of the up days with the average size of the down days. If up days were much larger, the reading is high. If down days dominated, it is low. It says nothing about the price level itself, only about the recent direction of movement.</p>
<p>The RSI here is a different thing from <a href="/learn/rs-rating">relative strength (RS)</a>, despite the similar name. RS compares a stock with the rest of the market. RSI compares a stock with itself.</p>
<h2>The rules the scanner checks</h2>
<ul>
<li>14-day RSI was below 30 at some point in the previous 10 trading days.</li>
<li>Today it is above 30.</li>
<li>Today's close is higher than yesterday's.</li>
</ul>
<p>${QUALITY_EN}</p>
<h2>Why oversold is not the same as cheap</h2>
<p>A stock can stay oversold for weeks during a strong decline, and RSI can rise above 30 while the stock is still well under its moving averages. The word "oversold" describes speed, not value. This scan looks at stocks after a sharp decline, so a look at the <a href="/learn/stage-analysis">trend stage</a> matters: a bounce in Stage 2 and a bounce in Stage 4 are very different situations.</p>
<h2>What it does not tell you</h2>
<p>A bounce from an oversold reading does not show that the decline is over, and it does not explain why the stock fell. If the cause was a poor earnings report, a sector event or a change in the company, RSI does not take that into account.</p>`,
    },
    he: {
      short: 'התאוששות RSI',
      title: 'התאוששות RSI ממכירת יתר: חזרה מעל 30',
      description: 'RSI מודד את האיזון האחרון בין עליות לירידות. מה המשמעות של מכירת יתר, מה מראה חזרה מעל 30, ומה הכלל שהסורק של Swing Desk בודק.',
      body: `<p class="lead">RSI (ראשי תיבות של Relative Strength Index) הוא מדד בין 0 ל-100 שמשווה את העליות האחרונות של מניה לירידות האחרונות שלה. ערך מתחת ל-30 נקרא בדרך כלל מכירת יתר. התאוששות ממכירת יתר היא החזרה מעל 30.</p>
<h2>איך מחשבים אותו</h2>
<p>על פני תקופה רגילה של 14 ימי מסחר, RSI משווה את הגודל הממוצע של ימי העלייה לגודל הממוצע של ימי הירידה. אם ימי העלייה היו גדולים בהרבה, הערך גבוה. אם ימי הירידה שלטו, הוא נמוך. הוא לא אומר דבר על רמת המחיר עצמה, אלא רק על הכיוון האחרון של התנועה.</p>
<p>ה-RSI כאן הוא דבר אחר מ<a href="/he/learn/rs-rating">עוצמה יחסית (RS)</a>, למרות השם הדומה. RS משווה מניה לשאר השוק. RSI משווה מניה לעצמה.</p>
<h2>הכללים שהסורק בודק</h2>
<ul>
<li>RSI של 14 יום היה מתחת ל-30 באחד מ-10 ימי המסחר הקודמים.</li>
<li>היום הוא מעל 30.</li>
<li>מחיר הסגירה היום גבוה מזה של אתמול.</li>
</ul>
<p>${QUALITY_HE}</p>
<h2>למה מכירת יתר אינה זולה</h2>
<p>מניה יכולה להישאר במכירת יתר שבועות בזמן ירידה חזקה, ו-RSI יכול לעלות מעל 30 כשהמניה עדיין הרבה מתחת לממוצעים שלה. המילים "מכירת יתר" מתארות מהירות, לא שווי. הסריקה הזאת מסתכלת על מניות אחרי ירידה חדה, ולכן חשוב להסתכל על <a href="/he/learn/stage-analysis">שלב המגמה</a>: ריבאונד בשלב 2 וריבאונד בשלב 4 הם מצבים שונים מאוד.</p>
<h2>מה היא לא אומרת</h2>
<p>התאוששות ממכירת יתר לא מראה שהירידה הסתיימה, ולא מסבירה למה המניה ירדה. אם הסיבה הייתה דוח חלש, אירוע בסקטור או שינוי בחברה, RSI לא מביא זאת בחשבון.</p>`,
    },
  },
];
