// v35: the pages search engines read, and people arriving from them see first. Each is plain HTML made here, so its text
// is in the page before any script runs (the app itself draws everything in the browser, and raw it had 790 characters of
// text and no links at all: HISTORY §27.3לז). vercel.json sends the addresses here:
//   /stock/KO, /he/stock/KO   the app (index.html) with this stock's title, description and text already in it
//   /he                       the app's home page in Hebrew ("/" itself is the static index.html, in English)
//   /stocks, /learn, /learn/cup-and-handle, /about, /privacy, /terms   own pages, and each under /he too
//   /sitemap.xml              the list of all of these
// A stock's facts come from one answer of the API kept in its memory (/api/seo/stock), and each page is kept by Vercel's
// CDN for hours, so a crawl of every stock does not wake the database. A stock page without a company profile in that
// language has too little text of its own: it works, but it asks search engines not to list it.
const fs = require('fs');
const path = require('path');
const C = require('./_content.js');

const SITE = String(process.env.SITE_URL || 'https://swing-desk-tau.vercel.app').replace(/\/$/, '');
const API = String(process.env.API_URL || 'https://swing-dashboard-3btx.onrender.com').replace(/\/$/, '');
const LANGS = ['en', 'he'];
const TICKER_RE = /^[A-Z][A-Z0-9-]{0,9}$/;           // a stock's page; what company_profiles.py and the sitemap accept
const APP_TICKER_RE = /^[A-Z0-9.\-^=]{1,12}$/;       // what the app can open (^GSPC, BTC-USD): works, not listed
const KEEP = {
  stock: 'public, max-age=0, must-revalidate, s-maxage=21600, stale-while-revalidate=86400',
  fixed: 'public, max-age=0, must-revalidate, s-maxage=86400, stale-while-revalidate=604800',
  unknown: 'public, max-age=0, must-revalidate, s-maxage=3600',
  failed: 'public, max-age=0, must-revalidate, s-maxage=60',
};
const API_WAIT_MS = 4000;
const INDEX_WAIT_MS = 8000;
const LOGO = { '@type': 'ImageObject', url: `${SITE}/logo.png`, width: 512, height: 512 };
const ORG = { '@type': 'Organization', '@id': `${SITE}/#org`, name: 'Swing Desk', url: `${SITE}/`, logo: LOGO };

