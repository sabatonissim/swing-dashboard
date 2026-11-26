"""Shared English->Hebrew translation fixes — used by pipeline_b_news_aggregator.py (macro news, in GitHub Actions) and
api_server.py (stock news + article previews, on Render). Must sit in the repo ROOT next to both.

Translation quality (v18, 26.10.2026 — measured on live headlines, see CLAUDE.md 27.3ג).
The old method masked ~75 finance terms with placeholders ("Q12Q") before Google and put the Hebrew back afterwards.
That did real damage: Google read "Q1Q"/"Q2Q" as "Q1"/"Q2" — "Await Fed Rate Decision" became "waiting for the Fed in
the first quarter", "Central Bank Decisions" became "decisions in the second quarter" — and the sentence lost the gender
of the hidden noun ("מלחמת סחר ... יסתיים"). Putting the Hebrew term inside the English text was tested too and also
broke the grammar. What works best: let Google translate the WHOLE sentence, after
  1) EN_REWRITES — a few market idioms rewritten into plain English Google gets right ("tick higher" was
     "מתקתקות גבוה יותר", "Pre-Bell" was "לפני הכדור"), and before
  2) HE_FIXES — known wrong Hebrew renderings replaced with the correct term ("סחיטה קצרה" -> "לחיצת שורטים").
Add to these two lists when a bad translation is spotted on the site.
"""
import html
import re

