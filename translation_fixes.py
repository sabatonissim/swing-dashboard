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
    (r"\bfan(?:s|ned|ning)?\s+((?:\w+\s+){0,2}?)(fears|concerns|worries|speculation|hopes)\b",
     r"increase \1\2"),                                                     # "strikes fan Fed inflation fears" -> "האוהדים"
    # v28d: Wall Street slang for an earnings report — "Ahead of the Print" -> "לפני ההדפסה"
    (r"\b(ahead of|before|after|into|for)\s+(?:the|its|their)\s+(?:earnings\s+)?print\b", r"\1 the earnings report"),
    (r"\b(earnings|q[1-4])\s+print\b", r"\1 report"),
    # v38c: "Visa slips" -> "תלושי ויזה"
    (r"\bslips\b", "falls slightly"),
    (r"\bslipped\b", "fell slightly"),
    (r"\bslip(?=\s+(?:as|after|on|in|amid|while)\b)", "fall slightly"),
]

HE_FIXES = [
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


def prep_en(text: str) -> str:
    """English text as it should be sent to the translator."""
    for pat, rep in EN_REWRITES:
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)
    return keep_company_names(text)


def fix_he(text: str) -> str:
    """Translator output with known bad renderings corrected."""
    # v28: MyMemory answers with HTML codes — the site showed "בארה&quot;ב" and "אנרג&#39;י" literally
    text = _NIQQUD.sub("", html.unescape(text or ""))
    for bad, good in HE_FIXES:
        text = text.replace(bad, good)
    text = _NAME_BACK.sub(r"\1", text)          # v38c: "Ball Inc" -> "Ball" (keep_company_names added the suffix)
    return re.sub(r'(?<=\S) בע["״]מ(?=[\s,.;:]|$)', "", text)    # "בלוק בע"מ": the same suffix, translated