const T = {
  en: {
    locale: 'en_US', home: 'Scanner', stocks: 'Stocks', learn: 'Learn', about: 'About', privacy: 'Privacy', terms: 'Terms',
    otherLang: 'עברית', crumbHome: 'Swing Desk',
    sector: 'Sector', industry: 'Industry', hq: 'Headquarters', employees: 'Employees', cap: 'Market cap', close: 'Last close',
    chg1: '1-month change', chg3: '3-month change', high: 'From 52-week high', stage: 'Trend stage', rs: 'Relative strength (RS)',
    report: 'Next earnings report', what: 'How the business works', revenue: 'Where the money comes from',
    products: 'Products and brands', customers: 'Customers and competitors', lastScan: 'Latest scan', similar: 'Similar companies',
    allStocks: 'All stocks', stages: { 1: 'Stage 1 (basing)', 2: 'Stage 2 (uptrend)', 3: 'Stage 3 (topping)', 4: 'Stage 4 (downtrend)' },
    scanLine: (d, t, p) => `On ${d}, ${t} appeared in the Swing Desk scanner with this setup: ${p}. A scan describes a technical pattern. It is not a recommendation.`,
    sourceLine: (s) => `Company description: summarized automatically by an AI model from ${s}, so it may contain errors. The original source is the authority.`,
    sources: { 'sec-10k': (d) => `the company's annual report to the SEC (10-K${d ? `, filed ${d}` : ''})`, 'sec-20f': (d) => `the company's annual report to the SEC (20-F${d ? `, filed ${d}` : ''})`, wikipedia: () => 'Wikipedia (CC BY-SA)' },
    pricesLine: (d) => `Prices and ratings as of ${d}. The interactive page shows newer data.`,
    title: (n, t) => `${n} (${t}) Stock: Chart, Technical Analysis & Earnings | Swing Desk`,
    plainTitle: (t) => `${t} — stock analysis · Swing Desk`,
    description: (n, t, s) => `${n} (${t}): ${s}`,
    plainDescription: (t) => `${t}: chart, technical setup, fundamentals, earnings and news on Swing Desk. Information only, not investment advice.`,
    stocksTitle: 'All Stocks: Company Profiles and Technical Analysis | Swing Desk',
    stocksHeading: 'All stocks',
    stocksIntro: 'Large US stocks covered by Swing Desk, by sector. Each page has a short company profile from its annual report, the current technical setup, earnings dates and fundamentals.',
    stocksDescription: 'Company profiles, charts, technical setups and earnings dates for large US stocks, listed by sector. Information only, not investment advice.',
    otherSector: 'Other',
    learnTitle: 'Learn Swing Trading: Chart Patterns and Market Data Explained | Swing Desk',
    learnHeading: 'Learn',
    learnIntro: 'What each chart pattern and market figure on Swing Desk means, and the exact rules the scanner checks. Written to explain, not to recommend.',
    learnDescription: 'Guides to chart patterns, relative strength, ATR, market breadth, the yield curve, insider filings and more, with the exact rules the Swing Desk scanner uses.',
    updated: 'Updated', related: 'Related guides', ctaText: 'See which stocks match these rules today on the Swing Desk scanner.', cta: 'Open the scanner',
    footerNote: 'Information only, not investment advice.',
  },
  he: {
    locale: 'he_IL', home: 'הסורק', stocks: 'מניות', learn: 'לימוד', about: 'אודות', privacy: 'פרטיות', terms: 'תנאי שימוש',
    otherLang: 'English', crumbHome: 'Swing Desk',
    sector: 'סקטור', industry: 'ענף', hq: 'מטה', employees: 'עובדים', cap: 'שווי שוק', close: 'מחיר סגירה אחרון',
    chg1: 'שינוי בחודש', chg3: 'שינוי ב-3 חודשים', high: 'מרחק משיא 52 שבועות', stage: 'שלב מגמה', rs: 'עוצמה יחסית (RS)',
    report: 'הדוח הבא', what: 'איך העסק עובד', revenue: 'מאיפה מגיע הכסף', products: 'מוצרים ומותגים',
    customers: 'לקוחות ומתחרים', lastScan: 'הסריקה האחרונה', similar: 'חברות דומות', allStocks: 'כל המניות',
    stages: { 1: 'שלב 1 (בסיס)', 2: 'שלב 2 (מגמת עלייה)', 3: 'שלב 3 (היפוך אפשרי מלמעלה)', 4: 'שלב 4 (מגמת ירידה)' },
    scanLine: (d, t, p) => `ב-${d} המניה ${t} הופיעה בסורק של Swing Desk עם המבנה: ${p}. סריקה מתארת תבנית טכנית ואינה המלצה.`,
    sourceLine: (s) => `תיאור החברה: סוכם אוטומטית על ידי מודל AI מתוך ${s}, ולכן ייתכנו בו טעויות. המקור המקורי הוא הקובע.`,
    sources: { 'sec-10k': (d) => `הדוח השנתי של החברה ל-SEC (טופס 10-K${d ? `, הוגש ב-${d}` : ''})`, 'sec-20f': (d) => `הדוח השנתי של החברה ל-SEC (טופס 20-F${d ? `, הוגש ב-${d}` : ''})`, wikipedia: () => 'ויקיפדיה (CC BY-SA)' },
    pricesLine: (d) => `המחירים והדירוגים נכונים ל-${d}. בעמוד האינטראקטיבי מוצגים נתונים עדכניים יותר.`,
    title: (n, t) => `מניית ${n} (${t}): ניתוח טכני, דוחות וחדשות | Swing Desk`,
    plainTitle: (t) => `${t} — ניתוח מניה · Swing Desk`,
    description: (n, t, s) => `${n} (${t}): ${s}`,
    plainDescription: (t) => `${t}: גרף, מבנה טכני, נתונים פונדמנטליים, דוחות וחדשות ב-Swing Desk. מידע בלבד, לא ייעוץ השקעות.`,
    stocksTitle: 'כל המניות: פרופיל חברה וניתוח טכני | Swing Desk',
    stocksHeading: 'כל המניות',
    stocksIntro: 'מניות אמריקאיות גדולות שמכוסות ב-Swing Desk, לפי סקטור. בכל עמוד יש פרופיל קצר של החברה מתוך הדוח השנתי שלה, המבנה הטכני הנוכחי, מועדי דוחות ונתונים פונדמנטליים.',
    stocksDescription: 'פרופיל חברה, גרף, מבנה טכני ומועדי דוחות למניות אמריקאיות גדולות, לפי סקטור. מידע בלבד, לא ייעוץ השקעות.',
    otherSector: 'אחר',
    learnTitle: 'לימוד מסחר סווינג: תבניות גרף ונתוני שוק בהסבר פשוט | Swing Desk',
    learnHeading: 'לימוד',
    learnIntro: 'מה אומרים התבניות והנתונים שמופיעים ב-Swing Desk, ומה הכללים המדויקים שהסורק בודק. נכתב כדי להסביר, לא כדי להמליץ.',
    learnDescription: 'מדריכים לתבניות גרף, עוצמה יחסית, ATR, רוחב שוק, עקום התשואות, דיווחי בעלי עניין ועוד, עם הכללים המדויקים של הסורק של Swing Desk.',
    updated: 'עודכן', related: 'מדריכים קשורים', ctaText: 'אפשר לראות בסורק של Swing Desk אילו מניות עומדות בכללים האלה היום.', cta: 'לפתיחת הסורק',
    footerNote: 'מידע בלבד, לא ייעוץ השקעות.',
  },
};