EN_REWRITES = [
    (r"\btick(?:s|ed|ing)?\s+(?:higher|up)\b", "rise slightly"),
    (r"\btick(?:s|ed|ing)?\s+(?:lower|down)\b", "fall slightly"),
    (r"\bpre-?bell\b", "before the market open"),
    (r"\bpre-?market\b", "before the market open"),
    (r"\brate call\b", "rate decision"),
    (r"\bfizzl(?:e|es|ed|ing)\b", "fading"),
    (r"\bnon-?farm payrolls\b", "US jobs report"),
    (r"\b(?:beat|beats|top|tops|topped)\s+(estimates|expectations|forecasts)\b", r"came in above \1"),
    (r"\b(?:miss|misses|missed)\s+(estimates|expectations|forecasts)\b", r"came in below \1"),
    (r"\brisk-off\b", "cautious"),
    (r"\brisk-on\b", "risk-seeking"),
    # v19 (live feed 27.10.2026): each one came out wrong in Hebrew
    (r"\bstrik(?:e|es|ing)\s+(an?\s+)?deal\b", "signs a deal"),             # "Ripple strikes deal" -> "שביתות Ripple"
    (r"\bstruck\s+(an?\s+)?deal\b", "signed a deal"),
    # v73 (23.11.2026): "Trump warns of new strikes" in a story on Yemen's Houthis -> "שביתות" (labor strikes). Only where the
    # headline names a war; a strike of workers stays a strike.
    (r"^(?=.*\b(?:iran\w*|israel\w*|houthis?|yemen|gaza|hezbollah|lebanon|syria\w*|russia\w*|ukrain\w*|kremlin|missiles?|drones?|airstrikes?|"
     r"military|pentagon)\b)(?!.*\b(?:workers?|unions?|strikers?|walkouts?|labor|labour|pay)\b)(.*?)\b((?:(?:new|more|fresh|further|us|"
     r"american|russian|israeli|iranian|drone|missile|air|retaliatory|military)\s+)+)strikes\b", r"\1\2attacks"),
    # "Gold bounces after 9% weekly rout" -> "מסלול שבועי"
    (r"\b(weekly|monthly|daily)\s+rout\b", r"\1 plunge"),
    (r"\b(stock|stocks|market|tech|ai|chip|bond|crypto)\s+rout\b", r"\1 sell-off"),
    (r"\bbeat[- ]and[- ]raise\b", "better-than-expected results and raised outlook"),  # -> "פעימות והעלאת"
    (r"\bearnings beat\b", "better-than-expected earnings"),                 # -> "הרווחים מקציפים"
    (r"\bmidterms\b", "midterm elections"),                                 # -> "בחינות האמצע"
    (r"\bcrypto\b", "cryptocurrency"),                                      # -> "אנוסים" (!)
    (r"\bCDs\b", "bank deposits"),                                          # -> "תקליטורים"
    (r"\bconsumer discretionary\b", "consumer cyclical"),                   # -> "שיקול דעת צרכניות"
    # only after a market word: "jumped 300 points higher" / "policy drift" must stay as they are
    (r"\b(wall street|futures|stocks|markets?|s&p 500|nasdaq|dow)\s+(?:point|points|pointing)\s+(higher|lower)\b",
     r"\1 signal a \2 open"),                                               # "Wall Street points higher" -> "זוכה לנקודות"
    (r"\b(wall street|futures|stocks|markets?|s&p 500|nasdaq|dow|shares)\s+drift(?:s|ed|ing)?\b",
     r"\1 trade quietly"),                                                  # "Wall Street drifts" -> "נסחפת"
    (r"\bon deck\b", "coming up"),                                          # -> "בסיפון"
    (r"\b(courts?)\s+rul(?:e|es|ed)\b", r"\1 decided"),                     # "Courts Rule" -> "כלל בתי המשפט"
    # v28 (live feed 3.11.2026)
    (r"\binch(?:es|ed|ing)?\s+(?:higher|up)\b", "rise slightly"),            # "MU, SNDK inch higher" -> "אינץ' גבוה יותר"
    (r"\binch(?:es|ed|ing)?\s+(?:lower|down)\b", "fall slightly"),
    # "strikes fan Fed inflation fears" -> "האוהדים"; v44: "Hot US Inflation Fuels Fed Rate Hike Bets" -> "הימורי דלקים חמים"
    (r"\b(?:fan(?:s|ned|ning)?|(?<!fossil )(?<!jet )fuel(?:s|l?ed|l?ing)|stok(?:e|es|ed|ing))\s+((?:[\w-]+\s+){0,4}?)"
     r"(fears|concerns|worries|speculation|hopes|bets|expectations)\b", r"increase \1\2"),
    # v44 (live feed 10.11.2026)
    (r"\b(cpi|ppi|pce|inflation|jobs|payrolls|gdp|data)\s+print\b", r"\1 reading"),        # "CPI print" -> "הדפסת מדד"
    (r"(?<!interest )(?<!interest-)\brate[- ]hike(s?)\b", r"interest rate hike\1"),   # "Rate-Hike Betting" -> "העלאת שיעור"
    (r"(?<!interest )(?<!interest-)\brate[- ]cut(s?)\b", r"interest rate cut\1"),
    (r"\bcement(s|ed|ing)?\s+((?:[\w-]+\s+){0,4}?)(bets|expectations|hopes|case|view)\b", r"strengthen\1 \2\3"),   # -> "מלט את הימורי"
    (r"\b(stocks|shares|futures|equities|markets?|wall street|s&p 500|nasdaq|dow)\s+slide\b", r"\1 fall"),    # -> "גולשות"
    (r"\b(stocks|shares|futures|equities|markets?|wall street|s&p 500|nasdaq|dow)\s+slides\b", r"\1 falls"),
    (r"\binflection (year|point)\b", r"turning-point \1"),                 # -> "שנת פיתול"
    # v28d: Wall Street slang for an earnings report — "Ahead of the Print" -> "לפני ההדפסה"
    (r"\b(ahead of|before|after|into|for)\s+(?:the|its|their)\s+(?:earnings\s+)?print\b", r"\1 the earnings report"),
    (r"\b(earnings|q[1-4])\s+print\b", r"\1 report"),
    # v47 (live feed 11.11.2026): "CoreWeave stock pops 15% after outlook raise eases AI slowdown concerns" came out as
    # "צונחת ב-15% לאחר שתחזית העלתה את החששות" — the OPPOSITE of the news. With "jumps" Google gets both halves right
    # (checked against the same endpoint).
    (r"\bpops\b(?=\s+(?:\d|(?:as|after|on|amid|to)\b))", "jumps"),
    (r"\bpopped\b(?=\s+(?:\d|(?:as|after|on|amid|to)\b))", "jumped"),
    (r"\b(stock|stocks|shares|futures|prices?)\s+pop\b", r"\1 jump"),
    # "Dems Reveal ..." -> "הדגים חושפים"; "Top Senate Dem says" -> "הסנאט העליון אומר"
    (r"\btop (senate|house) dem(?:ocrat)?\b", r"the senior Democrat in the \1"),
    (r"\btop (senate|house) republican\b", r"the senior Republican in the \1"),
    (r"\bdems\b", "Democrats"),
    (r"\bdem\b(?=\s+(?:says|said|senator|lawmakers?|leader))", "Democrat"),
    # v51 (live feed 15.11.2026): "GOP midterm losses" -> "הפסדי אמצע הטווח של הרפובליקה הצרפתית";
    # "after every midterm since 1950" -> "כל טווח אמצע"
    (r"\bGOP\b", "Republican"),
    (r"\bmidterm\b(?![\s-]+elections?)", "midterm election"),
    # "Fed minutes" -> "דקות הפד"
    (r"\b(fed|fomc|ecb|boe|boj|meeting)\s+minutes\b", r"\1 meeting protocol"),
    # v38c: "Visa slips" -> "תלושי ויזה"
    (r"\bslips\b", "falls slightly"),
    (r"\bslipped\b", "fell slightly"),
    (r"\bslip(?=\s+(?:as|after|on|in|amid|while)\b)", "fall slightly"),
    # v67 (key events 21.11.2026; each checked against clients5 from home, before -> after):
    # "Oil Posts Third Weekly Drop" -> "שמן מפרסם ירידה" -> "מחירי הנפט רושמים ירידה שבועית שלישית"
    (r"\b(oil|crude|gold|silver|copper)\s+posts?\s+(?:a\s+)?record\b", r"\1 prices reach a record"),   # "Gold posts record high"
    (r"\b(oil|crude|gold|silver|copper)\s+posts?\b", r"\1 prices record"),
    (r"\b(oil|crude|gold|silver|copper)\s+posted\b", r"\1 prices recorded"),
    # "Oil prices edge higher" -> "עולים בעליות"
    (r"\bedg(?:e|es|ed|ing)\s+(?:higher|up)\b", "rise slightly"),
    (r"\bedg(?:e|es|ed|ing)\s+(?:lower|down)\b", "fall slightly"),
    # "Canada Auto Plant The 'Front Line'" -> "קנדה נטעה" -> "מפעל הרכב בקנדה"
    (r"\b(auto|car|chip|steel|aluminum|battery|assembly|manufacturing|meatpacking|semiconductor)\s+plant(s?)\b", r"\1 factory\2"),
    # Reuters labels: "Instant view: Nasdaq plunges" -> "תצוגה מיידית: ..."
    (r"^\s*(?:instant view|view|factbox|explainer|analysis|breakingviews|commentary)\s*[:\-–—]\s*", ""),
    # v70: "off 10% from their records" -> "הנחה של 10% מהרשומות שלהם" (23.11.2026)
    (r"\b(?:off|down)\s+(\d+(?:\.\d+)?%)\s+from\s+(their|its)\s+records?\b(?!\s+highs?)", r"down \1 from \2 record highs"),
    # "J.P. Morgan prices in stunning shift on Fed rate-hike call" -> "J.P. מורגן מחירים בשינוי מדהים בבקשת"
    (r"\b(morgan|goldman(?: sachs)?|citi(?:group)?|barclays|ubs|bofa|traders|markets|investors|wall street)\s+price(?:s|d)?\s+in\b",
     r"\1 now expect"),
    (r"\b(interest rate (?:hike|cut)s?|rate (?:hike|cut)s?)\s+call\b", r"\1 forecast"),
    # v74 (24.11.2026; each checked against clients5 from home):
    # "ECB's Simkus Says It's 'Prudent' to Hike as Inflation Broadens" -> "'שפוי' לעלות". A central bank's hike with nothing
    # after it that is hiked; "to hike prices" stays.
    (r"^(?=.*\b(?:fed|fomc|ecb|boe|boj|boc|rba|rbnz|central banks?)\b)(.*?)\bto hike\b"
     r"(?=\s*$|\s*[,.;:]|\s+(?:as|after|again|in|by|if|before|until|amid|further|once|twice|when|while)\b)", r"\1to raise interest rates"),
    # "AI market whipsaw puts ..." -> "שוט שוק AI"
    (r"\bmarket whipsaw\b", "market volatility"),
    # "Chip, AI stocks retreat" -> "צ'יפ, מניות בינה מלאכותית"
    (r"\bchip, (AI|tech|software|memory) (stocks|shares)\b", r"chip and \1 \2"),
    # "Nvidia-Led Rally" -> "עליית Nvidia-Led"
    # v76: also -fueled / -sparked; with the theme before "rally" ("Nvidia-fueled AI rally fades" -> "עצירת AI", "AI rally" -> "עצרת")
    (r"\b([A-Z][\w&.]+)-(?:led|fuell?ed|sparked|driven) (AI|chip|tech|technology|memory|software) rally\b", r"\1-driven surge in \2 stocks"),
    (r"\b(?:(?:the|an?)\s+)?(?<!-)(AI|chip|tech|memory|software) rally\b(?=\s+(?:fades?|cools?|stalls?|slows?|loses|continues?|extends?|broadens?|"
     r"resumes?|pauses?|falters?|ends?)\b|\s*$)", r"the surge in \1 stocks"),
    (r"\b([A-Z][\w&.]+)-(?:led|fuell?ed|sparked) (rally|rebound|sell-?off|surge|slump|decline|gains|drop)\b", r"\2 led by \1"),
    # v76 (24.11.2026; each checked against clients5 from home): "Chip Stocks" in capitals -> "מניות צ'יפ" ("chip stocks" is right)
    (r"\bchip stocks\b", "chip stocks"),
    (r"\bwall st\b\.?(?!\w)", "Wall Street"),
    # "Germany Eyes EU Sanctions" -> "גרמניה מבטלת סנקציות" (the opposite); "Apple eyes $5 trillion"
    (r"(?<=\w )(?<!all )(?<!his )(?<!her )(?<!their )eyes (?=\$|\d)", "aims for "),
    (r"(?<=[a-z] )(?<!all )(?<!his )(?<!her )(?<!their )(?<!s' )eyes (?!on\b|of\b|and\b|are\b|were\b|turn|as\b|to\b|for\b|in\b|wide\b|open|\$|\d)", "considers "),
    # "bargain buying" -> "קניית מציאה"; "margin pressure" -> "לחץ שולי"; "US inflation data awaited" -> "נתוני האינפלציה ממתינים"
    (r"\bbargain[- ](?:buying|hunting)\b", "buying at low prices"),
    (r"\b(PC|memory|chip|cloud|gross) margin pressures?\b", r"pressure on \1 profit margins"),      # v77b: "PC pressure on profit margins"
    (r"\bmargin pressures?\b", "pressure on profit margins"),
    (r"[;,]\s*((?:US |U\.S\. |key )?(?:inflation|jobs|cpi|pce|ppi|gdp|payrolls) (?:data|report|reading)) awaited\b", r"; markets await \1"),
    # v76b: "EPS of $0.10 beats by $0.02" -> "EPS של 0.10 דולר ב-0.02 דולר" (the beat was lost); "short interest" -> "ריבית קצרה"
    (r"\b(EPS|revenue) of (-?\$[\d.]+[MBK]?) beats by (\$[\d.]+[MBK]?)(?!\w)", r"\1 of \2 is \3 above estimates"),
    (r"\b(EPS|revenue) of (-?\$[\d.]+[MBK]?) misses by (\$[\d.]+[MBK]?)(?!\w)", r"\1 of \2 is \3 below estimates"),
    (r"\bshort interest(?: positions?)?\b", "short positions"),
    # "Bond Market's Go-To Trade ... Roiled by Stocks Retreat" -> "סחר האג"ח ... שנובע מנסיגה"
    (r"\bgo-to trade for\b", "popular trade on"),
    (r"\broiled by stocks? retreat\b", "hurt by fall in stocks"),
    # "Asian stocks rally as" -> "מתגברות"; "Dollar Tree rallies on Q3 results" -> "מתגבר על תוצאות"
    (r"\b(stocks|shares|futures|equities|markets) rally\b(?=\s+(?:as|after|on|amid)\b)", r"\1 climb"),
    (r"\brallies\b(?=\s+(?:on|after|as|amid)\b|\s+\d)", "jumps"),
    # "undershoots forecasts" -> "מטה את התחזיות"
    (r"\bundershoots?\s+(forecasts|expectations|estimates)\b", r"comes in below \1"),
    (r"\bovershoots?\s+(forecasts|expectations|estimates)\b", r"comes in above \1"),
    # v77 (25.11.2026; each checked against clients5 from home):
    # "Huang Relief Rally Fades" -> "עצרת הסיוע"; "Nvidia bounce fades" -> "הקפצת Nvidia"; "AI rally proves fleeting" -> "עצרת AI"
    (r"\brelief rally\b", "rebound"),
    (r"\bbounce(?=\s+(?:fades?|fizzles?|fading|stalls?|cools?)\b)", "rebound"),
    (r"(?<!-)\b(AI|chip|tech|memory|software) rally reversal\b", r"reversal in \1 stocks"),
    (r"(?<!-)\b(AI|chip|tech|memory|software) rally (proves?)\b", r"the surge in \1 stocks \2"),
    (r"\bsingle session\b", "single trading day"),
    # "Stocks Settle Mostly Lower" -> "מתיישבות"; "WTI trades below settlement ... crude build" -> "מתחת להתנחלות ... בנייה קטנה"
    (r"\b(stocks|shares|futures|oil|crude|brent|wti|gold|silver|copper|prices|wall street)\s+settle(s|d)?\b(?=\s+(?:mostly|at|up|down|higher|lower|near|above|below|little|mixed|flat)\b)",
     lambda m: m.group(1) + " close" + (m.group(2) or "")),
    (r"\b(above|below)\s+settlement\b", r"\1 the closing price"),
    (r"\bcrude (?:oil )?build\b", "rise in crude oil inventories"),
    (r"\bcrude (?:oil )?draw\b", "drop in crude oil inventories"),
    (r"\bon an?\s+rout\s+in\b", "on plunge in"),
    (r"\bmajor\s+sell-?off\b", "major wave of selling"),
    # "Risk of Fed Hikes Stalling Economy" -> "עליית עליות הפד בכלכלה"; "Fed hike bets" -> "הימורי עלייה"; "Case for Another Hike" -> "לטיול נוסף"
    (r"\brisk of ((?:fed|ecb|boe|boj|rba)\b[\w\s-]{3,40}?) stalling (?:the )?economy\b", r"risk that \1 stall the economy"),
    (r"\b(fed|ecb|boe|boj|boc|rba|rbnz) hike(s?)\b(?=\s+(?:bets?|stall|stalling|path|odds|fears|expectations|hopes|risk|risks|wagers|speculation|looms?|ahead)\b|\s*[,.;:]|\s*$)",
     r"\1 interest rate hike\2"),
    (r"^(?=.*\b(?:fed|fomc|ecb|boe|boj|boc|rba|rbnz|central banks?)\b)(.*?)\b(another|further|more) hike(s?)\b", r"\1\2 interest rate hike\3"),
    # "Dell reports Q3 beat on revenue and earnings" -> "רווחים ורווחים"; "amid earnings beats" -> "פעימות הרווח"
    (r"\breports?\s+(q[1-4]\s+)?beat on (revenues?|sales) and (earnings|profit)\b",
     lambda m: "reports " + (m.group(1) or "") + m.group(2) + " and " + m.group(3) + " above estimates"),
    (r"\bearnings beats\b", "better-than-expected earnings"),
    (r"\b(tops?|beats?)\s+(q[1-4])\s+(earnings(?: and revenues?)?|revenues?)\s+estimates\b", r"reports \2 \3 above estimates"),
    (r"\bmisses\s+(q[1-4])\s+(earnings(?: and revenues?)?|revenues?)\s+estimates\b", r"reports \1 \2 below estimates"),
    (r"\b(q[1-4]) earnings match estimates\b", r"\1 earnings in line with estimates"),
    (r"\b(earnings|results|profits?|sales|revenues?) (?:crush(?:es|ed)?|smash(?:es|ed)?) (estimates|expectations|forecasts)\b", r"\1 are far above \2"),
    (r"\bdemand roars\b", "demand surges"),
    (r"\b(stock|stocks|shares) tanks?\b", r"\1 plunges"),
    (r"\bblowout (earnings|results|quarter)\b", r"very strong \1"),
    # "strong guidance" -> "הנחיה / הדרכה חזקה"
    (r"(?<!forward )(?<!fed )(?<!policy )(?<!rate )\bguidance\b", "outlook"),
    (r"\b(?:sour|sours|soured) on\b", "lose faith in"),
    (r"\binflation release\b", "inflation data"),
    (r"[;,]\s*((?:US |U\.S\. |key )?(?:cpi|pce|ppi)(?: inflation)?) awaited\b", r"; markets await \1"),
    (r"\b(investors|traders|markets|analysts) eye\b", r"\1 watch"),
    # the currencies: "Aussie slumps" -> "אוסי צונחת", "Kiwi Slides" -> "קיווי גולש"
    (r"\baussie\b(?=\s+(?:and\b|set\b|slumps?|falls?|slides?|rises?|drops?|jumps?|weakens?|tumbles?|steadies|slips|dollar\b)|\s*[,;]|\s*$)(?:\s+dollar\b)?", "Australian dollar"),
    (r"\bkiwi\b(?=\s+(?:and\b|set\b|slumps?|falls?|slides?|rises?|drops?|jumps?|weakens?|tumbles?|dollar\b))(?:\s+dollar\b)?", "New Zealand dollar"),
    (r"\bdollar rallies\b", "dollar jumps"),
    # v77b (25.11.2026, a day tech fell; each checked against clients5 from home): "Stock Sell-Off" -> "מכירת מניות", "tech sell-off" ->
    # "המכירה הטכנולוגית", "AI jitters" -> "ריצוד AI", "A.I. Players" -> "A.I. שחקנים", "Nvidia whiplash" -> "צליפת שוט"
    (r"\bA\.I\.(?=\s)", "AI"),
    (r"\b(AI|chip) players\b", r"\1 companies"),
    (r"(?<!-)\b(AI|chip|tech|bond|memory|software) sell-?off\b", lambda m: "sharp fall in " + (m.group(1) + "s" if m.group(1).lower() == "bond" else m.group(1) + " stocks")),
    (r"\b(AI|chip|tech|bank|memory|software) stocks? sell-?off\b", r"sharp fall in \1 stocks"),     # not "Tech sharp fall in stocks"
    (r"\bstocks? sell-?off\b", "sharp fall in stocks"),
    (r"\b(AI|tech|chip|market|inflation|tariff) jitters\b", r"\1 worries"),
    (r"\b(AI|chip|tech) trade struggles\b", r"\1 stocks struggle"),
    (r"\bto regain (?:its |their )?footing\b", "to recover"),
    (r"\bwhiplash\b(?= tests?\b)", "volatility"),
    (r"\btakes? (?:a )?breather\b", "pauses"),
    # "as new US tariffs loom" -> "מתנשאים"; "Aussie jumps on CPI" -> "זינק במדד המחירים"
    (r"\b(tariffs|data|deadline|meeting|decision|report) (loom)(s?)\b(?=\s*[,;:.]|\s*$)", lambda m: m.group(1) + " approach" + ("es" if m.group(3) else "")),
    (r"\b(jumps|rises|climbs|gains|falls|slips|drops) on (CPI|PCE|PPI)\b(?! data)", r"\1 after \2 data"),
    # "rate expectations" -> "ציפיות תעריפים"; "extend losses" -> "מאריכות את ההפסדים"; "revenue miss" -> "הפסקת הכנסות"
    (r"(?<!interest )(?<!growth )(?<!inflation )(?<!tax )(?<!unemployment )(?<!exchange )\brate expectations\b", "interest rate expectations"),
    (r"\bextend(s?) losses\b", r"continue\1 to fall"),
    (r"\brevenue miss\b(?! (?:the )?(?:estimates|expectations|forecasts|consensus|street|wall|views))", "lower-than-expected revenue"),
    (r"(?<!are )(?<!is )(?<!be )(?<!were )(?<!remain )\bat odds over\b", "disagree over"),        # -> "מתנגדים לסיבות"
    (r"\bcautious earnings outlook\b", "cautious profit forecast"),
    (r"\b(overshadows?) (?:quarterly|q[1-4]) beat\b", r"\1 better-than-expected quarter"),
    (r"\b(report|data) to (provide|show|give)\b", r"\1 will \2"),
    # v77b (26.11.2026, each checked against clients5 from home): "may not be done" -> "אולי לא נעשה"; "in first since Cold War" ->
    # "בהתחלה מאז"; "shrug off latest US tariff salvo" -> "מתנערים מהמחסום"; "Hawkish Bank of Japan member" -> "חבר בבנק הוקיש ביפן";
    # "Bank of Japan's Tamura" -> "בנק אוף יפן Tamura"; "Monitor Stock Slump" -> "צניחה של המניה"; "as tech, bank stocks rebound" ->
    # "ככל שהטכנולוגיה מתקדמת"
    (r"\bmay not be done\b(?=\s*[.,;:!]|\s*$)", "may not be over"),
    (r"\s+in first since\b", ", the first time since"),
    (r"\bshrug(s?) off\b", r"ignore\1"),
    (r"\btariff salvo\b", "round of tariffs"),
    (r"\bhawkish (Bank of Japan|Bank of England|ECB|Fed|BOJ|BOE) (?:member|official|policymaker)\b", r"\1 policymaker with a hawkish stance"),
    # only a person's name before a verb ("Bank of Japan's December decision" is not touched)
    (r"\b(Bank of Japan|Bank of England)['’]s ((?-i:[A-Z][a-z]+))(?=\s+(?:says|said|considers|sees|signals|warns|backs|calls|flags|urges|"
     r"aims|eyes|favors|wants|seeks|expects|hints)\b)", r"\1 policymaker \2"),
    (r"\bstock slump\b", "stock market slump"),
    (r"\b(tech|bank|chip|energy|mining|auto|retail), (tech|bank|chip|energy|mining|auto|retail) stocks\b", r"\1 stocks and \2 stocks"),
    # v77c (26.11.2026, each checked against clients5 from home): "Hawkish Fed sentiment" -> "סנטימנט הפד ההוקיש"; "tech holds court" ->
    # "מחזיקה במשפט"; "red tape" -> "הביטאון"; "Gold drifts lower ... fears loom" -> "נסחף נמוך יותר ... מתנשאים"
    (r"\bhawkish (Fed|ECB|BOJ|BOE) sentiment\b", r"expectations of a hawkish \1"),
    # only as the subject of a verb ("hawkish Fed bets", "Hawkish Fed comments" are not touched)
    (r"(?<!a )(?<!the )\bhawkish (Fed|ECB|BOJ|BOE)\b(?=\s+(?:risks?|could|may|might|would|will|threatens?|looms?|weighs?|hits?|pushes?|keeps?|"
     r"puts?|sends?|spooks?|rattles?|drags?)\b)", r"a hawkish \1"),
    (r"\b(tech|chips?|AI|banks?|energy) holds? court\b", r"\1 stocks lead"),
    (r"\bred tape\b", "bureaucracy"),
    (r"\bdrifts (lower|higher)\b", lambda m: ("falls" if m.group(1).lower() == "lower" else "rises") + " slightly"),     # "equities drift lower" is fine (v44)
    (r"\b(fears|worries|concerns) loom\b", r"\1 grow"),
    # v78b (26.11.2026, clients5): "after Wall Street pares sharp losses" -> "ספגה הפסדים חדים" (the opposite: the losses got
    # smaller); "Sterling Steady as Dollar Pauses" -> "Sterling Steady כמו הפסקות דולרים"; "investingLive European session wrap:"
    # -> "השקעה חיה בסשן אירופאי"
    (r"\bpares\b", "cuts"), (r"\bpared\b", "cut"), (r"\bparing\b", "cutting"),
    (r"\bsterling\b(?=\s+(?:steady|steadies|holds|rises|falls|slips|jumps|gains|drops|edges|firms|weakens|dips|slides|climbs)\b)", "British pound"),
    (r"\b(pound|euro|yen|dollar|yuan) steady as (?:the )?", r"\1 holds steady as the "),
    (r"^\s*investinglive\s+", ""),
    (r"\b(European|Asian|US|North American|Americas) session wrap\b", r"Summary of the \1 trading session"),
]

