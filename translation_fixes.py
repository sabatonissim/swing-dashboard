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
]

HE_FIXES = [
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

# Google sometimes adds vowel points to single words ("לִחיוֹת"). Points and accents only — not the maqaf (U+05BE) or
# the Hebrew punctuation marks U+05C0 / U+05C3 / U+05C6.
_NIQQUD = re.compile(r"[֑-ׇֽֿׁׂׅׄ]")


def prep_en(text: str) -> str:
    """English text as it should be sent to the translator."""
    for pat, rep in EN_REWRITES:
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)
    return text


def fix_he(text: str) -> str:
    """Translator output with known bad renderings corrected."""
    text = _NIQQUD.sub("", text or "")
    for bad, good in HE_FIXES:
        text = text.replace(bad, good)
    return text