const HOME = {
  en: { title: 'Swing Desk — Stock Scanner, Technical Analysis & Market News', description: 'Swing Desk is a swing-trading dashboard for US stocks: a technical-pattern scanner, macro news with a daily summary, an economic calendar and a market heatmap. Information only, not investment advice.' },
  he: { title: 'Swing Desk — סורק מניות, ניתוח טכני וחדשות שוק', description: 'Swing Desk הוא דשבורד למסחר סווינג במניות אמריקאיות: סורק מניות לפי מבנים טכניים, חדשות מאקרו עם סיכום יומי, יומן כלכלי ומפת חום. מידע בלבד, לא ייעוץ השקעות.' },
};

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
const localPath = (p, lang) => (lang === 'he' ? (p === '/' ? '/he' : `/he${p}`) : p);
const abs = (p, lang) => SITE + localPath(p, lang);

function cut(text, limit) {
  const s = String(text || '').replace(/\s+/g, ' ').trim();
  if (s.length <= limit) return s;
  const at = s.lastIndexOf(' ', limit - 1);
  return `${s.slice(0, at > limit * 0.6 ? at : limit - 1).replace(/[,;:.\s]+$/, '')}…`;
}

// Yahoo's names, shortened for titles: "Coca-Cola Company (The)" -> "Coca-Cola", "JPMorgan Chase & Co." -> "JPMorgan Chase",
// "Berkshire Hathaway Inc. New" -> "Berkshire Hathaway"
function shortName(name) {
  let s = String(name || '').trim().replace(/^The\s+/i, '').replace(/\s*\(The\)$/i, '').replace(/\s+New$/, '')
    .replace(/\s+(?:Class [A-C]\s+)?(?:Common Stock|Common Shares|Ordinary Shares|American Depositary Shares)\b.*$/i, '');
  const tail = /(?:,|\s)+(?:&\s*Co\.?|Inc\.?|Incorporated|Corp\.?|Corporation|Company|Co\.?|plc|Ltd\.?|Limited|N\.?V\.?|S\.?A\.?|Holdings?|Group)$/i;
  for (let i = 0; i < 4 && tail.test(s); i += 1) s = s.replace(tail, '').replace(/[\s,&]+$/, '');
  return s.trim() || String(name || '');
}

