"""
Google News link -> the real article URL (v18d). Shared by pipeline_b_news_aggregator.py (GitHub Actions) and
api_server.py (Render). Standard library only, so neither side needs a new package.

Why: ~85% of our headlines come from Google News search feeds, whose links (news.google.com/rss/articles/CBMi...)
only open through JavaScript. The article preview in the drawer could not follow them, so most news boxes showed
nothing beyond the headline even though the real publisher was known. Google's own page for the link carries a
signature + timestamp; posting them back to Google's "batchexecute" endpoint returns the publisher's URL (the same
call the Google News web app makes). Checked 27.10.2026 from a home connection: a Daily Sabah link decoded to
dailysabah.com/business/economy/... . Whether Google answers from GitHub/Render addresses is checked in their logs.

Never raises: returns None whenever anything does not look as expected (the caller keeps the Google link).
"""

import json
import re
import urllib.parse
import urllib.request
from typing import Optional

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def is_google_news(url: str) -> bool:
    try:
        return (urllib.parse.urlparse(url or "").hostname or "").lower().endswith("news.google.com")
    except Exception:  # noqa: BLE001
        return False


def decode_google_news_url(url: str, timeout: float = 8.0) -> Optional[str]:
    try:
        m = re.search(r"/articles/([A-Za-z0-9_-]+)", urllib.parse.urlparse(url).path)
        if not m:
            return None
        art_id = m.group(1)
        # v18e: the signature sits at the very END of a large page (/articles/ ~1.07MB, /rss/articles/ ~0.6MB, measured
        # 27.10.2026) — v18d read only the first 800KB, so every decode failed ("decoded 0, 5 failed" in the log).
        req = urllib.request.Request(f"https://news.google.com/rss/articles/{art_id}", headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            page = resp.read(3_000_000).decode("utf-8", errors="replace")
        sig = re.search(r'data-n-a-sg="([^"]+)"', page)
        ts = re.search(r'data-n-a-ts="(\d+)"', page)
        if not (sig and ts):
            return None
        inner = json.dumps(
            ["garturlreq",
             [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
              "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
             art_id, int(ts.group(1)), sig.group(1)],
            separators=(",", ":"))
        body = urllib.parse.urlencode({"f.req": json.dumps([[["Fbv4je", inner, None, "generic"]]])}).encode()
        req = urllib.request.Request(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute", data=body,
            headers={"User-Agent": _UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read(200_000).decode("utf-8", errors="replace")
        # answer = ")]}'" line, then a JSON list; the Fbv4je entry holds a JSON string ["garturlres", "<url>", ...]
        try:
            data = json.loads(text.split("\n", 1)[1] if text.startswith(")]}'") else text)
        except ValueError:
            m = re.search(r'garturlres\\",\\"(https?://[^"\\]+)\\"', text)
            real = m.group(1) if m else None
            return real if real and not is_google_news(real) else None
        for row in data:
            if isinstance(row, list) and len(row) > 2 and row[:2] == ["wrb.fr", "Fbv4je"] and isinstance(row[2], str):
                res = json.loads(row[2])
                real = res[1] if isinstance(res, list) and len(res) > 1 and res[0] == "garturlres" else None
                if isinstance(real, str) and real.startswith(("https://", "http://")) and not is_google_news(real) \
                        and len(real) <= 2000:
                    return real
        return None
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------
# v28 (live feed 3.11.2026): headline clean-up rules, shared by the news pipeline (before saving) and api_server
# (rows already saved). Junk seen on the site: "War Laura Thalassa OOP", "Q. Is this a credit card?", "Watch - Amer: ...",
# "+113%, +43%, See Which Stocks Are In ... Our AI Picks", India food inflation / Russian utility bills / Ethiopia —
# and outlet taglines glued to titles and names ("... - Euronext Markets: Real-time Stock Market Data | live").
# ------------------------------------------------------------------
PROMO_RE = re.compile(r"\b(stocks? to (buy|watch|own|bet on)|to bet on|should you (buy|sell|invest|bet)|buy now|"
                      r"is it (time to buy|a buy)|smart buy|buy the dip|"
                      r"stocks? (for|to) (retirees|beginners)|our (ai |top )?picks|stock picks|"
                      r"(to )?buy (ahead of|before) (the )?(print|earnings|results|report)|"   # v28d: "To Buy Ahead of the Print or Wait"
                      r"buy or (sell|wait|hold)|"
                      # v47 (live feed 11.11.2026): "+102%, +40%: ... these AI-picked winners", "We told you first: these
                      # AI-picked small-cap stocks are up +49%", "New Strong Buy Stocks for November 11th",
                      # "Top 3 Real Estate Stocks That Could Blast Off In Q4"
                      r"ai-picked|we told you first|strong buy stocks?|blast off|"
                      # v44 (live feed 10.11.2026): "As Crude Oil Prices Retreat, Sell Soybean Oil Now" \u2014 advice, not news
                      r"(buy|sell|short)\s+(?:[\w&'-]+\s+){1,4}now(?!\s+that)|buying opportunity|"
                      # v76 (24.11.2026): "This Wall Street Pro Maintains That 440% Returns Are Coming", "Can Dollar Tree Make You Some Cash?"
                      # v76b: "Insiders Are Loading Up on These 5 Stocks", "These 4 Utility Stocks Pay Dividends", "Salesforce Stock Looks Cheap"
                      r"these \d+ (?:[\w&'-]+ ){0,2}stocks|stock looks (?:cheap|expensive)|how to watch|"
                      r"wall street pro|make you (?:some |a lot of )?(?:cash|money|rich)|"
                      # v77 (25.11.2026): "If You Invest $5,000 Into These 3 Stocks, You Could Have $10,000 by 2030", "Made Our List of
                      # Dirt-Cheap Stocks", "Josh Brown's best stocks in the market", "Cramer ... names his two favorites",
                      # "These Most Accurate Analysts Revise Forecasts", "Here's How You Can Still Win", "GDS Holdings And 2 Other AI ... Stocks"
                      r"if you (?:had )?invest(?:ed)? \$|dirt[- ]cheap stocks?|(?:made|on) our list|best stocks in the market|most accurate analysts|"
                      r"names? (?:his|her) (?:\w+ )?favorites|\d favorites|how you can still win|and \d+ other (?:[\w&'-]+ ){0,4}stocks|"
                      r"generate a return from|are they right|barchart data|"
                      # v77b (25.11.2026): "Five materials stocks with attractive valuations following Q3 earnings", "Ten undervalued
                      # energy stocks after Q3 earnings", "Why Space Stocks Could Be The Next Frontier For AI Investors"
                      r"(?<!long )undervalued\b[^.:;]*\bstocks?|stocks? with attractive valuations?|next frontier for\b[^.]*\binvestors|"
                      # v77c (26.11.2026): "President Trump Just Hit Stock Market Investors With Bad News at a Bad Time" (written to be clicked)
                      r"\bjust (?:hit|handed|gave|sent|delivered)\b[^.]*\binvestors\b|bad news at a bad time|"
                      r"screaming buys?)\b"      # "Tech Sell-Off: Two AI Stocks That Are Screaming Buys Right Now" (11.11.2026)
                      r"|^\s*(?:top\s+)?\d+\s+[\w\s&'-]{0,40}\bstocks?\b"
                      r"|^\s*(?:top\s+)?(?:three|four|five|six|seven|eight|nine|ten|twelve|fifteen|twenty)\s+(?:[\w&'-]+\s+){0,2}stocks?\b"      # v77b
                      # v51 (live feed 15.11.2026): "Bank of America names 5 bullish stocks amid market fears"
                      r"|\b(?:names?|named|picks?|lists?|reveals?|highlights?)\s+\d+\s+(?:[\w&'-]+\s+){0,3}stocks?\b"
                      # v67 (home page 21.11.2026, MarketBeat): "Best Luxury Goods Stocks To Research - November 21st",
                      # "Promising Fintech Stocks Worth Watching - November 21st", "Value Stocks To Consider - November 21st"
                      r"|\bstocks?\s+(?:worth\s+watching|to\s+(?:research|consider|keep\s+an\s+eye\s+on|add|follow))\b", re.I)
# v51 (live feed 15.11.2026): articles about one imaginary reader's money, written to be clicked. They sat on the home page
# between the Fed and Nvidia: "A 64-year-old farmer plans to lease land ... and start Social Security", "... Here's what it
# means for a 72-year-old widow with $150,000 of Disney", "Investors: if a market correction arrives, this move will make
# or break your portfolio", "Bitcoin vs. stocks: ... The results may surprise you", "Laird Superfood: 3 key options for
# long-term investors", "... Here's what that means and doesn't mean for you."
PERSONAL_RE = re.compile(r"\b\d{2}-year-old\b|\bwidow(?:er|s)?\b|\bsocial security\b|\bnest egg\b|\b401\(?k\)?|\broth ira\b|"
                         r"\byour (?:portfolio|retirement|savings|money|wallet)\b|\bmay surprise you\b|\bbest investment\b|"
                         r"\bfor long-term investors\b|\bmeans?(?: and [\w' ]{3,30})? for you\b|^\s*investors:\s", re.I)
# v51: outlets whose articles are advice to the reader ("3 stocks to ...", "here is what to do"), not news. The site shows
# facts and no recommendations, so they are left out of every feed. The stock's own news page is not touched.
ADVICE_OUTLETS_RE = re.compile(r"motley fool|24/7 wall st|zacks|investorplace|insider monkey|stocks down under|"
                               r"investorideas|chartmill", re.I)     # v77b (26.11.2026): "Is the Fed making a huge mistake ...?", "A Growth-at-a-Reasonable-Price Stock"


def is_advice_outlet(name: str) -> bool:
    return bool(ADVICE_OUTLETS_RE.search(name or ""))
# v44: a newsletter's table of contents as a headline ("Hot inflation report puts Fed on track ...; The best banks to
# work for in 2026\u2014and more in Bankers' Hours")
# v47: a quote page and round-ups as headlines ("Crude Oil Dec 26 (CL=F) Stock Price, News, Quote & History", "AM Markets
# Need to Know: Fed faces inflation test, Anthropic IPO demand surges, and more", "Dow Jones Top Energy Headlines at 4 AM ET: ...")
_FORMAT_JUNK_RE = re.compile(r"^\s*(watch|video|listen|podcast)\s*[:\-\u2013\u2014]|^\s*q\s*[.:]\s|\band more (in|from)\b|"
                             r"\bstock price, news, quote\b|,? and more\s*$|\btop [\w ]{0,20}headlines at \d|"
                             # v51 (live feed 15.11.2026): a photo caption and a readers' poll as headlines ("FILE PHOTO: The
                             # Federal Reserve Board building is seen in Washington", "SA Asks: What are the most attractive ...")
                             r"^\s*file photo\b|^\s*(?:photos?|pictures?)\s*:|^\s*sa asks\b|"
                             # v74 (24.11.2026): "Earnings Calendar for:" was the headline of the earnings news of the day
                             r"^\s*earnings calendar\b|"
                             # v77 (25.11.2026): "Watch Retailers Post Strong Earnings Ahead of Holidays" (a video), "META Stock Quote Price and
                             # Forecast", "Caleb Martin posts 13 points, seven 'stocks'", "UNL Husker Pantry stocks up", "Earnings Check-In: ..."
                             r"^\s*watch\s+(?!list\b)|\bstock quote price\b|\bposts \d+ points,|\bpantry stocks\b|\bearnings check-in\b|"
                             r"\bstays connected to you\b|^\s*tax interest rates\b|"
                             # v77b: "Macy's Thanksgiving Day Parade Inflation Celebration" (balloons, shown as inflation news), "Forbes
                             # Daily: ...", "Anthropic, Nvidia ... and More Stocks That Explain Today's Market", "... | Market Talk" (a video)
                             r"\bthanksgiving day parade\b|\b(?:balloons?|parade)\b.*\binflation\b|\binflation celebration\b|^\s*forbes daily\b|"
                             r"\band more stocks that\b|\|\s*market talk\s*$|"
                             # v77b (26.11.2026, a holiday): "Opinion | The Next Fed Leader Should Be a Bot", "Sen. Tim Kaine, D-Va., shares
                             # why he opposes ...", "Economist shares how to save on the spread", "'War Is Hell:' A Foreign Soldier Writes Home"
                             r"^\s*opinion\s*[|:\-–—]|\bshares (?:why|how|what|his|her|their)\b|\bhow to save\b|\bwrites home\b|"
                             # a speculative question as the whole headline ("Could EU delisting of Chinese firm pave way for wider
                             # sanctions relief?"); "Will the Fed hike in December?" stays: it asks about a decision
                             # ("Can Iran Withstand Trump's Naval Blockade as ... Soar?" is about the war and oil, and stays)
                             # (and a question about the Fed stays: "Is the Fed lagging behind inflation?", kept on 23.11)
                             r"^(?!.*\b(?:trump|iran|war|blockade|opec|oil|fed|fomc|powell|warsh)\b)\s*(?:could|can|should|is|are|has|have|might)\b[^?]{10,}\?\s*$|"
                             # v76: "... Stays Elevated | Options Market Statistics"
                             r"\|\s*options market statistics\b", re.I)
# v37: machine-made pages about one company's report: 1,029 of the 1,564 headlines of 5.11.2026 were tagged as earnings, most
# of them these. The stock's own page finds its company's news separately (_feed_clean with promo_only).
EARNINGS_CHATTER_RE = re.compile(r":\s*(fiscal\s+)?q[1-4]\s+earnings snapshot\s*$|\bearnings call (highlights|transcript|is live)\b|"
                                 r"\bsaid on its q[1-4] earnings call\b|\breleases? (first|second|third|fourth)[- ]quarter earnings\b|"
                                 # v44: machine-made previews of one company's report ("Home Depot (HD) Reports Next Week: Wall
                                 # Street Expects Earnings Growth", "Earnings Preview: Spire (SR) Q4 Earnings Expected to Decline")
                                 # v70: "Dollar Tree earnings: what to look for from DLTR" (23.11.2026)
                                 r"\bearnings?:\s*what to (look for|expect|watch)\b|\bearnings preview\b|\breports next week\b|\bearnings expected to (decline|grow|rise|fall)\b|"
                                 r"\bexpected to beat earnings estimates\b|\banalysts estimate\b.*\bto report\b|"
                                 # v47: "Lowe's Companies, Inc. to Host Third Quarter 2026 Earnings Conference Call on Nov. 18",
                                 # "How Investors May Respond To Ross Stores Stock Ahead Of Earnings Growth Expectations",
                                 # "How Earnings Beat Will Impact Cloudflare Stock Investors", "(BOBS) Could Be 7% Undervalued As ..."
                                 r"\bto host\b.*\bearnings (conference )?call\b|\bhow investors may respond\b|"
                                 r"\bhow earnings beat will impact\b|\bcould be \d+% (under|over)valued\b|"
                                 # v74: "Primary Health Properties (LSE:PHP) Stock Looks Reasonably Priced On Current Earnings"
                                 r"\b(?:stock|shares?)\s+(?:looks?|seems?|appears?|is|are)\s+(?:reasonably|fairly|fully|attractively)\s+(?:priced|valued)\b|"
                                 # v76: seven of the twelve headlines of 24.11.2026 18:00 were Zacks templates on Yahoo: "Titan Machinery
                                 # (TITN) Q3 Earnings: Taking a Look at Key Metrics Versus Estimates", "... What Key Metrics Have to Say",
                                 # "Are Utilities Stocks Lagging WEC Energy Group (WEC) This Year?", "Globe Life (GL) Stock Could Be
                                 # Undervalued On Its Earnings Strength", "Agilent Q4 Earnings Call Maps Out Growth From Lab Upgrades"
                                 r"\bkey metrics\b.*\b(?:estimates|have to say|tell|compare)\b|"
                                 r"^\s*(?:is|are|has|have)\b.+\b(?:lagg(?:ing|ed)|outperform(?:ing|ed)|outpac(?:ing|ed))\b.+\bthis year\s*\?|"
                                 r"\b(?:stock|shares?)\s+could be\s+(?:under|over)valued\b|\bearnings call (?:maps out|signals|points to|shows)\b|"
                                 # v77 (25.11.2026): announcements of a date ("AeroVironment, Inc. to Announce ... Earnings and Host Conference
                                 # Call", "THOR Industries to Release 2027 Q1 Earnings on Dec. 8"), call summaries, a local bank's "announces
                                 # third-quarter earnings", and the valuation templates of Simply Wall St on Yahoo (9 of 60 headlines)
                                 r"\bto (?:announce|release)\b.*\b(?:earnings|results)\b.*(?:\bhost\b|\bconference call\b|\bon [a-z]{3,9}\.? \d)|"
                                 r"\bearnings release and conference call\b|\bearnings call (?:summary|recap|details)\b|\brecap of .*\bearnings call\b|"
                                 r"\bannounces? (?:first|second|third|fourth)[- ]quarter (?:earnings|results)\s*$|- earnings summary\s*$|"
                                 r"\breports results for the quarter ended\b|"
                                 r"\bfair value\b|^(?=.*\bstock\b).*\bbull case\b|\bchange the case for\b|\bearnings beat change\b|\b(?:could be|be) a (?:game changer|big deal) for\b|"
                                 r"\bstill (?:a bargain|cheap|trading at a discount)\b|\b(?:is|are)\b.*\ba bargain\b|\btrade at a discount\b|\blooks? (?:over|under)valued\b|"
                                 r"\balready reflect(?:s|ed)? its\b|\bjustify its price\b|\bhow investors are reacting to\b|\bstrong (?:forward )?earnings potential\b|"
                                 # v77b (26.11.2026): "What CMC Markets (CMCX) Said on Its H1 Earnings Call", "A Growth-at-a-Reasonable-Price Stock"
                                 r"\bsaid on its (?:h[12]|fy|full[- ]year|half[- ]year|(?:first|second|third|fourth)[- ]quarter) earnings call\b|\bgrowth-at-a-reasonable-price\b|"
                                 # v77c (26.11.2026): "Sanmina (SANM) Stock Still Looks Like A Bargain On Earnings", "【The First Insurance FY2026 Q3
                                 # Earnings Call】Underwriting Profit Surges 68.9% ... NT$1.369 Billion" (a Taiwanese insurer, machine-written)
                                 r"\bstill looks? like a bargain\b|\blooks? like a bargain\b|^\s*【|\bearnings call\s*】|"
                                 # v77b: "How Upcoming Earnings At Deere (DE) Has Changed Its Investment Story", "Ingles Markets GAAP EPS of
                                 # $0.97, revenue of $1.37B" (a page made by a machine), "New Zealand King Salmon posts record earnings"
                                 r"\bchang(?:e|es|ed|ing) (?:its|the|their) investment (?:story|narrative|thesis|case)\b|"
                                 r"^(?!.*\b(?:beats|misses|in[- ]line)\b).*(?<!non-)\bgaap eps of -?\$[\d.]+.*\brevenue of \$|\bnew zealand\b.*\b(?:posts?|reports?)\b.*\b(?:earnings|profits?)\b|"
                                 r"\bmeans for shareholders\b|\bstock gains attention\b|\bstocks with pricing power\b|"
                                 r"\bquant[- ](?:rated|ratings?|rankings?)\b|\btrades at [\d.]+x earnings\b|"
                                 # TradingView's machine summaries of companies abroad ("NRR: Earnings and rents grew, while H2 disposals ...")
                                 r"^(?!(?:CPI|PPI|PCE|GDP|FED|WSJ|ECB|BOJ|BOE|OPEC|IMF)\b)(?-i:[A-Z]{3,5}): .*\b(?:earnings|revenue|ffo|aum)\b.*\b(?:while|as|but|and)\b|"
                                 # a company with a few million dollars of sales ("Perimeter Medical Imaging AI Q3 GAAP EPS of $0.02, revenue of $0.6M")
                                 r"\beps of -?\$[\d.]+.*\brevenue of \$\d{1,2}(?:\.\d+)?M\b",
                                 re.I)
# v74: a page about one company listed on another country's exchange ("(LSE:PHP)"); one that is listed in the US too stays.
_FOREIGN_LISTING_RE = re.compile(r"\((?:LSE|LON|AIM|ASX|TSX|TSXV|CVE|NSE|BSE|SGX|KLSE|HKG|SEHK|JSE|EPA|ETR|AMS|TSE|TADAWUL|BIT|XTRA|SWX|BME|ENXTPA|OM|WSE)\s?:\s?[A-Z0-9.&-]+\)")
_US_LISTING_RE = re.compile(r"\b(?:NYSE|NASDAQ|AMEX|NYSEARCA)\b", re.I)
# Countries whose domestic news rarely moves US stocks — dropped unless the headline also ties it to the US / world markets
# v37: also the adjectives. "Philippine Inflation Surges to 7.8%" passed and made the overview of a day the S&P 500 fell 4.36%.
LOCAL_RE = re.compile(r"\b(india|indian|russians?|ethiopia|ethiopian|pakistan|pakistani|nigeria|nigerian|kenya|kenyan|bangladesh|"
                      r"sri lanka|philippines?|filipino|indonesia|indonesian|vietnam|vietnamese|thailand|thai|malaysia|malaysian|"
                      r"argentina|argentine|egypt|egyptian|south africa|south african|ghana|zimbabwe|uganda|tanzania|sudan|"
                      r"myanmar|nepal|peru|peruvian|colombia|colombian|"
                      # v44: "Brazil's central bank signals high bar for rate hikes", "Poland's Maslowska sees growing case ..."
                      r"brazil|brazilian|poland|polish|turkey|turkish|hungary|hungarian|czech|romania|romanian|chile|chilean|"
                      # v47: "Moldova to fully join EU sanctions against Russia and Belarus", "Irish annual CPI inflation rises to 4.3%"
                      r"moldova|moldovan|belarus|belarusian|cuba|cuban|serbia|serbian|bulgaria|bulgarian|ireland|irish|"
                      r"libya|libyan|"          # v76: "Could Russia-Ukraine war threaten Libya's fragile reunification process?"
                      r"turkmenistan|azerbaijan|kazakhstan|uzbekistan|lebanon|lebanese|"
                      # v77b (26.11.2026): "Rwanda hikes rates for fourth time this year", "Recruiters Lured Brazilians to Fight ..."
                      r"rwanda|rwandan|zambia|zambian|mozambique|angola|angolan|senegal|ivory coast|cameroon|mongolia|"
                      r"brazilians|colombians|indians|filipinos|indonesians|nigerians|pakistanis|egyptians)\b", re.I)
# v77b: another country's central bank on its own rates ("Canadian inflation could end the year below Bank of Canada forecast",
# "India swaps, treasury bills signal possibility of outsized RBI rate hike") — the Fed, ECB, BOJ, BOE stay.
_FAR_CENTRAL_BANK_RE = re.compile(r"\b(?:bank of canada|boc|reserve bank of (?:india|australia|new zealand)|rbi|rba|rbnz|"
                                  r"bank of korea|banxico|bank of mexico|central bank of brazil|riksbank|norges bank|snb|"
                                  r"swiss national bank|national bank of poland|bank of thailand|bank indonesia)\b", re.I)
_US_RATES_RE = re.compile(r"\bU\.?S\.?\b|(?i:\b(?:fed|fomc|federal reserve|powell|warsh|wall street|dollar|treasur(?:y|ies) yields?|s&p|nasdaq|dow)\b)")
# v44: another country's own stock index is not US market news even when the headline names the US as the reason
# ("Canadian stock market today: TSX futures drift higher in critical US inflation test")
FOREIGN_INDEX_RE = re.compile(r"\b(tsx|canadian (stocks?|shares|markets?|stock market)|sensex|nifty|asx ?200|bovespa|"
                              # v51: "Saudi stocks fell 0.66% as inflation eased to 1.7%" was a sentence of the home page's
                              # summary of 15.11.2026
                              # v70: "Spanish stocks - Factors to watch on November 23" (a market that moves ours still passes)
                              r"(spanish|polish|swiss|nordic|czech|hungarian) (stocks?|shares)\b[^.]*\bfactors to watch|"
                              r"tadawul|(saudi|gulf|dubai|abu dhabi|qatari?|kuwaiti?|egyptian) (stocks?|shares|bourses?|markets?|index)|"
                              # v77b (26.11.2026): "Taiwan Stocks Rebound 956 Points, but Foreign Investors Still Pull NT$49.18 Billion"
                              # ("Taiwan stocks rebound ... as AI selloff eases" is about the chip trade and stays)
                              r"taiex|taiwan (?:stocks?|shares) (?:rebound|rise|fall|close|end|open|gain|drop|slip|climb)s?(?![^.]*\b(?:ai|chips?|tsmc|nvidia|semiconductors?)\b)|"
                              r"kospi|nikkei (?:closes|ends|opens)|hang seng (?:closes|ends|opens))\b",
                              re.I)
# v51: Israel's own price index and home prices are not US market news ("Israel inflation rate drops to 1.1% in October",
# "Contrary to forecasts, CPI falls, inflation drops and home prices fall across Israel"). Israel in a war or oil story
# is not touched.
_ISRAEL_LOCAL_RE = re.compile(r"\bisrael(?:i|'s|’s)?\b[^.]*\b(inflation|cpi|consumer prices?|home prices?|housing|shekel|bank of israel)\b|"
                              r"\b(inflation|cpi|consumer price index|home prices?)\b[^.]*\bisrael\b", re.I)
GLOBAL_RE = re.compile(r"\bU\.?S\.?\b|(?i:\b(america|american|fed|wall street|trump|washington|oil|crude|opec|global|"
                       r"world|dollar|treasur(y|ies)(?! bills)|tariffs?|nasdaq|s&p|chips?)\b)")     # v77b: India's "treasury bills" are not the US's
# v47: "sanctions" used to be in the list above, so the word itself let far-away news in ("Poland passes bill targeting
# crypto assets under sanctions", "Lukashenka calls for dialogue with Poland, criticizes sanctions"). And a sanctions
# headline must say who or what is hit that matters to US markets ("France threatens sanctions against EPR
# organisations over low packaging fees" does not).
_SANCTIONS_RE = re.compile(r"\bsanction(s|ed|ing)?\b", re.I)
_SANCTIONS_SUBJECT_RE = re.compile(r"\bU\.?S\.?\b|(?i:\b(america|american|trump|treasury|washington|white house|congress|"
                                   r"ofac|russia|russian|iran|iranian|china|chinese|venezuela|north korea|oil|crude|"
                                   r"energy|gas|tankers?|banks?|chips?|exports?)\b)")
# v76: "Germany Eyes EU Sanctions to Bar Russia From Hockey Championship"; "Brazil's tax revenue ... buoyed by higher oil prices"
_SPORTS_RE = re.compile(r"\b(hockey|soccer|football|olympics?|olympic games|world cup|championships?)\b", re.I)
_LOCAL_TAX_RE = re.compile(r"\btax (?:revenues?|receipts|collections?)\b", re.I)
_LOCAL_MARKET_RE =re.compile(r"\b(stocks?|markets?|shares)\b", re.I)
_SUBSTANCE_RE = re.compile(r"\d|%|\$|\b(stocks?|shares|markets?|fed|rates?|earnings|oil|gold|bitcoin|economy)\b", re.I)


# v77 (25.11.2026): eleven headlines of one night were on a water company's tariff ("Illinois American Water rate hike", "PNM
# requests new rate hike", "Commissioners vote in favor of utility rate hike"), shown as interest rates ("העלאת ריבית המים").
_UTILITY = r"(?:water|sewer|utility|utilities|electric(?:ity)?|PNM|SDG&E|PG&E|ComEd|toll|commissioners|commerce commission|customers|ratepayers)"
_UTILITY_RATE_RE = re.compile(r"\b" + _UTILITY + r"\b.{0,90}\brate (?:hike|increase)|\brate (?:hike|increase)s?\b.{0,90}\b" + _UTILITY + r"\b|"
                              r"\brequests? (?:a )?new rate hike\b", re.I)
_CENTRAL_BANK_RE = re.compile(r"\b(?:fed|fomc|federal reserve|interest|ecb|boe|boj|boc|rba|central banks?|mortgage|treasur(?:y|ies)|stocks?)\b", re.I)


def is_unwanted_headline(title: str) -> bool:
    """True for promotional listicles, video/Q&A items, too-short meaningless titles and far-away domestic news."""
    t = (title or "").strip()
    if not t or PROMO_RE.search(t) or _FORMAT_JUNK_RE.search(t) or EARNINGS_CHATTER_RE.search(t) or FOREIGN_INDEX_RE.search(t):
        return True
    if PERSONAL_RE.search(t) or (_ISRAEL_LOCAL_RE.search(t) and not GLOBAL_RE.search(t)):
        return True
    if _FOREIGN_LISTING_RE.search(t) and not _US_LISTING_RE.search(t):
        return True
    if len(t.split()) < 5 and not _SUBSTANCE_RE.search(t):
        return True
    if _SANCTIONS_RE.search(t) and (not _SANCTIONS_SUBJECT_RE.search(t) or _SPORTS_RE.search(t)):
        return True
    if _UTILITY_RATE_RE.search(t) and not _CENTRAL_BANK_RE.search(t):
        return True
    if LOCAL_RE.search(t) and _LOCAL_TAX_RE.search(t):
        return True
    if _FAR_CENTRAL_BANK_RE.search(t) and not _US_RATES_RE.search(t):
        return True
    return bool(LOCAL_RE.search(t)) and not GLOBAL_RE.search(t) and not _LOCAL_MARKET_RE.search(t)


# ------------------------------------------------------------------
# v47 (live feed 11.11.2026). The Google News search for "war OR sanctions OR oil prices ..." returns whoever wrote those
# words: a state news agency of Iran, a Belarusian exile channel, local radio stations, a plastics trade paper. 13 of the
# 25 headlines on the home page came from such outlets. From THAT feed only established news outlets are taken (the
# owner approved the list). Matched against the outlet name in lower case.
# ------------------------------------------------------------------
TRUSTED_OUTLETS_RE = re.compile(
    r"reuters|bloomberg|cnbc|\bwsj\b|wall street journal|associated press|\bap news\b|^ap$|financial times|^ft$|"
    r"marketwatch|yahoo|barron|new york times|washington post|axios|fox business|fox news|economist|\bbbc\b|\bcnn\b|"
    r"politico|guardian|nbc news|abc news|cbs news|\bnpr\b|\bpbs\b|forbes|fortune|business insider|investing\.com|"
    r"seeking alpha|^the hill$|semafor|al jazeera|nikkei|south china morning post|france 24|^dw$|deutsche welle|"
    r"euronews|sky news|times of israel|jerusalem post|oilprice|s&p global|rigzone|benzinga|morningstar|usa today|"
    r"los angeles times|telegraph|^time$|^the times$|foreign policy|kitco|investopedia|\bupi\b|\bafp\b|"
    r"federal reserve|^bls$")


def is_trusted_outlet(name: str) -> bool:
    return bool(TRUSTED_OUTLETS_RE.search((name or "").strip().lower()))


# ------------------------------------------------------------------
# v47: the same story from several outlets. The list showed "Oil prices rise ... Strait of Hormuz" four times in a row
# (25 headlines = about 9 stories). Rows are still SAVED one by one — the key-events strip and the daily summary count
# how many outlets reported a story — and are folded into one line when the list is SHOWN.
# Two headlines tell the same story when the words they share are a large enough part of all the words of both
# (weighted Jaccard). A word's weight is its rarity among the headlines compared (idf): "Hormuz" or "CoreWeave" says
# what the story is, "Fed" on a day when every headline names the Fed says little. Words for "went up" / "went down"
# are folded into one each, so "oil rises" and "oil rebounds" meet while "oil rises" and "oil falls" do not.
# Measured on the 100 headlines of 11.11.2026 (tests/fixtures/news_2026_11_11.json): real pairs score 0.25 and up; at
# 0.21-0.24 real and false pairs mix ("Trump, Netanyahu clash ... Iran war" / "Trump says US won Iran war" = 0.24), so
# the line is 0.25 — a missed pair leaves the list as it was, a wrong pair hides a story behind another.
# ------------------------------------------------------------------
_STORY_STOP = frozenset((
    "a an the and or but as at by for from in into of on to with after amid over under than that this these its it is are "
    "was were be been will would could may might can says said say new more most out off how why what when who vs per "
    "about ahead despite while now still near toward towards his her their our your not no yes has have had amp").split())
_STORY_UP = frozenset((
    "rise rises rose rising gain gains gained climb climbs climbed jump jumps jumped rally rallies rallied surge surges "
    "surged advance advances advanced rebound rebounds rebounded soar soars soared pop pops popped higher "
    "recover recovers recovered up").split())
_STORY_DOWN = frozenset((
    "fall falls fell falling drop drops dropped slip slips slipped slide slides slid sink sinks sank tumble tumbles "
    "tumbled plunge plunges plunged retreat retreats retreated lower decline declines declined slump slumps down").split())
STORY_SAME = 0.25              # common weight / weight of all the words of the two headlines
STORY_MIN_SHARED = 3           # ... and at least this many common words
STORY_WINDOW_H = 36
# "Oil prices rise as ...", "Oil rebounds after ...", "... push oil prices up": on the same half day these are one story
# whatever reason each outlet gives — the words after "as" differ too much for the word count above (5 oil headlines
# stayed apart). Only for things with ONE price (not "stocks": European, memory and neocloud stocks are different stories).
STORY_MOVE_WINDOW_H = 12
_MOVE_SUBJECT = {"oil": "oil", "crude": "oil", "brent": "oil", "wti": "oil", "gold": "gold", "silver": "silver",
                 "dollar": "dollar", "bitcoin": "bitcoin", "ethereum": "ethereum"}
_MOVE_RE = re.compile(r"\b(" + "|".join(_MOVE_SUBJECT) + r")\s+(?:(?:prices?|futures)\s+)?([a-z]+)\b", re.I)


def story_move(title: str):
    """("oil", "up") for a headline that says a commodity / currency moved, else None."""
    for m in _MOVE_RE.finditer((title or "").lower()):
        way = "up" if m.group(2) in _STORY_UP else "down" if m.group(2) in _STORY_DOWN else None
        if way:
            return _MOVE_SUBJECT[m.group(1)], way
    return None


def story_words(title: str) -> frozenset:
    out = set()
    for w in re.findall(r"[a-z0-9][a-z0-9&.%$-]*", (title or "").lower().replace("’", "'").replace("'s ", " ")):
        w = w.strip(".-")
        if len(w) < 2 or w in _STORY_STOP:
            continue
        if w in _STORY_UP:
            w = "(up)"
        elif w in _STORY_DOWN:
            w = "(down)"
        elif len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.add(w)
    return frozenset(out)


def story_groups(titles: list, hours: list = None) -> list:
    """titles in the order they are shown (newest first); hours = each one's age in hours (optional). Returns groups of
    indexes, each group in the given order, groups ordered by their first member."""
    import math
    words = [story_words(t) for t in titles]
    n = len(words)
    df: dict = {}
    for ws in words:
        for w in ws:
            df[w] = df.get(w, 0) + 1
    weight = {w: math.log(1 + n / c) for w, c in df.items()}
    total = [sum(weight[w] for w in ws) for ws in words]
    moves = [story_move(t) for t in titles]

    def same(i, j):
        apart = abs(hours[i] - hours[j]) if hours else 0.0
        if apart > STORY_WINDOW_H:
            return 0.0
        if moves[i] and moves[i] == moves[j] and apart <= STORY_MOVE_WINDOW_H:
            return 1.0
        shared = words[i] & words[j]
        if len(shared) < STORY_MIN_SHARED:
            return 0.0
        # opposite directions are different stories ("oil rises" / "oil falls")
        if ("(up)" in words[i] and "(down)" in words[j] and "(up)" not in words[j]) or \
           ("(down)" in words[i] and "(up)" in words[j] and "(up)" not in words[i]):
            return 0.0
        common = sum(weight[w] for w in shared)
        return common / (total[i] + total[j] - common)

    groups: list = []
    for i in range(n):
        best, best_score = None, STORY_SAME
        for g in groups:
            s = max(same(j, i) for j in g)
            if s >= best_score:
                best, best_score = g, s
        if best is None:
            groups.append([i])
        else:
            best.append(i)
    return groups


def clean_publisher(name: str) -> str:
    """Outlet name without its tagline: "The Daily News | Texas' Oldest Newspaper" -> "The Daily News",
    "Euronext Markets: Real-time Stock Market Data | live" -> "Euronext Markets"."""
    name = (name or "").strip()
    short = re.split(r"\s+[|\u2013\u2014]\s+|:\s+", name)[0].strip()
    return short if len(short) >= 2 else name


def strip_outlet_suffix(title: str, publisher: str) -> str:
    """A title without a trailing " - <outlet name>" (Google News adds it; long outlet names escaped the old 40-char rule)."""
    return _BYLINE_RE.sub("", _TICKER_TAIL_RE.sub("", _without_outlet((title or "").strip(), publisher)))


# v76, Benzinga: "Trump Tariffs Return, China Could Be Hit Hardest: WSJ - NVIDIA (NASDAQ:NVDA)"
_TICKER_TAIL_RE = re.compile(r"(?<=\S)\s+-\s+[^-]{2,60}\((?:NASDAQ|NYSE|AMEX|ARCA|OTC)\s?:\s?[A-Z.]{1,6}\)\s*$")


_BYLINE_RE = re.compile(r"\s+By (?:Reuters|Investing\.com)\s*$")       # v77: "Dollar up ahead of inflation data By Reuters" (also "מאת רויטרס")


def _without_outlet(title: str, publisher: str) -> str:
    for name in {(publisher or "").strip(), clean_publisher(publisher)}:
        for sep in (" - ", " | "):                                     # v77: "... AI rally reversal | Daily Sabah"
            if name and title.endswith(sep + name):
                return title[: -len(sep + name)].strip()
    short = clean_publisher(publisher)
    i = title.rfind(" - " + short) if short else -1
    if i > 0 and len(short) >= 4:          # the translated tail of a long tagline: cut from the outlet name on
        return title[:i].strip()
    return title