HE_FIXES = [
    ("מהרשומות שלהם", "מהשיאים שלהם"),          # v70
    # v51 (live feed 15.11.2026)
    ("דקות הפד", "פרוטוקול הפד"),
    ("דקות ה-FOMC", "פרוטוקול ה-FOMC"),
    # v44 (live feed 10.11.2026): "rate" read as a tariff / a proportion, not the interest rate
    ("העלאת שיעור", "העלאת ריבית"),
    ("העלאות שיעור", "העלאות ריבית"),
    ("הורדת שיעור", "הורדת ריבית"),
    ("העלאת תעריפים", "העלאת ריבית"),
    ("העלאות תעריפים", "העלאות ריבית"),
    ("הורדת תעריפים", "הורדת ריבית"),
    ("הדפסת מדד", "קריאת מדד"),
    ("שנת פיתול", "שנת מפנה"),
    # v37b: the AI's Hebrew in the daily summary of 5.11.2026
    ("הבחירות האמצעיות", "בחירות אמצע הקדנציה"),
    ("המכירה החריגה", "גל המכירות החריג"),
    ("מכירה חריגה", "גל מכירות חריג"),
    ("מטחי בטחון", "נכסי מפלט"),
    ("מטחי ביטחון", "נכסי מפלט"),
    ("איבוד מניות", "ירידת מניות"),
    ("מסלול רחב של מניות", "גל מכירות רחב במניות"),      # "rout" read as "route"
    ("מסלול מניות", "גל המכירות במניות"),
    ("מטבעות קריפטוגרפיים", "קריפטו"),
    ("תשואת האוצר", "תשואת האג״ח הממשלתי"),
    ("תוצאות אמצע הטווח", "תוצאות בחירות אמצע הקדנציה"),
    ("בחירות האמצע", "בחירות אמצע הקדנציה"),
    ("מקלט בטוח", "נכס מפלט"),
    ("מפרס הפרסי", "מפרץ הפרסי"),
    ("במפגש מסחר", "ביום מסחר"),
    ("מפגש מסחר", "יום מסחר"),
    ("הבינה מלאכותית", "הבינה המלאכותית"),
    ("גל מכירות אכזרי", "גל מכירות חד"),
    ("סחיטה קצרה", "לחיצת שורטים"),
    ("לפני הפעמון", "לפני פתיחת המסחר"),
    ("לפני הכדור", "לפני פתיחת המסחר"),
    ("מתקתקות גבוה יותר", "עולות מעט"),
    ("מתקתקות נמוך יותר", "יורדות מעט"),
    ("מתקתק גבוה יותר", "עולה מעט"),
    ("מתקתק נמוך יותר", "יורד מעט"),
    ("האנתרופיה", "אנתרופיק"),
    ("ציפיות שיעור", "ציפיות הריבית"),
    ("שוק השור", "שוק שורי"),
    ("שוק השוורים", "שוק שורי"),
    ("שוק שוורי", "שוק שורי"),
    ("שוק הדובים", "שוק דובי"),
    ("שיא השיא", "שיא היסטורי"),
    ("סטגלציה", "סטגפלציה"),
    ("משכורות שאינן חקלאיות", "משרות מחוץ לחקלאות"),
    ("משכורות חוץ-חקלאיות", "משרות מחוץ לחקלאות"),
    ("השכר הלא-חקלאי", "המשרות מחוץ לחקלאות"),
    ("אנתרופית", "של אנתרופיק"),                 # "Anthropic spending" -> "הוצאה אנתרופית"
    ("ניפוח המזון", "אינפלציית המזון"),
    ("חוזים עתידיים אנוסים", "חוזים עתידיים על קריפטו"),
    # v73 (23.11.2026): "Trump warns of new strikes", "weekly rout"
    ("מזהיר מפני שביתות חדשות", "מזהיר מפני תקיפות חדשות"), ("מסלול שבועי של", "ירידה שבועית של"),
    # v68 (key events 22.11.2026): "Oil Posts Third Weekly Drop" -> "הנפט מפרסם ירידה"
    ("מפרסם ירידה", "רושם ירידה"), ("מפרסמת ירידה", "רושמת ירידה"), ("מפרסמים ירידה", "רושמים ירידה"),
    # v74 (24.11.2026): rows saved before the EN_REWRITES of the same day
    ("'שפוי' לעלות", "'נבון' להעלות את הריבית"), ("צ'יפ, מניות בינה מלאכותית", "מניות שבבים ובינה מלאכותית"),
    ("שוט שוק AI מעמיד", "תנודתיות בשוק ה-AI מעמידה"), ("עליית Nvidia-Led", "עלייה בהובלת Nvidia"),
    ("מטה את התחזיות", "נמוך מהתחזיות"),
    # v76 (24.11.2026): rows saved before the EN_REWRITES of the same day
    ("למניות צ'יפ", "למניות השבבים"), ("מניות צ'יפ", "מניות השבבים"), ("Wall St נפתח נמוך", "וול סטריט נפתחת נמוך"), ("על Wall St", "בוול סטריט"),
    ("כאשר עצירת AI מבוססת Nvidia דועכת", "כאשר הזינוק במניות ה-AI בהובלת Nvidia דועך"), ("לאחר עצרת מונעת על ידי Nvidia", "לאחר עלייה בהובלת Nvidia"),
    ("גרמניה מבטלת סנקציות של האיחוד האירופי", "גרמניה שוקלת סנקציות של האיחוד האירופי"), ("בקניית מציאה", "בקנייה במחירים נמוכים"),
    ("לחץ שולי על", "לחץ על שולי הרווח על"), ("קפצה על הכאה ברבעון השלישי", "קפצה בעקבות תוצאות טובות מהצפוי ברבעון השלישי"),
    ("נתוני האינפלציה בארה\"ב ממתינים", "השווקים ממתינים לנתוני האינפלציה בארה\"ב"), ("מניית של אנתרופיק", "מניית אנתרופיק"),
    ("סחר האג\"ח של שוק האג\"ח להעלאות הפד שנובע מנסיגה במניות", "הפוזיציה הפופולרית בשוק האג\"ח על העלאות ריבית נפגעה מירידת המניות"),
    ("תפקידו הפוטנציאלי של קווין וורש בפד", "מדיניות הפד בראשות קווין וורש"),
    # v77 (25.11.2026): rows saved before the EN_REWRITES of the same day
    ("כשעצרת הסיוע של הואנג דועכת", "כשהריבאונד של הואנג דועך"), ("עצרת הסיוע כבר דועכת", "הריבאונד כבר דועך"), ("מתחת להתנחלות", "מתחת למחיר הסגירה"),
    ("על בנייה קטנה של גולמי", "על עלייה קטנה במלאי הנפט הגולמי"), ("הסיכון לעליית עליות הפד בכלכלה", "הסיכון שהעלאות ריבית של הפד יעכבו את הכלכלה"),
    ("מניות מתיישבות לרוב בירידה במניות", "המניות נסגרו ברובן בירידה עם צניחה במניות"), ("על רווחים ורווחים ברבעון", "על הכנסות ורווחים מעל ההערכות ברבעון"),
    ("פעימות הרווח", "רווחים טובים מהצפוי"), ("מחממים על אנתרופיק", "מאבדים אמון באנתרופיק"), ("שחרור האינפלציה", "פרסום נתוני האינפלציה"),
    ("הקפצת Nvidia דועכת", "הריבאונד של Nvidia דועך"), ("היפוך עצירת בינה מלאכותית", "היפוך במניות הבינה המלאכותית"), ("לטיול נוסף", "להעלאת ריבית נוספת"),
    ("יום עבודה (WDAY) בראש הרווחים וההערכות ההכנסות", "Workday (WDAY) מדווחת על רווחים והכנסות מעל ההערכות"), ("הנחיה חזקה", "תחזית חזקה"),
    ("מלאי מניות של Dell לאחר רווחי התפוצצות", "מניית Dell צוללת לאחר רווחים חזקים מאוד"), ("גווני אפור", "Grayscale"), ("לאחר סילוף גדול", "לאחר גל מכירות גדול"),
    ("מפרסם הפסד", "רושם הפסד"), ("מפרסמת הפסד", "רושמת הפסד"), ("מפרסם עלייה", "רושם עלייה"), ("מפרסמת עלייה", "רושמת עלייה"),
    # v77b: rows saved before the EN_REWRITES of the same day
    ("כאשר ריצוד AI נמשך", "כשהחשש סביב ה-AI נמשך"), ("הפסקת הכנסות", "הכנסות נמוכות מהצפוי"), ("וציפיות תעריפים", "וציפיות לריבית"),
    ("מאריכות את ההפסדים", "ממשיכות לרדת"), ("מתנגדים לסיבות לאינפלציה", "חלוקים לגבי הסיבות לאינפלציה"), ("צליפת שוט של Nvidia בוחנת", "התנודתיות של Nvidia בוחנת"),
    ("הדולר לוקח אוויר", "הדולר עוצר"), ("זינק במדד המחירים לצרכן", "זינק אחרי נתוני מדד המחירים לצרכן"), ("בארה\"ב מתנשאים", "בארה\"ב מתקרבים"),
    ("פוטנציאליים מתנשאים", "פוטנציאליים מתקרבים"), ("מכירת מניות אמורה להימשך כ-A.I. שחקנים נופלים", "הירידה במניות אמורה להימשך כשחברות AI נופלות"),
    ("סחר בינה מלאכותית נאבקת להחזיר את דרכה", "מניות הבינה המלאכותית נאבקות להתאושש"), ("מכירת הטכנולוגיה מתגברת", "הירידה במניות הטכנולוגיה מתגברת"),
    ("מלחץ מחשבים על שולי הרווח והרווח הזהיר", "מלחץ על שולי הרווח של מחשבים ומתחזית רווח זהירה"), ("דוח אינפלציה כדי לספק את", "דוח האינפלציה יספק את"),
    ("שכן רוסיה מבטיחה תגמול", "בעוד רוסיה מבטיחה תגובה"),
    # v77b (26.11.2026): rows saved before the EN_REWRITES above; "major U.S. trading partners" has no English that Google gets right
    ("סחורות משותפות סחר גדולות בארה\"ב", "סחורות של שותפות הסחר הגדולות של ארה\"ב"), ("סחורות משותפות הסחר", "סחורות של שותפות הסחר"),
    ("אבל אולי לא נעשה", "אבל אולי לא נגמר"), ("איומי רוסיה בהתחלה מאז", "איומי רוסיה, לראשונה מאז"),
    ("מתנערים מהמחסום האחרון בארה\"ב", "מתעלמים מסבב המכסים האחרון של ארה\"ב"), ("חבר בבנק הוקיש ביפן", "חבר ניצי בבנק יפן"),
    ("בנק אוף יפן", "בנק יפן"), ("ככל שהטכנולוגיה מתקדמת, מניות הבנקים", "כשמניות הטכנולוגיה ומניות הבנקים"),
    ("צניחה של המניה ולהגיב", "הצניחה בבורסה ולהגיב"),
    # v77c: rows saved before the EN_REWRITES above
    ("סנטימנט הפד ההוקיש", "הציפיות לפד נצי"), ("ההוקיש", "הניצי"), ("הוקיש", "ניצי"),
    ("כשהטכנולוגיה מחזיקה במשפט", "כשמניות הטכנולוגיה מובילות"), ("נגד הביטאון הפדרלי", "נגד הבירוקרטיה לפדרל ריזרב"),
    ("נסחף נמוך יותר", "יורד מעט"), ("נסחפים נמוך יותר", "יורדים מעט"), ("נסחף גבוה יותר", "עולה מעט"),
    ("החששות מהעלאת הריבית של הפד מתנשאים", "החששות מהעלאת הריבית של הפד גוברים"),
    # v78b: rows saved before the EN_REWRITES above (the first one only in its own sentence: "ספגה הפסדים" is right elsewhere)
    ("בהובלת מניות בינה מלאכותית לאחר שוול סטריט ספגה הפסדים חדים", "בהובלת מניות בינה מלאכותית לאחר שוול סטריט צמצמה הפסדים חדים"),
    ("Sterling Steady כמו הפסקות דולרים", "הפאונד הבריטי יציב כשהדולר עוצר"), ("השקעה חיה בסשן אירופאי", "סיכום המסחר באירופה"),
]