function fmtDate(iso, lang) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
  if (!m) return '';
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return lang === 'he' ? `${+m[3]}.${+m[2]}.${m[1]}` : `${months[+m[2] - 1]} ${+m[3]}, ${m[1]}`;
}
function fmtCap(v) {
  if (!v || !Number.isFinite(v) || v <= 0) return null;
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  return `$${Math.round(v / 1e6)}M`;
}
const fmtPct = (v) => (v == null ? null : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`);
const fmtPrice = (v) => (v == null ? null : `$${v.toFixed(2)}`);
const fmtInt = (v) => String(Math.round(v)).replace(/\B(?=(\d{3})+(?!\d))/g, ',');

function jsonLd(graph) {
  const body = JSON.stringify({ '@context': 'https://schema.org', '@graph': graph }).replace(/</g, '\\u003c');
  return `<script type="application/ld+json">${body}</script>`;
}

function crumbs(id, items) {
  return { '@type': 'BreadcrumbList', '@id': id, itemListElement: items.map((x, i) => ({ '@type': 'ListItem', position: i + 1, name: x[0], item: x[1] })) };
}

// The block between <!--seo-head-start--> and <!--seo-head-end--> of index.html; the standalone pages use it too.
// langs = the languages this page really exists in (hreflang only between pages that can be listed).
function headTags(o) {
  const url = abs(o.path, o.lang);
  const alts = o.langs && o.langs.length > 1
    ? o.langs.map((l) => `<link rel="alternate" hreflang="${l}" href="${abs(o.path, l)}">`).join('\n')
      + `\n<link rel="alternate" hreflang="x-default" href="${abs(o.path, 'en')}">`
    : '';
  return `<title>${esc(o.title)}</title>
<meta name="description" content="${esc(o.description)}">
<link rel="canonical" href="${url}">
<meta name="robots" content="${o.robots || 'index, follow'}">
${alts}
<meta property="og:type" content="${o.ogType || 'website'}">
<meta property="og:url" content="${url}">
<meta property="og:site_name" content="Swing Desk">
<meta property="og:locale" content="${T[o.lang].locale}">
<meta property="og:title" content="${esc(o.title)}">
<meta property="og:description" content="${esc(o.description)}">
<meta property="og:image" content="${SITE}/og-image.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="Swing Desk">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="${esc(o.title)}">
<meta name="twitter:description" content="${esc(o.description)}">
<meta name="twitter:image" content="${SITE}/og-image.png">
${o.graph ? jsonLd(o.graph) : ''}${o.extra ? `\n${o.extra}` : ''}`;
}

// ---- the app's page (index.html) ----
let indexCache = null;
async function indexHtml() {
  if (indexCache) return indexCache;
  for (const p of [path.join(__dirname, '..', 'index.html'), path.join(process.cwd(), 'index.html')]) {
    try {
      indexCache = fs.readFileSync(p, 'utf8');
      return indexCache;
    } catch (e) { /* the next place */ }
  }
  const r = await fetch(`${SITE}/index.html`);          // vercel.json includes the file; this is only a safety net
  if (!r.ok) throw new Error(`index.html: ${r.status}`);
  indexCache = await r.text();
  return indexCache;
}

function appPage(index, lang, head, block) {
  let html = index
    .replace(/<!--seo-head-start-->[\s\S]*?<!--seo-head-end-->/, () => `<!--seo-head-start-->\n${head}\n<!--seo-head-end-->`)
    .replace(/<html lang="[a-z]+"/, `<html lang="${lang}"`);
  if (block !== undefined) {                            // a stock's address: the analysis page is the one showing
    html = html
      .replace('<div id="page-home">', '<div id="page-home" style="display:none;">')
      .replace('<div id="page-deepdive" style="display:none;">', '<div id="page-deepdive">')
      .replace('<div id="deepdive-content"></div>', () => `<div id="deepdive-content">${block}</div>`);
  }
  return html;
}

async function apiJson(url, wait) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), wait);
  try {
    const r = await fetch(url, { signal: ctl.signal, headers: { Accept: 'application/json' } });
    return r.ok ? await r.json() : null;
  } catch (e) {
    return null;                                        // the API asleep or down: the page still works without the facts
  } finally {
    clearTimeout(timer);
  }
}

function stockLink(ticker, name, lang) {
  return `<a href="${localPath(`/stock/${ticker}`, lang)}">${esc(name ? `${shortName(name)} (${ticker})` : ticker)}</a>`;
}

function stockBlock(row, lang) {
  const L = T[lang];
  const t = row.ticker;
  const n = row.numbers || {};
  const f = row.facts || {};
  const p = (row.profile || {})[lang];
  const learn = (slug, text) => `<a href="${localPath(`/learn/${slug}`, lang)}">${esc(text)}</a>`;
  const facts = [
    [L.sector, row.sector && esc(lang === 'he' ? (C.SECTOR_HE[row.sector] || row.sector) : row.sector)],
    [L.industry, lang === 'en' && f.industry ? esc(f.industry) : null],
    [L.hq, f.hq && `<bdi>${esc(f.hq)}</bdi>`],
    [L.employees, f.employees && `<bdi>${fmtInt(f.employees)}</bdi>`],
    [L.cap, fmtCap(row.market_cap) && `<bdi>${fmtCap(row.market_cap)}</bdi>`],
    [L.close, n.close != null && `<bdi>${fmtPrice(n.close)}</bdi>`],
    [L.chg1, n.chg_1m != null && `<bdi>${fmtPct(n.chg_1m)}</bdi>`],
    [L.chg3, n.chg_3m != null && `<bdi>${fmtPct(n.chg_3m)}</bdi>`],
    [L.high, n.from_high != null && `<bdi>${fmtPct(n.from_high)}</bdi>`],
    [L.stage, L.stages[n.stage] && learn('stage-analysis', L.stages[n.stage])],
    [L.rs, n.rs != null && learn('rs-rating', String(n.rs))],
    [L.report, row.next_report && `<bdi>${fmtDate(row.next_report, lang)}</bdi>`],
  ].filter((x) => x[1]);
  const sections = p ? ['what', 'revenue', 'products', 'customers'].filter((k) => p[k])
    .map((k) => `<h2>${L[k]}</h2><p>${esc(p[k])}</p>`).join('') : '';
  const pat = row.scan && C.PATTERNS[row.scan.pattern];
  const scan = pat ? `<h2>${L.lastScan}</h2><p>${L.scanLine(fmtDate(row.scan.day, lang), esc(t), learn(pat.slug, pat[lang]))}</p>` : '';
  const src = row.source && L.sources[row.source.kind];
  const srcLine = src && p
    ? L.sourceLine(row.source.url && /^https:\/\//.test(row.source.url)
      ? `<a href="${esc(row.source.url)}" rel="nofollow noopener" target="_blank">${esc(src(fmtDate(row.source.filed, lang)))}</a>`
      : esc(src(fmtDate(row.source.filed, lang)))) : '';
  const peers = (row.peers || []).filter((x) => (x.langs || []).includes(lang)).map((x) => stockLink(x.ticker, x.name, lang));
  const allLink = `<a href="${localPath('/stocks', lang)}">${L.allStocks}</a>`;
  return `<section id="seo-stock" class="seo-stock" data-ticker="${esc(t)}" data-lang="${lang}"${lang === 'he' ? ' dir="rtl"' : ''}>
<h1>${esc(shortName(row.name || t))} <span class="seo-tk"><bdi>(${esc(t)})</bdi></span></h1>
${p ? `<p class="seo-lead">${esc(p.summary)}</p>` : ''}
${facts.length ? `<dl class="seo-facts">${facts.map((x) => `<div><dt>${x[0]}</dt><dd>${x[1]}</dd></div>`).join('')}</dl>` : ''}
${sections}${scan}
<nav class="seo-links" aria-label="${L.similar}">${peers.length ? `<h2>${L.similar}</h2>${peers.join(' · ')} · ` : ''}${allLink}</nav>
<p class="seo-note">${srcLine}${srcLine ? ' ' : ''}${n.as_of ? `${L.pricesLine(fmtDate(n.as_of, lang))} ` : ''}${C.DISCLAIMER[lang]}</p>
</section>`;
}

function stockGraph(row, lang, url, title, description) {
  const name = shortName(row.name || row.ticker);
  const f = row.facts || {};
  const corp = { '@type': 'Corporation', '@id': `${url}#corp`, name, tickerSymbol: row.ticker };
  if (row.profile && row.profile[lang]) corp.description = row.profile[lang].summary;
  if (f.employees) corp.numberOfEmployees = { '@type': 'QuantitativeValue', value: f.employees };
  const n = row.numbers || {};
  return [
    { '@type': 'WebPage', '@id': `${url}#webpage`, url, name: title, description, inLanguage: lang, about: { '@id': `${url}#corp` },
      breadcrumb: { '@id': `${url}#crumbs` }, publisher: { '@id': `${SITE}/#org` }, ...(n.as_of ? { dateModified: n.as_of } : {}) },
    corp, ORG,
    crumbs(`${url}#crumbs`, [[T[lang].crumbHome, abs('/', lang)], [T[lang].stocks, abs('/stocks', lang)], [`${name} (${row.ticker})`, url]]),
  ];
}

