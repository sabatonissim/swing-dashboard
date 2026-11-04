// v35: the guides (/learn/...), part 2: how the site measures strength, trend and volatility, and the market and company data
// it shows. Thresholds come from pipeline_a_scanner.py and api_server.py (_breadth_label, _curve_label): keep them equal.
module.exports = [
  {
    slug: 'rs-rating',
    group: 'measures',
    related: ['stage-analysis', '52-week-high-breakout', 'atr', 'market-breadth'],
    en: {
      short: 'Relative strength (RS)',
      title: 'RS Rating: How a Stock\'s Strength Against the Market Is Measured',
      description: 'A 1-to-99 rank of a stock\'s return against the S&P 500 over the past year. How the RS rating is calculated on Swing Desk and how to read it.',
      body: `<p class="lead">Relative strength (RS) compares a stock's price performance with the market's over the same period. On Swing Desk it appears as a rating from 1 to 99: an RS of 90 means the stock did better than about 90% of the stocks in the scan.</p>
<h2>How it is calculated</h2>
<ul>
<li>For each stock, the return over the last 63, 126, 189 and 252 trading days (about 3, 6, 9 and 12 months) is compared with the return of SPY, an S&P 500 fund, over the same days.</li>
<li>The four differences are combined with weights of 40%, 20%, 20% and 20%, so the most recent quarter counts double.</li>
<li>All the stocks in the scan are then ranked by that number, and the rank is turned into a percentile from 1 to 99.</li>
</ul>
<p>So the rating is relative twice: against the index, and then against the other stocks in the scan on that day.</p>
<h2>How it is used on the site</h2>
<p>Most scans are listed only if RS is at least 90, or at least 70 together with a <a href="/learn/stage-analysis">Stage 2</a> uptrend and an <a href="/learn/atr">ATR</a> of at least 3%. The "Leading stocks" table lists stocks with RS of at least 80 in Stage 2. "Today's focus" asks for RS of at least 90 and no earnings report in the next 7 days.</p>
<h2>RS and RSI are different things</h2>
<p>RS compares a stock with the market over a year. <a href="/learn/rsi-oversold-bounce">RSI</a> compares a stock's recent up days with its own recent down days, over two weeks. A stock can have a high RS and a low RSI at the same time, for example during a short pullback in a long rise.</p>
<h2>What it does not tell you</h2>
<p>RS looks backward. A high rating says the stock has been strong. It does not say it will remain so, and a very high rating often comes after a large move. In a falling market a high RS can also mean only that a stock fell less than the others. The rating is built from price alone and knows nothing about the company's earnings or value.</p>`,
    },
    he: {
      short: 'עוצמה יחסית (RS)',
      title: 'דירוג RS: איך מודדים את עוצמת המניה מול השוק',
      description: 'דירוג מ-1 עד 99 של תשואת המניה מול S&P 500 בשנה האחרונה. איך מחושב דירוג RS ב-Swing Desk ואיך לקרוא אותו.',
      body: `<p class="lead">עוצמה יחסית (RS) משווה את ביצועי המחיר של מניה לביצועי השוק באותה תקופה. ב-Swing Desk היא מופיעה כדירוג מ-1 עד 99: דירוג 90 אומר שהמניה הצליחה יותר מכ-90% מהמניות בסריקה.</p>
<h2>איך מחשבים אותה</h2>
<ul>
<li>לכל מניה משווים את התשואה ב-63, 126, 189 ו-252 ימי המסחר האחרונים (כ-3, 6, 9 ו-12 חודשים) לתשואה של SPY, קרן על S&P 500, באותם ימים.</li>
<li>ארבעת ההפרשים משוקללים במשקלים של 40%, 20%, 20% ו-20%, כך שהרבעון האחרון נספר כפול.</li>
<li>כל המניות בסריקה מדורגות לפי המספר הזה, והדירוג הופך לאחוזון מ-1 עד 99.</li>
</ul>
<p>לכן הדירוג יחסי פעמיים: מול המדד, ואחר כך מול שאר המניות בסריקה באותו יום.</p>
<h2>איך משתמשים בה באתר</h2>
<p>רוב הסריקות מוצגות רק אם RS הוא 90 לפחות, או 70 לפחות יחד עם מגמת עלייה ב<a href="/he/learn/stage-analysis">שלב 2</a> ו-<a href="/he/learn/atr">ATR</a> של 3% לפחות. הטבלה "מניות מובילות" מציגה מניות עם RS של 80 לפחות בשלב 2. "המוקד של היום" דורש RS של 90 לפחות ובלי דוח ב-7 הימים הקרובים.</p>
<h2>RS ו-RSI הם דברים שונים</h2>
<p>RS משווה מניה לשוק על פני שנה. <a href="/he/learn/rsi-oversold-bounce">RSI</a> משווה את ימי העלייה האחרונים של מניה לימי הירידה האחרונים שלה עצמה, על פני שבועיים. למניה יכולים להיות בו זמנית RS גבוה ו-RSI נמוך, למשל בתיקון קצר בתוך עלייה ארוכה.</p>
<h2>מה היא לא אומרת</h2>
<p>RS מסתכל אחורה. דירוג גבוה אומר שהמניה הייתה חזקה. הוא לא אומר שכך תישאר, ודירוג גבוה מאוד מגיע לעיתים קרובות אחרי תנועה גדולה. בשוק יורד RS גבוה יכול גם לומר רק שהמניה ירדה פחות מהאחרות. הדירוג בנוי ממחיר בלבד ולא יודע דבר על הרווחים או השווי של החברה.</p>`,
    },
  },
  {
    slug: 'atr',
    group: 'measures',
    related: ['position-sizing', 'expected-move-earnings', '150-day-moving-average'],
    en: {
      short: 'ATR (Average True Range)',
      title: 'ATR: How Far a Stock Usually Moves in a Day',
      description: 'Average True Range measures a stock\'s typical daily range, gaps included. How ATR is calculated, what ATR percent means, and how Swing Desk uses it.',
      body: `<p class="lead">ATR (Average True Range) measures how far a stock typically moves in a single day, including gaps between one day's close and the next day's open. It says how much a stock moves, not in which direction.</p>
<h2>How it is calculated</h2>
<p>The <b>true range</b> of a day is the largest of three numbers:</p>
<ul>
<li>today's high minus today's low;</li>
<li>the distance from yesterday's close to today's high;</li>
<li>the distance from yesterday's close to today's low.</li>
</ul>
<p>The last two catch the gaps that a simple high-minus-low would miss. ATR is a smoothed average of the true range, here over 14 days using Wilder's standard method.</p>
<h2>Why a percentage</h2>
<p>A $2 ATR is large for a $20 stock and small for a $500 stock. Dividing by the price makes stocks comparable: a $2 ATR on a $50 stock is an ATR of 4%. Swing Desk shows both the dollar value and the percentage.</p>
<h2>How it is used on the site</h2>
<ul>
<li>Most scans require an ATR of at least 3% when RS is between 70 and 90, because a stock that barely moves leaves little room for a swing.</li>
<li>The <a href="/learn/150-day-moving-average">150-day moving average</a> support bounce has its own requirement of at least 2.5%.</li>
</ul>
<h2>How traders read it</h2>
<p>ATR gives a sense of what an ordinary day looks like. A stop closer than one ATR from the entry can be reached by normal movement, which is why it often appears in <a href="/learn/position-sizing">position sizing</a>. A sudden rise in ATR shows that the stock has become more volatile than it was.</p>
<h2>What it does not tell you</h2>
<p>ATR does not predict direction or the next day's range. It is an average of the past two to three weeks, so it reacts slowly, and an earnings report can move a stock several times its ATR. For that event, the <a href="/learn/expected-move-earnings">expected move</a> from options prices is a closer measure.</p>`,
    },
    he: {
      short: 'ATR (טווח אמיתי ממוצע)',
      title: 'ATR: כמה מניה זזה בדרך כלל ביום',
      description: 'ATR מודד את הטווח היומי הרגיל של מניה, כולל פערים. איך מחשבים ATR, מה המשמעות של ATR באחוזים, ואיך Swing Desk משתמש בו.',
      body: `<p class="lead">ATR (ראשי תיבות של Average True Range, טווח אמיתי ממוצע) מודד כמה מניה זזה בדרך כלל ביום אחד, כולל פערים בין סגירה של יום אחד לפתיחה של הבא. הוא אומר כמה המניה זזה, לא לאיזה כיוון.</p>
<h2>איך מחשבים אותו</h2>
<p><b>הטווח האמיתי</b> של יום הוא הגדול מבין שלושה מספרים:</p>
<ul>
<li>השיא של היום פחות השפל של היום;</li>
<li>המרחק מהסגירה של אתמול לשיא של היום;</li>
<li>המרחק מהסגירה של אתמול לשפל של היום.</li>
</ul>
<p>שני האחרונים תופסים את הפערים שחישוב פשוט של שיא פחות שפל היה מפספס. ATR הוא ממוצע מוחלק של הטווח האמיתי, כאן על פני 14 יום בשיטה הרגילה של Wilder.</p>
<h2>למה באחוזים</h2>
<p>ATR של 2 דולר הוא גדול למניה של 20 דולר וקטן למניה של 500 דולר. חלוקה במחיר מאפשרת להשוות בין מניות: ATR של 2 דולר במניה של 50 דולר הוא ATR של 4%. Swing Desk מציג גם את הערך בדולרים וגם את האחוז.</p>
<h2>איך משתמשים בו באתר</h2>
<ul>
<li>רוב הסריקות דורשות ATR של 3% לפחות כש-RS הוא בין 70 ל-90, כי מניה שכמעט לא זזה משאירה מעט מקום לתנועת סווינג.</li>
<li>לריבאונד מ<a href="/he/learn/150-day-moving-average">ממוצע 150 יום</a> יש דרישה משלו של 2.5% לפחות.</li>
</ul>
<h2>איך סוחרים קוראים אותו</h2>
<p>ATR נותן תחושה של איך נראה יום רגיל. סטופ קרוב יותר מ-ATR אחד מהכניסה יכול להיות מושג על ידי תנועה רגילה, ולכן הוא מופיע לעיתים קרובות ב<a href="/he/learn/position-sizing">חישוב גודל פוזיציה</a>. עלייה פתאומית ב-ATR מראה שהמניה נעשתה תנודתית יותר ממה שהייתה.</p>
<h2>מה הוא לא אומר</h2>
<p>ATR לא חוזה כיוון ולא את הטווח של מחר. הוא ממוצע של השבועיים-שלושה האחרונים, ולכן מגיב לאט, ודוח יכול להזיז מניה פי כמה מה-ATR שלה. לאירוע כזה <a href="/he/learn/expected-move-earnings">התנודה הצפויה</a> ממחירי האופציות היא מדד קרוב יותר.</p>`,
    },
  },
  {
    slug: 'stage-analysis',
    group: 'measures',
    related: ['150-day-moving-average', 'rs-rating', 'golden-cross', 'what-is-swing-trading'],
    en: {
      short: 'Stage analysis',
      title: 'Stage Analysis: The Four Stages of a Stock\'s Trend',
      description: 'Basing, uptrend, topping and downtrend. What the four stages of a stock\'s trend mean, and the exact rules Swing Desk uses to assign each one.',
      body: `<p class="lead">Stage analysis describes a stock's long-term trend as one of four phases: basing, uptrend, topping and downtrend. The idea is usually credited to the market analyst Stan Weinstein, who built it around a 30-week moving average.</p>
<h2>The four stages</h2>
<ul>
<li><b>Stage 1, basing:</b> after a decline, the stock moves sideways. Sellers and buyers are roughly in balance.</li>
<li><b>Stage 2, uptrend:</b> the stock makes higher highs and higher lows, above rising averages.</li>
<li><b>Stage 3, topping:</b> after a rise, the advance stalls and the longer average flattens out.</li>
<li><b>Stage 4, downtrend:</b> the stock falls below falling averages, with lower highs and lower lows.</li>
</ul>
<h2>The rules Swing Desk uses</h2>
<p>Each stock gets a stage from its closing price and its 50, 150 and 200-day simple moving averages. "Rising" means that the 150-day average is higher than it was about 20 trading days ago. The rules are checked in this order:</p>
<ul>
<li><b>Stage 2:</b> the price is above all three averages, the 50-day is above the 150-day, the 150-day is above the 200-day, and the 150-day is rising.</li>
<li><b>Stage 4:</b> the price is below the 150-day and the 200-day, and the 150-day is falling.</li>
<li><b>Stage 3:</b> the price is above the 150-day, and the 150-day is flat or falling.</li>
<li><b>Stage 1:</b> anything else.</li>
</ul>
<p>A stock needs at least 210 trading days of history to get a stage.</p>
<h2>How it is used on the site</h2>
<p>Stage 2 is part of the quality rule for most scans, and of the "Leading stocks" table. The stage also adjusts a scan's score: the same pattern scores higher in an uptrend than in a downtrend.</p>
<h2>What it does not tell you</h2>
<p>The stage comes from a fixed rule, so it can differ from what a person would call the phase of a chart, particularly near a change between stages. It describes the present and says nothing about when a stage will end. A stock can remain in Stage 2 for a long time and still fall sharply within it, and averages move slowly, so a stage changes well after the turn.</p>`,
    },
    he: {
      short: 'ניתוח שלבים',
      title: 'ניתוח שלבים: ארבעת השלבים במגמה של מניה',
      description: 'בסיס, מגמת עלייה, היפוך מלמעלה ומגמת ירידה. מה המשמעות של ארבעת השלבים במגמה של מניה, והכללים המדויקים שבהם Swing Desk קובע כל שלב.',
      body: `<p class="lead">ניתוח שלבים מתאר את המגמה ארוכת הטווח של מניה כאחד מארבעה מצבים: בסיס, מגמת עלייה, היפוך מלמעלה ומגמת ירידה. הרעיון מיוחס בדרך כלל לאנליסט השוק סטן ויינסטין, שבנה אותו סביב ממוצע נע של 30 שבועות.</p>
<h2>ארבעת השלבים</h2>
<ul>
<li><b>שלב 1, בסיס:</b> אחרי ירידה, המניה נעה הצידה. המוכרים והקונים מאוזנים בערך.</li>
<li><b>שלב 2, מגמת עלייה:</b> המניה עושה שיאים ושפלים עולים, מעל ממוצעים עולים.</li>
<li><b>שלב 3, היפוך מלמעלה:</b> אחרי עלייה, ההתקדמות נעצרת והממוצע הארוך מתיישר.</li>
<li><b>שלב 4, מגמת ירידה:</b> המניה יורדת מתחת לממוצעים יורדים, עם שיאים ושפלים יורדים.</li>
</ul>
<h2>הכללים ש-Swing Desk משתמש בהם</h2>
<p>כל מניה מקבלת שלב לפי מחיר הסגירה והממוצעים הנעים הפשוטים של 50, 150 ו-200 יום. "עולה" פירושו שממוצע 150 גבוה ממה שהיה לפני כ-20 ימי מסחר. הכללים נבדקים בסדר הזה:</p>
<ul>
<li><b>שלב 2:</b> המחיר מעל שלושת הממוצעים, ממוצע 50 מעל ממוצע 150, ממוצע 150 מעל ממוצע 200, וממוצע 150 עולה.</li>
<li><b>שלב 4:</b> המחיר מתחת לממוצע 150 ולממוצע 200, וממוצע 150 יורד.</li>
<li><b>שלב 3:</b> המחיר מעל ממוצע 150, וממוצע 150 שטוח או יורד.</li>
<li><b>שלב 1:</b> כל מצב אחר.</li>
</ul>
<p>מניה צריכה לפחות 210 ימי מסחר של היסטוריה כדי לקבל שלב.</p>
<h2>איך משתמשים בו באתר</h2>
<p>שלב 2 הוא חלק מכלל האיכות של רוב הסריקות, ומהטבלה "מניות מובילות". השלב גם משנה את הציון של סריקה: אותה תבנית מקבלת ציון גבוה יותר במגמת עלייה מאשר במגמת ירידה.</p>
<h2>מה הוא לא אומר</h2>
<p>השלב נובע מכלל קבוע, ולכן הוא יכול להיות שונה ממה שאדם היה קורא בגרף, בעיקר סמוך למעבר בין שלבים. הוא מתאר את ההווה ולא אומר דבר על מתי השלב יסתיים. מניה יכולה להישאר בשלב 2 זמן רב ועדיין לרדת בחדות בתוכו, והממוצעים זזים לאט, ולכן השלב מתחלף הרבה אחרי התפנית.</p>`,
    },
  },
  {
    slug: 'market-breadth',
    group: 'market',
    related: ['52-week-high-breakout', 'yield-curve', 'stage-analysis', 'rs-rating'],
    en: {
      short: 'Market breadth',
      title: 'Market Breadth: How Many Stocks Are Taking Part',
      description: 'An index can rise on a few large stocks. How market breadth measures participation, and how Swing Desk labels a market broad, mixed or narrow.',
      body: `<p class="lead">Market breadth measures how many stocks are taking part in a market's move. An index can rise while most of its stocks fall, because a few very large companies carry most of the weight.</p>
<h2>The figures on Swing Desk</h2>
<p>All of them are calculated every trading day over the stocks in the S&P 500:</p>
<ul>
<li><b>Above the 50-day and 200-day average:</b> the share of stocks trading above each average. The 50-day describes the past ten weeks and the 200-day about a year.</li>
<li><b>New highs and new lows:</b> how many stocks closed at a 52-week high or low.</li>
<li><b>Advancing, declining and unchanged:</b> how many stocks rose, fell or stayed flat that day.</li>
</ul>
<p>The index has about 500 companies but 503 shares, since three of the companies have two classes each. A stock with no price on the last two days, for example after being acquired, is left out of the count.</p>
<h2>The label</h2>
<p>The site adds a label based on one fixed rule, using the share of stocks above their 50-day average:</p>
<ul>
<li><b>Broad:</b> at least 60%.</li>
<li><b>Mixed:</b> between 40% and 60%.</li>
<li><b>Narrow:</b> under 40%.</li>
</ul>
<p>The label is a description of today's figure only. It is not a forecast and it does not change anything else on the site.</p>
<h2>How it is commonly read</h2>
<p>Traders often watch for a gap between an index and its breadth. An index at a new high while the share of stocks above their averages keeps falling means that fewer companies are carrying the rise. The reverse can also happen near the end of a decline. These are observations that can persist for months: such a gap does not time a turn.</p>
<h2>What it does not tell you</h2>
<p>Breadth treats a tiny company and a giant the same way, which is the point of it, but it also means that it can't explain why an index moved. It says nothing about any single stock.</p>`,
    },
    he: {
      short: 'רוחב השוק',
      title: 'רוחב השוק: כמה מניות משתתפות בתנועה',
      description: 'מדד יכול לעלות בזכות כמה מניות גדולות. איך רוחב השוק מודד השתתפות, ואיך Swing Desk מתייג שוק כרחב, מעורב או צר.',
      body: `<p class="lead">רוחב השוק מודד כמה מניות משתתפות בתנועה של השוק. מדד יכול לעלות בזמן שרוב המניות שלו יורדות, כי לכמה חברות ענק יש את רוב המשקל.</p>
<h2>הנתונים ב-Swing Desk</h2>
<p>כולם מחושבים בכל יום מסחר על המניות שב-S&P 500:</p>
<ul>
<li><b>מעל ממוצע 50 ו-200 יום:</b> חלק המניות שנסחרות מעל כל ממוצע. ממוצע 50 מתאר את עשרת השבועות האחרונים וממוצע 200 כשנה.</li>
<li><b>שיאים ושפלים חדשים:</b> כמה מניות נסגרו בשיא או בשפל של 52 שבועות.</li>
<li><b>עולות, יורדות וללא שינוי:</b> כמה מניות עלו, ירדו או נשארו במקום באותו יום.</li>
</ul>
<p>במדד כ-500 חברות אבל 503 מניות, כי לשלוש מהחברות יש שני סוגי מניות. מניה בלי מחיר ביומיים האחרונים, למשל אחרי שנרכשה, יוצאת מהספירה.</p>
<h2>התווית</h2>
<p>האתר מוסיף תווית לפי כלל קבוע אחד, בעזרת חלק המניות מעל ממוצע 50:</p>
<ul>
<li><b>רחב:</b> 60% לפחות.</li>
<li><b>מעורב:</b> בין 40% ל-60%.</li>
<li><b>צר:</b> מתחת ל-40%.</li>
</ul>
<p>התווית היא תיאור של הנתון של היום בלבד. היא לא תחזית, והיא לא משנה דבר אחר באתר.</p>
<h2>איך נוהגים לקרוא את זה</h2>
<p>סוחרים מסתכלים לעיתים קרובות על פער בין מדד לרוחב שלו. מדד בשיא חדש בזמן שחלק המניות מעל הממוצעים ממשיך לרדת אומר שפחות חברות נושאות את העלייה. גם ההפך יכול לקרות לקראת סוף ירידה. אלה תצפיות שיכולות להימשך חודשים: פער כזה לא מתזמן תפנית.</p>
<h2>מה הוא לא אומר</h2>
<p>רוחב השוק מתייחס לחברה קטנה ולענקית באותה צורה, וזו המטרה שלו, אבל זה גם אומר שהוא לא מסביר למה מדד זז. הוא לא אומר דבר על מניה בודדת.</p>`,
    },
  },
  {
    slug: 'yield-curve',
    group: 'market',
    related: ['market-breadth', 'what-is-swing-trading'],
    en: {
      short: 'The yield curve',
      title: 'The Yield Curve: What Inverted, Flat and Normal Mean',
      description: 'The yield curve compares US Treasury rates across maturities. What an inverted curve means, and how Swing Desk labels the gap between 10-year and 2-year yields.',
      body: `<p class="lead">The yield curve shows the interest rate the US government pays on its debt at different maturities, from a few months to 30 years. Its shape is one of the most watched figures in the bond market.</p>
<h2>What the shapes mean</h2>
<ul>
<li><b>Normal:</b> longer loans pay more than shorter ones, which is the usual reward for tying up money for longer.</li>
<li><b>Flat:</b> short and long rates are close together.</li>
<li><b>Inverted:</b> short rates are higher than long rates. This happens when the central bank holds short rates high while the bond market expects lower rates ahead.</li>
</ul>
<h2>How Swing Desk labels it</h2>
<p>The label comes from one number, the 10-year yield minus the 2-year yield, from the rates the US Treasury publishes:</p>
<ul>
<li><b>Inverted:</b> below 0.</li>
<li><b>Flat:</b> from 0 up to 0.5 percentage points.</li>
<li><b>Normal:</b> 0.5 points or more.</li>
</ul>
<p>The label is a description of that number only. The site does not attach a forecast to it. The Treasury publishes the curve after the end of each business day, so the latest figure can be a day old, and it does not change on days when the bond market is closed.</p>
<h2>Why people watch it</h2>
<p>An inverted curve has come before many past US recessions, which is why it attracts attention. But the time between an inversion and a recession has varied widely, by many months and sometimes years. With a limited number of recessions in the record, this is an observation about the past rather than a rule.</p>
<h2>What it does not tell you</h2>
<p>The curve says little about what any one stock will do in the next few weeks. Rates affect sectors differently: banks, real estate and utilities tend to be more directly sensitive to them than others, but the relationship shifts over time.</p>`,
    },
    he: {
      short: 'עקום התשואות',
      title: 'עקום התשואות: מה זה הפוך, שטוח ורגיל',
      description: 'עקום התשואות משווה את הריבית על אג"ח ממשלת ארה"ב לפי משך. מה המשמעות של עקום הפוך, ואיך Swing Desk מתייג את הפער בין תשואת 10 שנים לתשואת שנתיים.',
      body: `<p class="lead">עקום התשואות מראה את הריבית שממשלת ארה"ב משלמת על החוב שלה לפי משך ההלוואה, מכמה חודשים ועד 30 שנה. הצורה שלו היא אחד הנתונים הנצפים ביותר בשוק האג"ח.</p>
<h2>מה המשמעות של הצורות</h2>
<ul>
<li><b>רגיל:</b> הלוואות ארוכות משלמות יותר מקצרות, וזה התגמול הרגיל על נעילת כסף לזמן ארוך יותר.</li>
<li><b>שטוח:</b> הריביות הקצרות והארוכות קרובות זו לזו.</li>
<li><b>הפוך:</b> הריביות הקצרות גבוהות מהארוכות. זה קורה כשהבנק המרכזי מחזיק ריבית קצרה גבוהה, ושוק האג"ח מצפה לריביות נמוכות יותר בהמשך.</li>
</ul>
<h2>איך Swing Desk מתייג אותו</h2>
<p>התווית נובעת ממספר אחד, תשואת 10 שנים פחות תשואת שנתיים, מהשיעורים שמפרסם משרד האוצר האמריקאי:</p>
<ul>
<li><b>הפוך:</b> מתחת ל-0.</li>
<li><b>שטוח:</b> מ-0 ועד 0.5 נקודות אחוז.</li>
<li><b>רגיל:</b> 0.5 נקודות ומעלה.</li>
</ul>
<p>התווית היא תיאור של המספר הזה בלבד. האתר לא מצמיד לה תחזית. משרד האוצר מפרסם את העקום אחרי סוף כל יום עסקים, ולכן הנתון האחרון יכול להיות בן יום, והוא לא משתנה בימים שבהם שוק האג"ח סגור.</p>
<h2>למה עוקבים אחריו</h2>
<p>עקום הפוך הקדים הרבה מהמיתונים בארה"ב בעבר, ולכן הוא מושך תשומת לב. אבל הזמן בין היפוך למיתון השתנה מאוד, בחודשים רבים ולפעמים בשנים. עם מספר מוגבל של מיתונים בתיעוד, זו תצפית על העבר ולא כלל.</p>
<h2>מה הוא לא אומר</h2>
<p>העקום אומר מעט על מה שתעשה מניה מסוימת בשבועות הקרובים. ריבית משפיעה על סקטורים בצורה שונה: בנקים, נדל"ן ותשתיות נוטים להיות רגישים אליה ישירות יותר מאחרים, אבל הקשר משתנה עם הזמן.</p>`,
    },
  },
  {
    slug: 'expected-move-earnings',
    group: 'market',
    related: ['atr', 'position-sizing', 'what-is-swing-trading'],
    en: {
      short: 'Expected move before earnings',
      title: 'Expected Move: What Options Prices Say About an Earnings Report',
      description: 'The expected move is the size of a stock\'s move that options prices imply around an earnings report. How it is calculated and how Swing Desk shows it.',
      body: `<p class="lead">The expected move is the size of a stock's move, up or down, that options prices imply around an event such as an earnings report. It shows how much uncertainty the options market is pricing in, and it says nothing about direction.</p>
<h2>How it is calculated on Swing Desk</h2>
<ul>
<li>It takes the first options expiry after the report date.</li>
<li>It takes the at-the-money straddle at that expiry: a call and a put with a strike price near the current stock price.</li>
<li>The price of the straddle divided by the stock price, as a percentage, is the expected move. A $5 straddle on a $100 stock gives ±5%.</li>
<li>Figures outside the range of 0.3% to 40% are treated as bad data and are not shown.</li>
</ul>
<h2>Why it stops updating</h2>
<p>The figure is frozen at the time of the report. Once the results are out, the options are pricing the next event, so a later figure would not describe this report. That is why the earnings calendar keeps showing it on the report's row after the results arrive: the expected figure can be compared with what actually happened.</p>
<h2>How traders read it</h2>
<p>The expected move is often compared with the actual move. A stock that moves much more than expected surprised the market, and one that moves less was already priced for it. It also shows how much a position held through the report can change in one day, compared with the stock's ordinary <a href="/learn/atr">ATR</a>.</p>
<h2>What it does not tell you</h2>
<p>It is not a limit: stocks regularly move beyond it. It is roughly the size of a typical move implied by prices, not a maximum, and the straddle also includes the time left until expiry, so the figure can be a little larger than the report alone explains when the expiry is days after the report. Options are less liquid in some stocks, which makes their figure less reliable.</p>`,
    },
    he: {
      short: 'תנודה צפויה לפני דוח',
      title: 'תנודה צפויה: מה מחירי האופציות אומרים על דוח',
      description: 'התנודה הצפויה היא גודל התנועה של מניה שמחירי האופציות מגלמים סביב דוח. איך מחשבים אותה ואיך Swing Desk מציג אותה.',
      body: `<p class="lead">התנודה הצפויה היא גודל התנועה של מניה, למעלה או למטה, שמחירי האופציות מגלמים סביב אירוע כמו דוח. היא מראה כמה אי-ודאות שוק האופציות מתמחר, ולא אומרת דבר על הכיוון.</p>
<h2>איך מחשבים אותה ב-Swing Desk</h2>
<ul>
<li>לוקחים את פקיעת האופציות הראשונה אחרי מועד הדוח.</li>
<li>לוקחים את הסטראדל שעל הכסף באותה פקיעה: אופציית קול ואופציית פוט עם מחיר מימוש קרוב למחיר המניה הנוכחי.</li>
<li>מחיר הסטראדל חלקי מחיר המניה, באחוזים, הוא התנודה הצפויה. סטראדל של 5 דולר במניה של 100 דולר נותן ±5%.</li>
<li>נתונים מחוץ לטווח של 0.3% עד 40% נחשבים לנתון שגוי ולא מוצגים.</li>
</ul>
<h2>למה היא מפסיקה להתעדכן</h2>
<p>הנתון מוקפא בזמן הדוח. אחרי שהתוצאות יוצאות, האופציות מתמחרות את האירוע הבא, ולכן נתון מאוחר יותר לא יתאר את הדוח הזה. לכן יומן הדוחות ממשיך להציג אותו בשורת הדוח גם אחרי שהתוצאות מגיעות: אפשר להשוות את הנתון הצפוי למה שקרה בפועל.</p>
<h2>איך סוחרים קוראים אותה</h2>
<p>משווים לעיתים קרובות את התנודה הצפויה לתנועה בפועל. מניה שזזה הרבה יותר מהצפוי הפתיעה את השוק, ומניה שזזה פחות כבר הייתה מתומחרת לכך. היא גם מראה כמה פוזיציה שמוחזקת דרך הדוח יכולה להשתנות ביום אחד, בהשוואה ל-<a href="/he/learn/atr">ATR</a> הרגיל של המניה.</p>
<h2>מה היא לא אומרת</h2>
<p>היא לא גבול: מניות עוברות אותה באופן קבוע. זה בערך הגודל של תנועה טיפוסית שהמחירים מגלמים, לא מקסימום, והסטראדל כולל גם את הזמן שנותר עד הפקיעה, ולכן הנתון יכול להיות מעט גדול ממה שהדוח לבדו מסביר כשהפקיעה מגיעה ימים אחרי הדוח. האופציות פחות נזילות בחלק מהמניות, ואז הנתון שלהן פחות אמין.</p>`,
    },
  },
  {
    slug: 'insider-trading-form-4',
    group: 'market',
    related: ['13f-filings', 'what-is-swing-trading'],
    en: {
      short: 'Insider transactions (Form 4)',
      title: 'Insider Transactions: How to Read SEC Form 4',
      description: 'Company officers, directors and large owners must report their trades on SEC Form 4. What the codes mean, what a planned sale is, and what Swing Desk shows.',
      body: `<p class="lead">In the US, a company's officers, directors and owners of more than 10% of its shares must report their trades in its stock to the SEC, generally within two business days, on a document called Form 4. These are legal and public, and they are a different matter from illegal insider trading.</p>
<h2>What is on the form</h2>
<p>Each transaction has a letter code. Many are not trades in the market at all: A is a grant of shares, M is the exercise of an option, F is shares withheld for tax, G is a gift. A long list of "insider transactions" on many data sites mixes these with real trades.</p>
<h2>What Swing Desk shows</h2>
<ul>
<li>Only <b>P</b> (a purchase in the open market) and <b>S</b> (a sale in the open market), from the part of the form that covers actual shares and not derivatives.</li>
<li>The last six months, for the companies in the S&P 500 and Nasdaq 100, stocks that recently appeared in the scanner, and stocks on users' watchlists.</li>
<li>A sale under a <b>Rule 10b5-1 plan</b> is marked as planned. Such a plan is set up in advance, so the sale date is not a decision made on that day.</li>
</ul>
<h2>Why purchases and sales are read differently</h2>
<p>Insiders sell for many reasons that have nothing to do with the company's prospects: taxes, a home, diversification, a plan set a year earlier. An open-market purchase has fewer such explanations, since it uses the person's own money, and this is why many people pay more attention to purchases. That is a common way of reading the data, not a rule about what happens next.</p>
<h2>What it does not tell you</h2>
<p>A filing shows what was done, not why. Reported dates can lag slightly. An officer's purchase may be small compared with their pay or holdings, and even a large one does not say anything about the next few weeks of the stock's price. Where the site shows a purchase, it shows the fact, the size and the role of the person, and nothing further.</p>`,
    },
    he: {
      short: 'עסקאות בעלי עניין (טופס 4)',
      title: 'עסקאות בעלי עניין: איך קוראים טופס 4 של SEC',
      description: 'מנהלים, דירקטורים ובעלי מניות גדולים חייבים לדווח על עסקאות בטופס 4 של SEC. מה אומרים הקודים, מהי מכירה מתוכננת, ומה מציג Swing Desk.',
      body: `<p class="lead">בארה"ב, מנהלי חברה, דירקטורים ובעלי יותר מ-10% מהמניות חייבים לדווח ל-SEC על העסקאות שלהם במניה, בדרך כלל תוך שני ימי עסקים, במסמך שנקרא טופס 4. הדיווחים האלה חוקיים וגלויים, וזה עניין אחר לגמרי ממסחר אסור במידע פנים.</p>
<h2>מה יש בטופס</h2>
<p>לכל עסקה יש קוד של אות. הרבה מהן הן בכלל לא עסקאות בשוק: A היא הענקת מניות, M היא מימוש אופציה, F היא מניות שנוכו למס, G היא מתנה. רשימה ארוכה של "עסקאות בעלי עניין" באתרי נתונים רבים מערבבת אותן עם עסקאות אמיתיות.</p>
<h2>מה Swing Desk מציג</h2>
<ul>
<li>רק <b>P</b> (קנייה בשוק) ו-<b>S</b> (מכירה בשוק), מהחלק של הטופס שעוסק במניות עצמן ולא בנגזרים.</li>
<li>ששת החודשים האחרונים, לחברות ב-S&P 500 וב-Nasdaq 100, למניות שהופיעו לאחרונה בסורק, ולמניות ברשימות מעקב של משתמשים.</li>
<li>מכירה לפי <b>תוכנית 10b5-1</b> מסומנת כמתוכננת. תוכנית כזאת נקבעת מראש, ולכן מועד המכירה אינו החלטה שהתקבלה באותו יום.</li>
</ul>
<h2>למה קוראים קניות ומכירות בצורה שונה</h2>
<p>בעלי עניין מוכרים מסיבות רבות שאין להן קשר לעתיד החברה: מסים, דירה, פיזור, תוכנית שנקבעה לפני שנה. לקנייה בשוק יש פחות הסברים כאלה, כי היא נעשית בכסף של האדם עצמו, ולכן רבים שמים לב יותר לקניות. זו דרך נפוצה לקרוא את הנתונים, לא כלל לגבי מה שיקרה בהמשך.</p>
<h2>מה זה לא אומר</h2>
<p>דיווח מראה מה נעשה, לא למה. התאריכים המדווחים יכולים להתעכב מעט. קנייה של מנהל יכולה להיות קטנה ביחס לשכר או לאחזקות שלו, וגם קנייה גדולה לא אומרת דבר על השבועות הקרובים במחיר המניה. כשהאתר מציג קנייה, הוא מציג את העובדה, את הגודל ואת התפקיד של האדם, ולא מעבר לזה.</p>`,
    },
  },
  {
    slug: '13f-filings',
    group: 'market',
    related: ['insider-trading-form-4', 'rs-rating'],
    en: {
      short: 'Fund holdings (Form 13F)',
      title: 'Form 13F: What Large Funds Report Holding, and What It Leaves Out',
      description: 'Large US investment managers report their stock positions each quarter on SEC Form 13F. What the filing shows, its delay and limits, and what Swing Desk displays.',
      body: `<p class="lead">Form 13F is a quarterly report to the SEC in which large US investment managers list their stock positions. Any manager with over $100 million in the covered securities must file one.</p>
<h2>The timing</h2>
<p>The report is due within 45 days after the end of each calendar quarter. So positions as of the end of March can appear as late as mid-May. By then, any of them may have been sold.</p>
<h2>What it leaves out</h2>
<ul>
<li><b>Short positions</b> are not reported, so a position that looks like a bet on a rise can be one side of a hedge.</li>
<li><b>Non-US holdings, most bonds, and cash</b> are also generally outside the report, so the percentages describe only the reported part of a portfolio.</li>
<li><b>Options</b> are reported in a way that can make a position look different from its real exposure.</li>
<li>A manager can ask the SEC to keep part of a position confidential. A fund may also file a notice (13F-NT) saying that its positions are reported by another filer.</li>
</ul>
<h2>What Swing Desk shows</h2>
<ul>
<li>The latest 13F of 11 well-known managers, including Berkshire Hathaway, Pershing Square, Appaloosa, Baupost, Third Point, Bridgewater and Tiger Global, each with its largest positions. Option lines are left out.</li>
<li>On a stock's page: which of those funds reported holding it, and any change from the previous quarter.</li>
<li>The date of each report, so that it is clear how old the positions are.</li>
</ul>
<h2>What it does not tell you</h2>
<p>A fund's position says what it held at some date in the past, not what it holds now or why. A fund's size and its investors' time frame can be very different from anyone reading the filing. A new position in a report is news about the past, and by the time it is public the price may already reflect it.</p>`,
    },
    he: {
      short: 'אחזקות קרנות (טופס 13F)',
      title: 'טופס 13F: מה קרנות גדולות מדווחות שהן מחזיקות, ומה נשאר בחוץ',
      description: 'מנהלי השקעות גדולים בארה"ב מדווחים על אחזקות המניות שלהם כל רבעון בטופס 13F של SEC. מה הדיווח מראה, העיכוב והמגבלות שלו, ומה מציג Swing Desk.',
      body: `<p class="lead">טופס 13F הוא דיווח רבעוני ל-SEC שבו מנהלי השקעות גדולים בארה"ב מפרטים את אחזקות המניות שלהם. כל מנהל עם יותר מ-100 מיליון דולר בניירות הערך הרלוונטיים חייב להגיש אותו.</p>
<h2>התזמון</h2>
<p>הדיווח צריך להיות מוגש עד 45 יום אחרי סוף כל רבעון קלנדרי. כך שאחזקות של סוף מרץ יכולות להופיע גם באמצע מאי. עד אז כל אחת מהן יכולה להיות כבר מכורה.</p>
<h2>מה נשאר בחוץ</h2>
<ul>
<li><b>פוזיציות שורט</b> לא מדווחות, ולכן פוזיציה שנראית כהימור על עלייה יכולה להיות צד אחד של גידור.</li>
<li><b>אחזקות מחוץ לארה"ב, רוב האג"ח והמזומן</b> גם הם בדרך כלל מחוץ לדיווח, ולכן האחוזים מתארים רק את החלק המדווח של התיק.</li>
<li><b>אופציות</b> מדווחות בצורה שיכולה לגרום לפוזיציה להיראות שונה מהחשיפה האמיתית שלה.</li>
<li>מנהל יכול לבקש מ-SEC לשמור חלק מפוזיציה בסודיות. קרן יכולה גם להגיש הודעה (13F-NT) שאומרת שהאחזקות שלה מדווחות על ידי גורם אחר.</li>
</ul>
<h2>מה Swing Desk מציג</h2>
<ul>
<li>טופס 13F האחרון של 11 מנהלים מוכרים, ביניהם Berkshire Hathaway, Pershing Square, Appaloosa, Baupost, Third Point, Bridgewater ו-Tiger Global, עם האחזקות הגדולות של כל אחד. שורות של אופציות לא מוצגות.</li>
<li>בעמוד של מניה: אילו מהקרנות האלה דיווחו שהן מחזיקות בה, וכל שינוי מהרבעון הקודם.</li>
<li>התאריך של כל דיווח, כדי שיהיה ברור בנות כמה האחזקות.</li>
</ul>
<h2>מה זה לא אומר</h2>
<p>אחזקה של קרן אומרת מה היא החזיקה בתאריך כלשהו בעבר, לא מה היא מחזיקה עכשיו או למה. הגודל של קרן וטווח הזמן של המשקיעים בה יכולים להיות שונים מאוד מאלה של מי שקורא את הדיווח. פוזיציה חדשה בדיווח היא חדשה על העבר, ועד שהיא מתפרסמת המחיר יכול כבר לשקף אותה.</p>`,
    },
  },
];