# v38c: company names that are ordinary English words, which Google translates ("Ball" -> "כדור", "Visa slips" -> "תלושי ויזה",
# "First Solar" -> "פירסט סולאר", "Southern Company" -> "חברת דרום"). With the legal suffix after them ("Ball Corp") Google keeps
# them in English letters (tested 9.11.2026); fix_he takes the suffix off again. Not placeholders: a real English sentence.
# Add a name here when it comes out translated on the site. Multi-word names count anywhere; a one-word name only where it reads
# as the company (_NAME_CONTEXT), so "the Fed's inflation target" stays a word.
COMPANY_WORD_NAMES = [
    "Target", "Visa", "Block", "Ball", "Progressive", "Gap", "Carnival", "Caterpillar", "Carrier", "Waters", "Travelers",
    "Snowflake", "Shell", "Discover", "Mosaic", "Coherent", "Unity", "Snap", "Lucid", "Diamondback", "Corning", "Paramount",
    "Southern Company", "Public Storage", "First Solar", "General Mills", "Monster Beverage", "Best Buy", "Dollar General",
    "Dollar Tree", "Home Depot", "Applied Materials", "Texas Instruments", "Analog Devices", "Global Payments", "State Street",
    "Northern Trust", "Interactive Brokers", "Iron Mountain", "Genuine Parts", "Tractor Supply", "General Dynamics", "Air Products",
    "International Paper", "Sealed Air", "Electronic Arts", "Live Nation", "Fair Isaac", "Realty Income", "Extra Space Storage",
    "Digital Realty", "American Tower", "Crown Castle", "Boston Scientific", "Intuitive Surgical", "Marathon Petroleum",
    "Steel Dynamics", "Vulcan Materials", "Republic Services", "Waste Management", "Quest Diagnostics", "Cardinal Health",
    "Southwest Airlines", "United Airlines", "American Airlines", "Delta Air Lines", "Principal Financial", "Constellation Brands",
    "Workday", "Tapestry", "Occidental", "Grayscale", "Toll Brothers", "Dick's Sporting Goods",      # v77
    "Constellation Energy", "Dominion Energy", "Duke Energy", "Charter Communications", "Church & Dwight", "Brown & Brown",
]
_SUFFIX = "Inc"
_NAME_CONTEXT_AFTER = (r"(?i:'s|’s|\s*\(|,|\s+(?:shares?|stock|stocks|earnings|results|revenue|sales|profits?|Q[1-4]|CEO|"
                       r"forecasts?|outlook|guidance|beats?|miss(?:es)?|tops?|cuts?|raises?|lifts?|slips?|jumps?|falls?|rises?|"
                       r"sinks?|soars?|surges?|drops?|plunges?|gains?|rall(?:y|ies)|tumbles?|reports?|posts?|came|says|to|will|is|and|or)\b)")