async function stockPage(q, lang) {
  const index = await indexHtml();
  const raw = String(q.ticker || '').trim();
  const upper = raw.toUpperCase();
  const m = /^([A-Z]{1,5})\.([A-Z])$/.exec(upper);
  const t = m ? `${m[1]}-${m[2]}` : upper;              // BRK.B and brk-b have one address: BRK-B
  const page = (head, block, cache, status) => ({ status: status || 200, cache, body: appPage(index, lang, head, block) });
  if (!APP_TICKER_RE.test(t)) {
    const head = headTags({ lang, path: '/', title: HOME[lang].title, description: HOME[lang].description, robots: 'noindex, follow' });
    return page(head, undefined, KEEP.unknown, 404);
  }
  if (raw !== t) return { status: 301, location: localPath(`/stock/${encodeURIComponent(t)}`, lang), cache: KEEP.fixed };
  const plain = (robots, cache) => page(
    headTags({ lang, path: `/stock/${encodeURIComponent(t)}`, title: T[lang].plainTitle(t), description: T[lang].plainDescription(t), robots }),
    '', cache);
  if (!TICKER_RE.test(t)) return plain('noindex, follow', KEEP.unknown);
  const row = await apiJson(`${API}/api/seo/stock/${encodeURIComponent(t)}`, API_WAIT_MS);
  if (!row) return plain('index, follow', KEEP.failed);
  if (!row.known) return plain('noindex, follow', KEEP.unknown);
  const name = shortName(row.name || t);
  const p = (row.profile || {})[lang];
  const langs = LANGS.filter((l) => (row.profile || {})[l]);
  const title = T[lang].title(name, t);
  const description = p ? cut(T[lang].description(name, t, p.summary), 158) : T[lang].plainDescription(t);
  const url = abs(`/stock/${t}`, lang);
  const titles = { en: T.en.title(name, t), he: T.he.title(name, t) };
  const extra = `<script>window.__seoStock=${JSON.stringify({ ticker: t, titles }).replace(/</g, '\\u003c')};</script>`;
  const head = headTags({
    lang, path: `/stock/${t}`, title, description, langs, extra, robots: p ? 'index, follow' : 'noindex, follow',
    graph: stockGraph(row, lang, url, title, description),
  });
  return page(head, stockBlock(row, lang), KEEP.stock);
}

function homeGraph(lang) {
  const url = abs('/', lang);
  return [
    { ...ORG, description: HOME.en.description },
    { '@type': 'WebSite', '@id': `${SITE}/#website`, url: `${SITE}/`, name: 'Swing Desk', inLanguage: ['en', 'he'], publisher: { '@id': `${SITE}/#org` } },
    { '@type': 'WebPage', '@id': `${url}#webpage`, url, name: HOME[lang].title, description: HOME[lang].description, inLanguage: lang,
      isPartOf: { '@id': `${SITE}/#website` }, about: { '@id': `${SITE}/#app` } },
    { '@type': 'SoftwareApplication', '@id': `${SITE}/#app`, name: 'Swing Desk', applicationCategory: 'FinanceApplication', operatingSystem: 'Any (web browser)',
      description: 'Information only, not investment advice.', publisher: { '@id': `${SITE}/#org` } },
  ];
}

