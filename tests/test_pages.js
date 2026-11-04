// v35: api/page.js with a fake API: what a search engine gets for each kind of page, and that the site's files fit together.
// No network and no packages. In GitHub Actions: node tests/test_pages.js (tests.yml). On the owner's computer, where there is
// no node: tests/local_check/check-pages.html runs this same file in the browser.
const fs = require('fs');
const path = require('path');
const page = require('../api/page.js');
const C = require('../api/_content.js');

const ROOT = path.join(__dirname, '..');
const read = (f) => fs.readFileSync(path.join(ROOT, f), 'utf8');
const SITE = 'https://swing-desk-tau.vercel.app';
let failed = 0;
let passed = 0;
function ok(cond, msg) {
  if (cond) passed += 1;
  else { failed += 1; console.log(`FAILED: ${msg}`); }
}

const KO = {
  known: true, ticker: 'KO', name: 'The Coca-Cola Company', sector: 'Consumer Staples', market_cap: 414e9,
  numbers: { as_of: '2026-11-03', close: 71.25, from_high: -3.2, chg_1m: 1.5, chg_3m: -2, rs: 72, stage: 2 },
  profile: {
    en: { summary: 'The Coca-Cola Company is a beverage company.', what: 'It sells concentrates.', revenue: 'Concentrate sales.', products: 'Coca-Cola, Sprite.', customers: '' },
    he: { summary: 'Coca-Cola היא חברת משקאות.', what: 'היא מוכרת תרכיזים.', revenue: 'מכירת תרכיזים.', products: 'Coca-Cola, Sprite.', customers: '' },
  },
  facts: { industry: 'Beverages', hq: 'Atlanta, GA', employees: 65900 },
  source: { kind: 'sec-10k', url: 'https://www.sec.gov/Archives/edgar/data/21344/x.htm', filed: '2026-02-20' },
  scan: { pattern: 'cup_handle', day: '2026-11-02' }, next_report: '2026-11-20',
  peers: [{ ticker: 'PEP', name: 'PepsiCo, Inc.', langs: ['en', 'he'] }, { ticker: 'XYZ', name: 'Only English Inc.', langs: ['en'] }],
};
const ONLY_EN = { ...KO, ticker: 'ENG', name: 'English Only Inc.', profile: { en: KO.profile.en } };
const NO_PROFILE = { known: true, ticker: 'BARE', name: 'Bare Inc.', sector: 'Energy', market_cap: 2e9, numbers: KO.numbers };
const NASTY = {
  ...KO, ticker: 'BAD', name: 'Bad"><script>alert(1)</script> Inc.',
  profile: { en: { summary: '<img src=x onerror=alert(1)> makes </script><script>alert(2)</script> things.', what: '<b>x</b>' } },
  source: { kind: 'sec-10k', url: 'javascript:alert(1)', filed: '2026-02-20' },
};
const ROWS = { KO, ENG: ONLY_EN, BARE: NO_PROFILE, BAD: NASTY };
const INDEX = {
  stocks: [
    { ticker: 'KO', name: 'The Coca-Cola Company', sector: 'Consumer Staples', langs: ['en', 'he'], day: '2026-11-03' },
    { ticker: 'ENG', name: 'English Only Inc.', sector: 'Energy', langs: ['en'], day: '2026-11-03' },
    { ticker: 'NOSEC', name: 'No Sector Inc.', sector: null, langs: ['en', 'he'], day: null },
  ],
};

let apiDown = false;
global.fetch = async (url) => {
  if (apiDown) throw new Error('down');
  const m = /\/api\/seo\/stock\/([^/?]+)/.exec(url);
  const body = m ? (ROWS[decodeURIComponent(m[1])] || { known: false, ticker: m[1] }) : (/\/api\/seo\/index/.test(url) ? INDEX : null);
  return { ok: !!body, status: body ? 200 : 404, json: async () => body };
};