_NAME_BAD_BEFORE = re.compile(r"(?:inflation|price|rate|rates|policy|profit|sales|earnings|its|the|a|an|aircraft|oil|fed|fed's|"
                              r"credit|debit|travel|student|work|year)\s+$", re.I)
_LEGAL = r"\s+(?:Inc|Corp|Corporation|Co|Company|Ltd|plc|Group|Holdings)\b"


def _name_re(name):
    word = r"(?<![\w&'’-])" + re.escape(name) + r"(?![\w&-])(?!" + _LEGAL + ")"
    return re.compile(word + ("" if " " in name else "(?=" + _NAME_CONTEXT_AFTER + "|\\s*$)"))


_NAME_RES = [(n, _name_re(n)) for n in COMPANY_WORD_NAMES]
_NAME_BACK = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(COMPANY_WORD_NAMES, key=len, reverse=True)) + r") " + _SUFFIX + r"\b\.?")


def keep_company_names(text: str) -> str:
    """The names above, with " Inc" after them where they are the company, so the translator keeps them in English."""
    for name, rx in _NAME_RES:
        text = rx.sub(lambda m: m.group(0) if _NAME_BAD_BEFORE.search(text[:m.start()]) else f"{m.group(0)} {_SUFFIX}", text)
    return text


# Google sometimes adds vowel points to single words ("לִחיוֹת"). Points and accents only — not the maqaf (U+05BE) or
# the Hebrew punctuation marks U+05C0 / U+05C3 / U+05C6.
_NIQQUD = re.compile(r"[֑-ׇֽֿׁׂׅׄ]")