async function homePage(lang) {
  const head = headTags({ lang, path: '/', title: HOME[lang].title, description: HOME[lang].description, langs: LANGS, graph: homeGraph(lang) });
  return { status: 200, cache: KEEP.fixed, body: appPage(await indexHtml(), lang, head) };
}

// ---- the site's own pages (no app around them) ----
const SHELL_CSS = `
:root{--bg:#0f1216;--panel:#161a20;--line:#262c36;--text:#eef1f5;--text-dim:#8a93a3;--amber:#e8a33d;--on-amber:#1a1300;--teal:#35b3a3;color-scheme:dark}
:root[data-theme="light"]{--bg:#fff;--panel:#f2f3f6;--line:#aeb6c3;--text:#05080f;--text-dim:#364052;--amber:#b05d00;--on-amber:#fff;--teal:#006b5e;color-scheme:light}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--bg);color:var(--text);font-family:'Heebo',system-ui,sans-serif;font-size:16.5px;line-height:1.75}
a{color:var(--teal)}
.top{display:flex;flex-wrap:wrap;align-items:center;gap:6px 18px;padding:12px 16px;border-bottom:1px solid var(--line)}
.brand{display:inline-flex;align-items:center;gap:8px;font-weight:700;color:var(--text);text-decoration:none}
.brand img{width:24px;height:24px;display:block}
.top nav{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:14.5px}
.top nav a{color:var(--text-dim);text-decoration:none}
.top nav a:hover{color:var(--text)}
main{max-width:760px;margin:0 auto;padding:18px 16px 36px}
.crumbs{font-size:13px;color:var(--text-dim)}
.crumbs a{color:var(--text-dim)}
h1{font-size:30px;line-height:1.25;margin:10px 0 6px}
h2{font-size:21px;line-height:1.3;margin:28px 0 6px}
h3{font-size:17px;margin:20px 0 4px}
p{margin:10px 0}
ul,ol{margin:10px 0}
li{margin:4px 0}
.dim{color:var(--text-dim);font-size:13.5px}
.lead{font-size:18px;color:var(--text-dim)}
.box{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:22px 0}
.btn{display:inline-block;background:var(--amber);color:var(--on-amber);font-weight:700;border-radius:8px;padding:8px 16px;text-decoration:none}
.cards{list-style:none;padding:0;margin:8px 0}
.cards li{border-bottom:1px solid var(--line);padding:9px 0;margin:0}
.cards a{font-weight:500}
.cards span{display:block;font-size:14px;color:var(--text-dim)}
.tickers{list-style:none;padding:0;margin:8px 0;display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:2px 16px;font-size:14.5px}
.tickers li{margin:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
footer{border-top:1px solid var(--line);padding:16px;font-size:13px;color:var(--text-dim);text-align:center}
footer a{color:var(--text-dim);margin:0 6px}
@media(max-width:600px){h1{font-size:25px}html,body{font-size:16px}}
`;
const THEME_JS = "try{if(localStorage.getItem('swing_theme')==='light')document.documentElement.setAttribute('data-theme','light')}catch(e){}";

function shell(o) {
  const L = T[o.lang];
  const home = localPath('/', o.lang);
  const other = o.lang === 'he' ? 'en' : 'he';
  const link = (p, text) => `<a href="${localPath(p, o.lang)}">${text}</a>`;
  return `<!DOCTYPE html>
<html lang="${o.lang}" dir="${o.lang === 'he' ? 'rtl' : 'ltr'}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
${headTags(o)}
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="theme-color" content="#0f1216">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;700&display=swap" rel="stylesheet">
<script>${THEME_JS}</script>
<style>${SHELL_CSS}</style>
</head>
<body>
<header class="top">
<a class="brand" href="${home}"><img src="/favicon.svg" alt="" width="24" height="24">Swing Desk</a>
<nav aria-label="${esc(L.home)}">${link('/', L.home)}${link('/stocks', L.stocks)}${link('/learn', L.learn)}${link('/about', L.about)}<a href="${localPath(o.path, other)}" lang="${other}" hreflang="${other}">${L.otherLang}</a></nav>
</header>
<main>
${o.main}
</main>
<footer>${link('/about', L.about)}${link('/privacy', L.privacy)}${link('/terms', L.terms)}<br>© Swing Desk. ${L.footerNote}</footer>
</body>
</html>`;
}

function crumbLine(lang, items) {
  return `<div class="crumbs">${items.map((x) => (x[1] ? `<a href="${x[1]}">${esc(x[0])}</a>` : esc(x[0]))).join(' › ')}</div>`;
}