async function get(query) {
  const res = { statusCode: 0, headers: {}, body: '', setHeader(k, v) { this.headers[k.toLowerCase()] = v; }, end(b) { this.body = b || ''; } };
  await page({ query, url: '/api/page' }, res);
  return res;
}
const has = (r, s) => r.body.includes(s);
const headOf = (r) => r.body.slice(0, r.body.indexOf('</head>'));
function linkedData(r) {
  const out = [];
  const re = /<script type="application\/ld\+json">([\s\S]*?)<\/script>/g;
  for (let m = re.exec(headOf(r)); m; m = re.exec(headOf(r))) out.push(JSON.parse(m[1]));
  return out;
}
const types = (r) => linkedData(r).flatMap((x) => x['@graph'].map((g) => g['@type']));

async function run() {
  // ---- the files fit together ----
  const index = read('index.html');
  const vercel = JSON.parse(read('vercel.json'));
  ok(/^<!DOCTYPE html>\n<html lang="en" dir="ltr">/.test(index), 'index.html starts with lang="en"');
  ['<!--seo-head-start-->', '<!--seo-head-end-->', '<div id="page-home">', '<div id="page-deepdive" style="display:none;">', '<div id="deepdive-content"></div>']
    .forEach((s) => ok(index.split(s).length === 2, `index.html has exactly one ${s} (api/page.js replaces it)`));
  ok(!read('.vercelignore').includes('sitemap.xml'), '.vercelignore: no static sitemap (a file would win over the rewrite)');
  ok(read('.vercelignore').includes('!api'), '.vercelignore lets api/ through');
  ok(read('robots.txt').includes('Disallow: /api/') && read('robots.txt').includes(`Sitemap: ${SITE}/sitemap.xml`), 'robots.txt');
  ok(vercel.functions['api/page.js'].includeFiles === 'index.html', 'the function gets index.html');
  const kinds = new Set();
  vercel.rewrites.forEach((r) => {
    const q = new URLSearchParams(r.destination.split('?')[1]);
    kinds.add(q.get('kind'));
    ok(r.destination.startsWith('/api/page?'), `rewrite ${r.source} goes to the function`);
    ok(r.source.startsWith('/he') === (q.get('lang') === 'he') || !q.get('lang'), `rewrite ${r.source} has the right language`);
  });
  ['stock', 'home', 'stocks', 'learn-index', 'learn', 'doc', 'sitemap'].forEach((k) => ok(kinds.has(k), `a rewrite goes to ${k}`));
  const staticTitle = /<title>([^<]*)<\/title>/.exec(index)[1];
  ok(staticTitle === 'Swing Desk — Stock Scanner, Technical Analysis &amp; Market News', 'the English title in index.html');
  ok(index.includes('Swing Desk — Stock Scanner, Technical Analysis & Market News\', he: \'Swing Desk — סורק מניות'), 'HOME_TITLES in the script');
  const slugs = new Set(C.ARTICLES.map((a) => a.slug));
  const hrefs = [...index.matchAll(/href="(\/[^"]*)"/g)].map((m) => m[1]);
  hrefs.filter((h) => /\/learn\//.test(h)).forEach((h) => ok(slugs.has(h.split('/').pop()), `index.html links to a real guide: ${h}`));
  hrefs.filter((h) => /\/stock\//.test(h)).forEach((h) => ok(C.POPULAR.includes(h.split('/').pop()), `footer stock is in POPULAR: ${h}`));
  C.POPULAR.forEach((t) => ok(hrefs.includes(`/stock/${t}`) && hrefs.includes(`/he/stock/${t}`), `footer links to ${t} in both languages`));
  const staticLd = JSON.parse(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/.exec(index)[1]);
  ok(staticLd['@graph'].length === 4, 'the JSON-LD in index.html parses');

  // ---- names and text helpers ----
  const names = {
    'The Coca-Cola Company': 'Coca-Cola', 'JPMorgan Chase & Co.': 'JPMorgan Chase', 'Alphabet Inc.': 'Alphabet', 'Meta Platforms, Inc.': 'Meta Platforms',
    'UnitedHealth Group Incorporated': 'UnitedHealth', 'Uber Technologies, Inc.': 'Uber Technologies', 'Microsoft Corp': 'Microsoft', '3M Company': '3M',
    'AT&T Inc.': 'AT&T', 'Carnival Corporation & plc': 'Carnival', 'Procter & Gamble Company': 'Procter & Gamble', 'Johnson & Johnson': 'Johnson & Johnson',
    'KO': 'KO', 'Coca-Cola Company (The)': 'Coca-Cola', 'Home Depot, Inc. (The)': 'Home Depot', 'Berkshire Hathaway Inc. New': 'Berkshire Hathaway',
    'Astera Labs, Inc. Common Stock': 'Astera Labs', 'Goldman Sachs Group, Inc. (The)': 'Goldman Sachs',
  };
  Object.keys(names).forEach((n) => ok(page.shortName(n) === names[n], `shortName(${n}) = ${page.shortName(n)}`));
  ok(page.cut('short', 158) === 'short', 'cut keeps short text');
  const longCut = page.cut('word '.repeat(60), 100);
  ok(longCut.length <= 100 && longCut.endsWith('…') && !longCut.includes('word …'), `cut: ${longCut}`);

  // ---- a stock page ----
  let r = await get({ kind: 'stock', lang: 'en', ticker: 'KO' });
  ok(r.statusCode === 200, 'KO 200');
  ok(has(r, '<title>Coca-Cola (KO) Stock: Chart, Technical Analysis &amp; Earnings | Swing Desk</title>'), 'KO title');
  ok(has(r, `<link rel="canonical" href="${SITE}/stock/KO">`), 'KO canonical');
  ok(has(r, `hreflang="he" href="${SITE}/he/stock/KO"`) && has(r, `hreflang="en" href="${SITE}/stock/KO"`) && has(r, `hreflang="x-default" href="${SITE}/stock/KO"`), 'KO hreflang');
  ok(has(r, '<meta name="robots" content="index, follow">'), 'KO is listed');
  ok(has(r, '<html lang="en"'), 'KO html lang');
  ok(has(r, '<div id="page-home" style="display:none;">') && has(r, '<div id="page-deepdive">') && !has(r, '<div id="page-deepdive" style="display:none;">'), 'the analysis page is the one showing');
  ok(has(r, 'id="seo-stock"') && has(r, 'data-ticker="KO"') && has(r, 'data-lang="en"'), 'KO block');
  ['<h1>Coca-Cola', 'The Coca-Cola Company is a beverage company.', 'Atlanta, GA', '65,900', '$414.0B', '$71.25', '+1.5%', '-2.0%', '-3.2%', 'Stage 2 (uptrend)', 'Nov 20, 2026',
    'href="/learn/cup-and-handle">Cup &amp; Handle', 'href="/stock/PEP"', 'href="/stock/XYZ"', 'href="/stocks"', 'href="/learn/rs-rating"', C.DISCLAIMER.en, 'rel="nofollow noopener"', 'Nov 3, 2026']
    .forEach((s) => ok(has(r, s), `KO page has ${s}`));
  ok(/window\.__seoStock=\{"ticker":"KO","titles":\{"en":"Coca-Cola \(KO\)[^"]*","he":"מניית Coca-Cola \(KO\)/.test(r.body), 'the app gets both titles');
  ok(r.headers['cache-control'].includes('s-maxage=21600'), 'KO is kept by the CDN');
  ok(types(r).includes('Corporation') && types(r).includes('BreadcrumbList') && types(r).includes('WebPage'), 'KO structured data');
  const corp = linkedData(r)[0]['@graph'].find((g) => g['@type'] === 'Corporation');
  ok(corp.tickerSymbol === 'KO' && corp.numberOfEmployees.value === 65900, 'KO corporation');
  const head = headOf(r);
  ok(head.split('<title>').length === 2 && head.split('rel="canonical"').length === 2, 'one title and one canonical');
  const desc = /<meta name="description" content="([^"]*)"/.exec(r.body)[1];
  ok(desc.length <= 158 && desc.startsWith('Coca-Cola (KO):'), `KO description: ${desc}`);

  r = await get({ kind: 'stock', lang: 'he', ticker: 'KO' });
  ok(has(r, '<html lang="he"'), 'he html lang');
  ok(has(r, `<link rel="canonical" href="${SITE}/he/stock/KO">`), 'he canonical');
  ok(has(r, '<title>מניית Coca-Cola (KO)'), 'he title');
  ok(has(r, 'data-lang="he" dir="rtl"'), 'he block is right to left');
  ['Coca-Cola היא חברת משקאות.', 'איך העסק עובד', 'href="/he/learn/cup-and-handle">כוס וידית', 'href="/he/stock/PEP"', '20.11.2026', 'שלב 2 (מגמת עלייה)', 'צריכה בסיסית', C.DISCLAIMER.he]
    .forEach((s) => ok(has(r, s), `he page has ${s}`));
  ok(!has(r, 'href="/he/stock/XYZ"'), 'no link to a Hebrew page that is not listed');
  ok(!has(r, '>Beverages<'), 'no English industry name in the Hebrew facts');

  // addresses
  for (const [ticker, lang, to] of [['ko', 'en', '/stock/KO'], ['BRK.B', 'en', '/stock/BRK-B'], ['brk-b', 'he', '/he/stock/BRK-B']]) {
    r = await get({ kind: 'stock', lang, ticker });
    ok(r.statusCode === 301 && r.headers.location === to, `${ticker} -> ${to} (${r.statusCode} ${r.headers.location})`);
  }
  r = await get({ kind: 'stock', lang: 'en', ticker: '<img src=x>' });
  ok(r.statusCode === 404 && has(r, 'noindex') && !has(r, '<img src=x>'), 'a bad address is refused and not repeated in the page');
  r = await get({ kind: 'stock', lang: 'en', ticker: '^GSPC' });
  ok(r.statusCode === 200 && has(r, 'noindex, follow') && has(r, '<div id="page-deepdive">'), 'an index opens in the app, not listed');
  ok(!has(r, 'id="seo-stock"'), 'no block for an index');

  // unknown, thin, one language, API down
  r = await get({ kind: 'stock', lang: 'en', ticker: 'NOPE' });
  ok(r.statusCode === 200 && has(r, 'noindex, follow') && !has(r, 'id="seo-stock"') && r.headers['cache-control'].includes('s-maxage=3600'), 'unknown stock');
  ok(has(r, '<title>NOPE — stock analysis'), 'unknown stock title');
  r = await get({ kind: 'stock', lang: 'en', ticker: 'BARE' });
  ok(has(r, 'noindex, follow') && has(r, 'id="seo-stock"') && has(r, '$71.25') && !headOf(r).includes('hreflang'), 'a stock without a profile: works, not listed');
  r = await get({ kind: 'stock', lang: 'en', ticker: 'ENG' });
  ok(has(r, 'content="index, follow"') && !headOf(r).includes('hreflang="he"'), 'English only: listed, no Hebrew alternate');
  r = await get({ kind: 'stock', lang: 'he', ticker: 'ENG' });
  ok(has(r, 'noindex, follow') && !headOf(r).includes('hreflang'), 'no Hebrew profile: the Hebrew page is not listed');
  ok(has(r, 'dir="rtl"') && !has(r, 'It sells concentrates'), 'and shows no English profile text');
  apiDown = true;
  r = await get({ kind: 'stock', lang: 'en', ticker: 'KO' });
  ok(r.statusCode === 200 && has(r, 'content="index, follow"') && r.headers['cache-control'].includes('s-maxage=60'), 'API down: still a page, kept for a minute only');
  ok(has(r, '<title>KO — stock analysis'), 'API down: title');
  apiDown = false;

  // nothing from the data can reach the page as markup
  r = await get({ kind: 'stock', lang: 'en', ticker: 'BAD' });
  ok(!has(r, '<img src=x') && !has(r, '<script>alert') && !has(r, '<b>x</b>'), 'profile text is escaped');
  ok(!has(r, 'javascript:alert') || !/href="javascript:/.test(r.body), 'a bad source address is not a link');
  linkedData(r).forEach((x) => ok(!JSON.stringify(x).includes('\\u003c'), 'JSON-LD parses back'));
  ok(!/<\/script><script>alert/.test(headOf(r)), 'the JSON-LD cannot close its own script tag');

  // ---- the Hebrew home page ----
  r = await get({ kind: 'home', lang: 'he' });
  ok(has(r, '<html lang="he"') && has(r, `<link rel="canonical" href="${SITE}/he">`) && has(r, '<title>Swing Desk — סורק מניות'), 'he home');
  ok(has(r, '<div id="page-home">') && !has(r, 'id="seo-stock"'), 'he home shows the home page');
  ok(has(r, `hreflang="en" href="${SITE}/"`) && has(r, `hreflang="he" href="${SITE}/he"`), 'he home hreflang');
  ok(types(r).includes('WebSite'), 'he home structured data');

  // ---- guides ----
  r = await get({ kind: 'learn-index', lang: 'en' });
  ok(r.statusCode === 200 && has(r, `<link rel="canonical" href="${SITE}/learn">`), 'learn index');
  C.ARTICLES.forEach((a) => ok(has(r, `href="/learn/${a.slug}"`), `the index lists ${a.slug}`));
  r = await get({ kind: 'learn-index', lang: 'he' });
  ok(has(r, 'dir="rtl"') && has(r, `<link rel="canonical" href="${SITE}/he/learn">`), 'he learn index');
  C.ARTICLES.forEach((a) => ok(has(r, `href="/he/learn/${a.slug}"`), `the Hebrew index lists ${a.slug}`));

  ok(C.ARTICLES.length === 24 && slugs.size === 24, `24 guides (${C.ARTICLES.length})`);
  ok(C.ARTICLES.every((a) => C.GROUPS.some((g) => g.id === a.group)), 'every guide is in a group');
  Object.keys(C.PATTERNS).forEach((p) => ok(slugs.has(C.PATTERNS[p].slug), `pattern ${p} has a guide`));
  const titles = new Set();
  for (const a of C.ARTICLES) {
    (a.related || []).forEach((s) => ok(slugs.has(s), `${a.slug} related ${s}`));
    for (const lang of ['en', 'he']) {
      const x = a[lang];
      const prefix = lang === 'he' ? '/he' : '';
      r = await get({ kind: 'learn', lang, slug: a.slug });
      ok(r.statusCode === 200, `${a.slug} ${lang} 200`);
      ok(has(r, `<link rel="canonical" href="${SITE}${prefix}/learn/${a.slug}">`), `${a.slug} ${lang} canonical`);
      ok(has(r, `<html lang="${lang}" dir="${lang === 'he' ? 'rtl' : 'ltr'}">`), `${a.slug} ${lang} direction`);
      ok(types(r).includes('Article') && types(r).includes('BreadcrumbList'), `${a.slug} ${lang} structured data`);
      ok(x.description.length >= 70 && x.description.length <= 200, `${a.slug} ${lang} description length ${x.description.length}`);
      ok(!titles.has(x.title), `${a.slug} ${lang} title is unique`);
      titles.add(x.title);
      const words = x.body.replace(/<[^>]+>/g, ' ').trim().split(/\s+/).length;
      ok(words >= 150, `${a.slug} ${lang} has text of its own: ${words} words`);
      ok(x.body.split('<h1').length === 1, `${a.slug} ${lang}: the page's only h1 comes from the template`);
      [...x.body.matchAll(/href="([^"]*)"/g)].map((m) => m[1]).forEach((h) => {
        ok(lang === 'he' ? h.startsWith('/he/') : !h.startsWith('/he'), `${a.slug} ${lang} links within its language: ${h}`);
        const m = /\/learn\/(.+)$/.exec(h);
        ok(!m || slugs.has(m[1]), `${a.slug} ${lang} links to a real guide: ${h}`);
      });
      ok(!/\b(you should|buy now|sell now|we recommend|good investment)\b/i.test(x.body), `${a.slug} ${lang}: no advice wording`);
      if (lang === 'he') {
        ok(/[א-ת]/.test(x.title) && /[א-ת]/.test(x.body), `${a.slug} he is in Hebrew`);
        ok(!/איתות/.test(x.body + x.title + x.description), `${a.slug} he: "סריקות", not "איתותים"`);
        ok(!/ממליצים|כדאי לקנות|כדאי למכור/.test(x.body), `${a.slug} he: no advice wording`);
      }
    }
  }
  r = await get({ kind: 'learn', lang: 'en', slug: 'no-such-guide' });
  ok(r.statusCode === 404 && has(r, 'noindex'), 'an unknown guide is a real 404');

  // ---- about, privacy, terms ----
  for (const doc of ['about', 'privacy', 'terms']) {
    for (const lang of ['en', 'he']) {
      r = await get({ kind: 'doc', lang, doc });
      const prefix = lang === 'he' ? '/he' : '';
      ok(r.statusCode === 200 && has(r, `<link rel="canonical" href="${SITE}${prefix}/${doc}">`), `${doc} ${lang}`);
      ok(has(r, C.DOCS[doc][lang].heading), `${doc} ${lang} heading`);
      ok(has(r, `href="${prefix}/privacy"`) && has(r, `href="${prefix}/terms"`), `${doc} ${lang} footer`);
    }
  }
  r = await get({ kind: 'doc', lang: 'en', doc: 'nothing' });
  ok(r.statusCode === 404, 'an unknown document');

  // ---- the list of stocks ----
  r = await get({ kind: 'stocks', lang: 'en' });
  ok(has(r, 'Consumer Staples') && has(r, 'href="/stock/KO"') && has(r, 'href="/stock/ENG"') && has(r, 'href="/stock/NOSEC"'), 'stocks list');
  r = await get({ kind: 'stocks', lang: 'he' });
  ok(has(r, 'צריכה בסיסית') && has(r, 'href="/he/stock/KO"') && !has(r, 'href="/he/stock/ENG"'), 'the Hebrew list has only pages listed in Hebrew');
  apiDown = true;
  r = await get({ kind: 'stocks', lang: 'en' });
  ok(r.statusCode === 200 && has(r, 'href="/stock/AAPL"') && r.headers['cache-control'].includes('s-maxage=60'), 'API down: the popular stocks');
  apiDown = false;

  // ---- sitemap ----
  r = await get({ kind: 'sitemap' });
  ok(r.headers['content-type'].includes('xml') && r.body.startsWith('<?xml'), 'sitemap is XML');
  [`${SITE}/`, `${SITE}/he`, `${SITE}/learn`, `${SITE}/he/learn/cup-and-handle`, `${SITE}/about`, `${SITE}/he/terms`, `${SITE}/stock/KO`, `${SITE}/he/stock/KO`, `${SITE}/stock/ENG`]
    .forEach((u) => ok(has(r, `<loc>${u}</loc>`), `sitemap lists ${u}`));
  ok(!has(r, `<loc>${SITE}/he/stock/ENG</loc>`), 'sitemap: no page that asks not to be listed');
  ok(has(r, `<loc>${SITE}/stock/KO</loc><lastmod>2026-11-03</lastmod><xhtml:link rel="alternate" hreflang="en" href="${SITE}/stock/KO"/>`), 'sitemap: date and alternates');
  ok(!/<loc>[^<]*<\/loc><xhtml:link[^>]*hreflang="he"[^>]*\/><\/url>\s*<url><loc>[^<]*ENG/.test(r.body), 'sitemap: no alternates for one language');
  ok((r.body.match(/<url>/g) || []).length === (r.body.match(/<\/url>/g) || []).length, 'sitemap is balanced');
  apiDown = true;
  r = await get({ kind: 'sitemap' });
  ok(has(r, `<loc>${SITE}/learn</loc>`) && !has(r, '/stock/') && r.headers['cache-control'].includes('s-maxage=60'), 'API down: the fixed pages');
  apiDown = false;

  r = await get({ kind: 'nothing', lang: 'en' });
  ok(r.statusCode === 404, 'an unknown kind');

  console.log(`pages: ${passed} passed, ${failed} failed`);
  if (typeof process !== 'undefined') process.exitCode = failed ? 1 : 0;
  return failed;
}

module.exports = run().catch((e) => {
  console.log(`CRASHED: ${e && e.stack}`);
  if (typeof process !== 'undefined') process.exitCode = 1;
  return 1;
});