# v44: "טראמפ אמר שחידוש המלחמה באיראן הוא "לא סביר ביותר". Oil Prices Fell Below a Key Threshold." — a translator
# sometimes hands back the second sentence untouched. A run of 4+ English words with ordinary words among them (not a
# company name) means part of the text was not translated.
_LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z'’-]*(?:\s+[A-Za-z][A-Za-z'’-]*){3,}")
_PLAIN_EN = {"the", "a", "an", "of", "to", "in", "on", "at", "as", "is", "are", "was", "were", "for", "and", "or", "but", "with",
             "by", "from", "after", "before", "below", "above", "over", "under", "into", "amid", "its", "their", "that", "this",
             "will", "says", "said", "fell", "rose", "falls", "rises", "than", "more", "not", "has", "have", "had", "be",
             # v51: a headline in Title Case has no small words: "רווחי Nvidia ו-Anthropic IPO Lead Market Week" (15.11.2026)
             "lead", "leads", "week", "market", "markets", "stocks", "shares", "ahead", "test", "tests", "faces", "face",
             # v77: "Dow Jones Futures: Nvidia, Anthropic Lead AI Retreat אבל מגזרים רבים חזקים"; "Puts, Eyes Fresh Nvidia Short קרוב"
             "retreat", "rally", "futures", "fresh", "eyes", "short", "many", "sectors"}