function learnIndexPage(lang) {
  const L = T[lang];
  const url = abs('/learn', lang);
  const groups = C.GROUPS.map((g) => {
    const items = C.ARTICLES.filter((a) => a.group === g.id)
      .map((a) => `<li><a href="${localPath(`/learn/${a.slug}`, lang)}">${esc(a[lang].short)}</a><span>${esc(a[lang].description)}</span></li>`);
    return `<h2>${esc(g[lang])}</h2><ul class="cards">${items.join('')}</ul>`;
  }).join('\n');
  const main = `${crumbLine(lang, [[L.crumbHome, localPath('/', lang)], [L.learnHeading]])}
<h1>${L.learnHeading}</h1><p class="lead">${L.learnIntro}</p>
${groups}<p class="dim">${C.DISCLAIMER[lang]}</p>`;
  const graph = [
    { '@type': 'CollectionPage', '@id': `${url}#webpage`, url, name: L.learnTitle, description: L.learnDescription, inLanguage: lang, publisher: { '@id': `${SITE}/#org` } },
    ORG, crumbs(`${url}#crumbs`, [[L.crumbHome, abs('/', lang)], [L.learnHeading, url]]),
  ];
  return { status: 200, cache: KEEP.fixed, body: shell({ lang, path: '/learn', title: L.learnTitle, description: L.learnDescription, langs: LANGS, graph, main }) };
}

function notFoundPage(lang) {
  const L = T[lang];
  const he = lang === 'he';
  const main = `<h1>${he ? 'העמוד לא נמצא' : 'Page not found'}</h1><p><a href="${localPath('/learn', lang)}">${L.learnHeading}</a> · <a href="${localPath('/', lang)}">${L.home}</a></p>`;
  return { status: 404, cache: KEEP.unknown, body: shell({ lang, path: '/learn', title: he ? 'העמוד לא נמצא | Swing Desk' : 'Page not found | Swing Desk', description: L.learnDescription, robots: 'noindex, follow', main }) };
}

function articlePage(slug, lang) {
  const a = C.ARTICLES.find((x) => x.slug === slug);
  if (!a) return notFoundPage(lang);
  const L = T[lang];
  const x = a[lang];
  const p = `/learn/${a.slug}`;
  const url = abs(p, lang);
  const related = (a.related || []).map((s) => C.ARTICLES.find((r) => r.slug === s)).filter(Boolean)
    .map((r) => `<li><a href="${localPath(`/learn/${r.slug}`, lang)}">${esc(r[lang].short)}</a></li>`).join('');
  const main = `${crumbLine(lang, [[L.crumbHome, localPath('/', lang)], [L.learnHeading, localPath('/learn', lang)], [x.short]])}
<article>
<h1>${esc(x.title)}</h1>
<p class="dim">${L.updated}: <bdi>${fmtDate(C.PUBLISHED, lang)}</bdi></p>
${x.body}
</article>
<div class="box"><p style="margin-top:0">${L.ctaText}</p><a class="btn" href="${localPath('/', lang)}">${L.cta}</a></div>
${related ? `<h2>${L.related}</h2><ul>${related}</ul>` : ''}
<p class="dim">${C.DISCLAIMER[lang]}</p>`;
  const graph = [
    { '@type': 'Article', '@id': `${url}#article`, mainEntityOfPage: url, headline: x.title, description: x.description, inLanguage: lang,
      datePublished: C.PUBLISHED, dateModified: C.PUBLISHED, image: `${SITE}/og-image.png`, author: { '@id': `${SITE}/#org` }, publisher: { '@id': `${SITE}/#org` } },
    ORG, crumbs(`${url}#crumbs`, [[L.crumbHome, abs('/', lang)], [L.learnHeading, abs('/learn', lang)], [x.short, url]]),
  ];
  return { status: 200, cache: KEEP.fixed, body: shell({ lang, path: p, title: `${x.title} | Swing Desk`, description: x.description, langs: LANGS, ogType: 'article', graph, main }) };
}

function docPage(doc, lang) {
  const d = C.DOCS[doc] && C.DOCS[doc][lang];
  if (!d) return notFoundPage(lang);
  const url = abs(`/${doc}`, lang);
  const main = `${crumbLine(lang, [[T[lang].crumbHome, localPath('/', lang)], [d.short]])}\n<h1>${esc(d.heading)}</h1>\n${d.body}`;
  const graph = [{ '@type': doc === 'about' ? 'AboutPage' : 'WebPage', '@id': `${url}#webpage`, url, name: d.title, description: d.description, inLanguage: lang, publisher: { '@id': `${SITE}/#org` } }, ORG];
  return { status: 200, cache: KEEP.fixed, body: shell({ lang, path: `/${doc}`, title: d.title, description: d.description, langs: LANGS, graph, main }) };
}

