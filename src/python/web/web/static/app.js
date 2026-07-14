const PAGE = 60;

const els = {
  grid: document.getElementById('grid'),
  count: document.getElementById('count'),
  empty: document.getElementById('empty'),
  sentinel: document.getElementById('sentinel'),
  detail: document.getElementById('detail'),
  card: document.querySelector('.detail-card'),
  search: document.getElementById('search'),
  series: document.getElementById('series'),
  author: document.getElementById('author'),
  language: document.getElementById('language'),
  format: document.getElementById('format'),
  sort: document.getElementById('sort'),
  whoami: document.getElementById('whoami'),
};

const state = { offset: 0, total: 0, loading: false, done: false };

// --- api --------------------------------------------------------------

// A session that expired while the tab sat open turns every request into a 401.
// Bounce to the login page rather than letting the UI quietly render nothing, and
// remember where we were so signing back in returns us here.
async function api(path) {
  const res = await fetch(path);
  if (res.status === 401) {
    const here = location.pathname + location.search + location.hash;
    location.assign(`/login?next=${encodeURIComponent(here)}`);
    return new Promise(() => {}); // never settles; the navigation is already underway
  }
  return res;
}

const getJSON = async (path) => (await api(path)).json();

// --- formatting -------------------------------------------------------

const LANGUAGES = {
  en: 'English', pt: 'Portuguese', 'pt-PT': 'Portuguese', 'pt-BR': 'Portuguese (Brazil)',
  es: 'Spanish', fr: 'French', de: 'German', it: 'Italian', nl: 'Dutch',
  ru: 'Russian', ja: 'Japanese', zh: 'Chinese', sv: 'Swedish', la: 'Latin',
};

const language = (code) => (code ? LANGUAGES[code] || code.toUpperCase() : null);
const year = (date) => (date ? date.slice(0, 4) : null);

// Books acquired after the old library stopped recording carry this sentinel
// instead of a real date. It is in the future, so it can never be a genuine
// acquisition — say so rather than showing a date that isn't true.
const UNKNOWN_ACQUISITION = '2027-01-01';
const acquired = (date) => {
  if (!date) return null;
  if (date === UNKNOWN_ACQUISITION) return 'Acquired: date unknown';
  return `Acquired ${date}`;
};