def half_translated(hebrew: str) -> bool:
    """True when a Hebrew translation still carries a stretch of plain English (see above). A text with no Hebrew at
    all is not "half" — the caller already treats that as a failed translation."""
    if not re.search(r"[א-ת]", hebrew or ""):
        return False
    for run in _LATIN_RUN.findall(hebrew):
        if sum(w.lower() in _PLAIN_EN for w in run.split()) >= 2:
            return True
    return False


def cut_short(english: str, hebrew: str) -> bool:
    """v77c (26.11.2026): Google's dict-chrome-ex endpoint gave "ברוקס מזהיר" for "Hawkish Fed risks deeper market selloff
    as real yields climb, Brooks warns" — only the last two words. In 50 headlines of the home page a correct Hebrew was at
    least 0.64 of the English length; under 0.45 means part of the sentence was dropped."""
    en, he = (english or "").strip(), (hebrew or "").strip()
    return len(en) >= 30 and bool(re.search(r"[א-ת]", he)) and len(he) < 0.45 * len(en)


def prep_en(text: str) -> str:
    """English text as it should be sent to the translator."""
    for pat, rep in EN_REWRITES:
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)
    return keep_company_names(text)


def fix_he(text: str) -> str:
    """Translator output with known bad renderings corrected."""
    # v28: MyMemory answers with HTML codes — the site showed "בארה&quot;ב" and "אנרג&#39;י" literally
    text = _NIQQUD.sub("", html.unescape(text or ""))
    text = text.replace("​", "").replace("﻿", "")      # v67: "האספקה ​​בשפע" came with two zero-width spaces
    for bad, good in HE_FIXES:
        text = text.replace(bad, good)
    # v67: "Oil" opening a headline is read as cooking oil ("שמן מפרסם ירידה"); "Anthropic" as "אנתרופי"
    text = re.sub(r"^(\s*)שמן (?!(?:זית|דקלים|קנולה|בישול|סויה)\b)", r"\1הנפט ", text)
    text = text.replace("מחירי השמן", "מחירי הנפט")
    text = re.sub(r"אנתרופי(?=[\s,.:;!?״\"']|$)", "אנתרופיק", text)
    # v68: Reuters' labels, in rows saved before EN_REWRITES dropped them ("תצוגה מיידית: נאסד"ק צוללת")
    text = re.sub(r"^\s*(?:תצוגה מיידית|תיבת עובדות|מסביר)\s*:\s*", "", text)
    text = _NAME_BACK.sub(r"\1", text)          # v38c: "Ball Inc" -> "Ball" (keep_company_names added the suffix)
    return re.sub(r'(?<=\S) בע["״]מ(?=[\s,.;:]|$)', "", text)    # "בלוק בע"מ": the same suffix, translated