async function stocksPage(lang) {
  const L = T[lang];
  const data = await apiJson(`${API}/api/seo/index`, INDEX_WAIT_MS);
  const sectorName = (s) => (lang === 'he' ? (C.SECTOR_HE[s] || s) : s);
  let list = '';
  if (data && Array.isArray(data.stocks)) {
    const bySector = {};
    data.stocks.filter((s) => (s.langs || []).includes(lang) && TICKER_RE.test(s.ticker)).forEach((s) => {
      (bySector[s.sector || ''] = bySector[s.sector || ''] || []).push(s);
    });
    const names = Object.keys(bySector).filter(Boolean).sort((a, b) => sectorName(a).localeCompare(sectorName(b), lang));
    if (bySector['']) names.push('');
    list = names.map((s) => `<h2>${esc(s ? sectorName(s) : L.otherSector)} <span class="dim"><bdi>(${bySector[s].length})</bdi></span></h2>
<ul class="tickers">${bySector[s].map((x) => `<li>${stockLink(x.ticker, x.name, lang)}</li>`).join('')}</ul>`).join('\n');
  }
  if (!list) list = `<ul class="tickers">${C.POPULAR.map((t) => `<li>${stockLink(t, null, lang)}</li>`).join('')}</ul>`;
  const url = abs('/stocks', lang);
  const main = `${crumbLine(lang, [[L.crumbHome, localPath('/', lang)], [L.stocksHeading]])}
<h1>${L.stocksHeading}</h1><p class="lead">${L.stocksIntro}</p>
${list}<p class="dim">${C.DISCLAIMER[lang]}</p>`;
  const graph = [
    { '@type': 'CollectionPage', '@id': `${url}#webpage`, url, name: L.stocksTitle, description: L.stocksDescription, inLanguage: lang, publisher: { '@id': `${SITE}/#org` } },
    ORG, crumbs(`${url}#crumbs`, [[L.crumbHome, abs('/', lang)], [L.stocksHeading, url]]),
  ];
  return { status: 200, cache: data ? KEEP.stock : KEEP.failed, body: shell({ lang, path: '/stocks', title: L.stocksTitle, description: L.stocksDescription, langs: LANGS, graph, main }) };
}

// ---- the list of pages ----
function siteEntry(p, lastmod, langs) {
  const alts = langs.length > 1
    ? langs.map((l) => `<xhtml:link rel="alternate" hreflang="${l}" href="${abs(p, l)}"/>`).join('')
      + `<xhtml:link rel="alternate" hreflang="x-default" href="${abs(p, 'en')}"/>`
    : '';
  return langs.map((l) => `<url><loc>${abs(p, l)}</loc>${lastmod ? `<lastmod>${lastmod}</lastmod>` : ''}${alts}</url>`).join('\n');
}

async function sitemap() {
  const data = await apiJson(`${API}/api/seo/index`, INDEX_WAIT_MS);
  const fixed = ['/', '/stocks', '/learn', ...C.ARTICLES.map((a) => `/learn/${a.slug}`), '/about', '/privacy', '/terms'];
  const entries = fixed.map((p) => siteEntry(p, p.startsWith('/learn/') ? C.PUBLISHED : null, LANGS));
  ((data && data.stocks) || []).filter((s) => TICKER_RE.test(s.ticker)).forEach((s) => {
    const langs = LANGS.filter((l) => (s.langs || []).includes(l));
    if (langs.length) entries.push(siteEntry(`/stock/${s.ticker}`, /^\d{4}-\d{2}-\d{2}$/.test(s.day || '') ? s.day : null, langs));
  });
  const body = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">
${entries.join('\n')}
</urlset>
`;
  return { status: 200, cache: data ? KEEP.stock : KEEP.failed, body, type: 'application/xml; charset=utf-8' };
}

function readQuery(req) {
  if (req.query && Object.keys(req.query).length) return req.query;
  const out = {};
  new URL(req.url || '/', 'http://x').searchParams.forEach((v, k) => { out[k] = v; });
  return out;
}

async function build(q) {
  const lang = q.lang === 'he' ? 'he' : 'en';
  switch (q.kind) {
    case 'stock': return stockPage(q, lang);
    case 'home': return homePage(lang);
    case 'stocks': return stocksPage(lang);
    case 'learn-index': return learnIndexPage(lang);
    case 'learn': return articlePage(String(q.slug || ''), lang);
    case 'doc': return docPage(String(q.doc || ''), lang);
    case 'sitemap': return sitemap();
    default: return notFoundPage(lang);
  }
}

module.exports = async (req, res) => {
  const q = readQuery(req);
  let out;
  try {
    out = await build(q);
  } catch (e) {
    console.error('[page]', q.kind, e);
    const home = localPath('/', q.lang === 'he' ? 'he' : 'en');
    out = { status: 302, location: home === '/he' ? '/' : home, cache: KEEP.failed };   // "/" is a plain file: it always works
  }
  res.statusCode = out.status;
  res.setHeader('Cache-Control', out.cache);
  if (out.location) {
    res.setHeader('Location', out.location);
    res.end();
    return;
  }
  res.setHeader('Content-Type', out.type || 'text/html; charset=utf-8');
  res.end(out.body);
};
module.exports.shortName = shortName;
module.exports.cut = cut;