function duration(seconds) {
  if (!seconds) return null;
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

function filesize(bytes) {
  if (!bytes) return null;
  const mb = bytes / 1048576;
  return mb < 1 ? `${Math.round(bytes / 1024)} KB` : `${mb.toFixed(1)} MB`;
}

function authorLine(book) {
  return book.authors.join(', ') || 'Unknown author';
}

function seriesLabel(book) {
  if (!book.series_name) return null;
  return book.series_position
    ? `${book.series_name} #${book.series_position}`
    : book.series_name;
}

const el = (tag, props = {}, ...children) => {
  const node = Object.assign(document.createElement(tag), props);
  for (const c of children) if (c != null) node.append(c);
  return node;
};

// --- tiles ------------------------------------------------------------

function tile(book, onClick) {
  const art = el('div', { className: 'art' });

  if (book.cover) {
    const img = el('img', {
      loading: 'lazy',
      alt: '',
      src: `/covers/${book.cover.type}/${book.cover.id}`,
    });
    // The cover file can disappear from disk under us; fall back rather than
    // leaving a broken image.
    img.onerror = () => { img.remove(); art.prepend(fallback(book)); };
    art.append(img);
  } else {
    art.append(fallback(book));
  }

  const badges = el('div', { className: 'badges' });
  if (book.epub_id) badges.append(el('span', { className: 'badge', textContent: 'EPUB' }));
  if (book.m4b_id) badges.append(el('span', { className: 'badge', textContent: 'AUDIO' }));
  if (badges.children.length) art.append(badges);

  const node = el(
    'button',
    { className: 'tile', type: 'button' },
    art,
    el('div', { className: 't', textContent: book.title }),
    el('div', { className: 'a', textContent: authorLine(book) }),
  );
  node.onclick = () => onClick(book);
  return node;
}

const fallback = (book) =>
  el(
    'div',
    { className: 'fallback' },
    el('div', { className: 'ft', textContent: book.title }),
    el('div', { className: 'fa', textContent: authorLine(book) }),
  );

// --- browsing ---------------------------------------------------------

function query() {
  const p = new URLSearchParams({ limit: PAGE, offset: state.offset });
  const filters = {
    q: els.search.value.trim(),
    series_id: els.series.value,
    author_id: els.author.value,
    language: els.language.value,
    format: els.format.value,
    sort: els.sort.value,
  };
  for (const [k, v] of Object.entries(filters)) if (v) p.set(k, v);
  return p;
}

async function loadPage() {
  if (state.loading || state.done) return;
  state.loading = true;

  const { total, items } = await getJSON(`/api/books?${query()}`);
  state.total = total;
  state.offset += items.length;
  state.done = state.offset >= total || items.length === 0;

  for (const book of items) els.grid.append(tile(book, (b) => open(b.id)));

  els.count.textContent = total
    ? `${total.toLocaleString()} book${total === 1 ? '' : 's'}`
    : '';
  els.empty.hidden = total > 0;
  state.loading = false;

  // A short page may not push the sentinel out of view; keep filling.
  if (!state.done && els.sentinel.getBoundingClientRect().top < window.innerHeight) {
    loadPage();
  }
}

function reload() {
  state.offset = 0;
  state.done = false;
  state.loading = false;
  els.grid.replaceChildren();
  loadPage();
}

// --- detail -----------------------------------------------------------

// epub descriptions carry publisher HTML. Keep the block structure, drop
// everything that could execute or restyle the page.
const ALLOWED = new Set(['P', 'BR', 'B', 'STRONG', 'I', 'EM', 'UL', 'OL', 'LI', 'H3', 'BLOCKQUOTE']);

function sanitize(html) {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const walk = (node) => {
    for (const child of [...node.children]) {
      walk(child);
      if (ALLOWED.has(child.tagName)) {
        for (const attr of [...child.attributes]) child.removeAttribute(attr.name);
      } else {
        child.replaceWith(...child.childNodes);
      }
    }
  };
  walk(doc.body);
  return doc.body.innerHTML;
}

function editionButton(edition, type) {
  const detail =
    type === 'epub'
      ? [filesize(edition.size)].filter(Boolean).join(' · ')
      : [duration(edition.duration_s), edition.narrators.length ? `read by ${edition.narrators.join(', ')}` : null, filesize(edition.size)]
          .filter(Boolean)
          .join(' · ');

  return el(
    'a',
    { className: 'dl', href: `/download/${type}/${edition.id}` },
    el('span', { textContent: type === 'epub' ? 'Download ebook' : 'Download audiobook' }),
    detail ? el('small', { textContent: detail }) : null,
  );
}

function renderDetail(book) {
  const art = el('div', { className: 'art' });
  if (book.cover) {
    art.append(el('img', { alt: '', src: `/covers/${book.cover.type}/${book.cover.id}` }));
  } else {
    art.append(fallback(book));
  }

  const close = el('button', { className: 'close', type: 'button', textContent: '×', title: 'Close' });
  close.onclick = closeDetail;

  const by = el('p', { className: 'by' });
  book.authors.forEach((name, i) => {
    if (i) by.append(', ');
    by.append(name);
  });
  if (!book.authors.length) by.textContent = 'Unknown author';

  const meta = el('div', { className: 'detail-meta' }, el('h1', { textContent: book.title }), by);

  if (book.series_name) {
    const link = el('a', { href: '#', textContent: seriesLabel(book) });
    link.onclick = (e) => {
      e.preventDefault();
      closeDetail();
      els.series.value = book.series_id;
      els.sort.value = 'series';
      reload();
    };
    meta.append(el('p', { className: 'series-line' }, link));
  }

  const facts = [
    year(book.publication_date),
    language(book.language),
    acquired(book.acquired_on),
  ].filter(Boolean);
  if (facts.length) meta.append(el('p', { className: 'facts', textContent: facts.join(' · ') }));

  const downloads = el('div', { className: 'downloads' });
  for (const e of book.epubs) downloads.append(editionButton(e, 'epub'));
  for (const m of book.m4bs) downloads.append(editionButton(m, 'm4b'));
  meta.append(downloads);

  const card = el('div', {}, close, el('div', { className: 'detail-top' },
    el('div', { className: 'detail-cover' }, art), meta));

  if (book.description) {
    const desc = el('div', { className: 'desc' });
    desc.innerHTML = sanitize(book.description);
    card.append(desc);
  }

  if (book.siblings.length > 1) {
    const strip = el('div', { className: 'strip' });
    for (const sib of book.siblings) {
      const t = tile(sib, (b) => open(b.id));
      if (sib.id === book.id) t.classList.add('current');
      strip.append(t);
    }
    card.append(
      el('div', { className: 'siblings' },
        el('h2', { textContent: book.series_name }), strip),
    );
  }

  els.card.replaceChildren(...card.childNodes);
  els.card.scrollTop = 0;
  els.detail.hidden = false;
  close.focus();

  // A book late in a long series would otherwise sit off the right edge. Scroll
  // the strip itself rather than using scrollIntoView, which on a short screen
  // also scrolls the card and pushes the title and cover out of view.
  const current = els.card.querySelector('.strip .current');
  if (current) {
    const strip = current.parentElement;
    strip.scrollLeft =
      current.offsetLeft - (strip.clientWidth - current.offsetWidth) / 2;
  }
}

async function open(id) {
  if (location.hash !== `#book/${id}`) history.pushState(null, '', `#book/${id}`);
  const res = await api(`/api/books/${id}`);
  if (res.ok) renderDetail(await res.json());
}

function closeDetail() {
  els.detail.hidden = true;
  els.card.replaceChildren();
  if (location.hash.startsWith('#book/')) history.pushState(null, '', location.pathname);
}

// --- wiring -----------------------------------------------------------

async function loadFilters() {
  const [series, authors, languages] = await Promise.all(
    ['/api/series', '/api/authors', '/api/languages'].map(getJSON),
  );
  for (const s of series) {
    els.series.append(el('option', { value: s.id, textContent: `${s.name} (${s.book_count})` }));
  }
  for (const a of authors) {
    els.author.append(el('option', { value: a.id, textContent: `${a.name} (${a.book_count})` }));
  }
  for (const l of languages) {
    els.language.append(el('option', { value: l.language, textContent: language(l.language) }));
  }
}

let debounce;
els.search.oninput = () => {
  clearTimeout(debounce);
  debounce = setTimeout(reload, 250);
};
for (const id of ['series', 'author', 'language', 'format', 'sort']) els[id].onchange = reload;

document.querySelector('.brand').onclick = (e) => {
  e.preventDefault();
  els.search.value = '';
  for (const id of ['series', 'author', 'language', 'format']) els[id].value = '';
  els.sort.value = 'title';
  reload();
};

document.querySelector('.detail-backdrop').onclick = closeDetail;
document.onkeydown = (e) => {
  if (e.key === 'Escape' && !els.detail.hidden) closeDetail();
};

// Back/forward between the grid and an open book.
window.onpopstate = () => {
  const m = location.hash.match(/^#book\/(\d+)$/);
  if (m) open(Number(m[1]));
  else if (!els.detail.hidden) { els.detail.hidden = true; els.card.replaceChildren(); }
};

new IntersectionObserver((entries) => {
  if (entries[0].isIntersecting) loadPage();
}, { rootMargin: '600px' }).observe(els.sentinel);

async function loadAccount() {
  const { name } = await getJSON('/api/me');
  els.whoami.textContent = name;
}

loadAccount();
loadFilters();
loadPage();
const deepLink = location.hash.match(/^#book\/(\d+)$/);
if (deepLink) open(Number(deepLink[1]));
