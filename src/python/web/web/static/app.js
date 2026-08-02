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
  guests: document.getElementById('guests'),
  addBooks: document.getElementById('add-books'),
};

// `generation` counts reloads. Typing a search fires one per keystroke-pause,
// and each abandons whatever the last one asked for -- so a request has to be
// able to tell, when it finally lands, whether anyone still wants its answer.
const state = { offset: 0, total: 0, loading: false, done: false, generation: 0 };

// --- api --------------------------------------------------------------

// A session that expired while the tab sat open turns every request into a 401.
// Bounce to the login page rather than letting the UI quietly render nothing, and
// remember where we were so signing back in returns us here.
async function api(path, options) {
  const res = await fetch(path, options);
  if (res.status === 401) {
    const here = location.pathname + location.search + location.hash;
    location.assign(`/login?next=${encodeURIComponent(here)}`);
    return new Promise(() => {}); // never settles; the navigation is already underway
  }
  return res;
}

const getJSON = async (path) => (await api(path)).json();

const post = (path, body) =>
  api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

// Set from /api/me. Courtesy only, exactly like the header links: every route
// behind this checks it server-side.
let isAdmin = false;

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
      // The thumbnail, not the original: a tile is 140px wide and the originals
      // run to 1200x1920. The detail card still shows the full image.
      src: `/covers/${book.cover.type}/thumb/${book.cover.id}`,
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
  const generation = state.generation;

  const { total, items } = await getJSON(`/api/books?${query()}`);

  // A reload happened while this was in flight. Its results answer a question
  // nobody is asking any more, and the grid it would append them to has been
  // emptied and refilled by someone else -- so appending now shows the same
  // book twice under a count that says one. Drop them, and do not touch
  // state: the newer load owns it.
  if (generation !== state.generation) return;

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
  state.generation++;
  state.offset = 0;
  state.done = false;
  // Deliberately cleared even though a request may still be running: a reload
  // must be able to start one immediately. What keeps that safe is the
  // generation check in loadPage, not this flag.
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

// Admin-only, one per file. A date and nothing else, so there is nothing to
// review and nothing to accept -- it saves when you change it. Per file rather
// than per book because that is how it is stored: the card's "Acquired" line is
// the earliest of a book's files, and for about one book in nine the ebook and
// the audiobook carry genuinely different dates.
function acquiredField(edition, type, book) {
  const input = el('input', {
    type: 'date', className: 'acq-input', value: edition.acquired_on || '',
  });
  const note = el('span', { className: 'acq-note' });

  input.onchange = async () => {
    if (!input.value) return; // cleared: there is no "unknown" to write
    input.disabled = true;
    note.textContent = 'Saving…';
    note.classList.remove('bad');
    try {
      const res = await api(`/api/admin/assets/${type}/${edition.id}/acquired-on`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ acquired_on: input.value }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        note.textContent = err.detail || 'Did not save.';
        note.classList.add('bad');
        return;
      }
      note.textContent = 'Saved';
      // The book's own Acquired line is the minimum across its files, so it can
      // move when one file's date does. Refresh both it and the grid tile.
      reload();
      const fresh = await getJSON(`/api/books/${book.id}`);
      const line = els.card.querySelector('.facts');
      if (line && fresh) {
        line.textContent = [year(fresh.publication_date), language(fresh.language),
                            acquired(fresh.acquired_on)].filter(Boolean).join(' · ');
      }
    } catch {
      note.textContent = 'Could not reach the server.';
      note.classList.add('bad');
    } finally {
      input.disabled = false;
    }
  };

  return el('label', { className: 'acq' },
    el('span', { className: 'acq-label', textContent: type === 'epub' ? 'Ebook' : 'Audiobook' }),
    input, note);
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

// --- corrections (admin) ----------------------------------------------
//
// Same bargain as the review card on the ingest page: say what is wrong in
// plain language, read what that would do to the record, and nothing is written
// until Accept. Held entirely in this closure, so closing the card or pressing
// Escape abandons it -- which costs nothing, because nothing was stored.

const rowNode = (row) =>
  el(
    'div',
    { className: 'row' },
    el('span', { className: 'row-label', textContent: row.label }),
    el(
      'span',
      { className: 'row-value' },
      row.verb ? el('span', { className: `verb ${row.verb}`, textContent: row.verb }) : null,
      el('span', { textContent: row.text }),
      row.warning ? el('span', { className: 'warn', textContent: `⚠ ${row.warning}` }) : null,
    ),
  );

function correctionPanel(book) {
  const wrap = el('div', { className: 'correct' });

  const opener = el('button', {
    className: 'ghost correct-open', type: 'button',
    textContent: 'Something’s wrong…',
    title: 'Tell the catalog what it got wrong about this book',
  });
  const collapse = () => wrap.replaceChildren(opener);
  collapse();

  opener.onclick = () => {
    const box = el('textarea', {
      className: 'correct-input', rows: 2,
      placeholder: 'e.g. the publication date is wrong — or: you got the author wrong, it’s Ursula K. Le Guin',
    });
    const ask = el('button', { className: 'primary', type: 'button', textContent: 'Ask' });
    const cancel = el('button', { className: 'ghost', type: 'button', textContent: 'Cancel' });
    const msg = el('p', { className: 'correct-msg', hidden: true });
    const out = el('div', {});

    const say = (text, bad = false) => {
      msg.textContent = text || '';
      msg.hidden = !text;
      msg.classList.toggle('bad', bad);
    };

    cancel.onclick = collapse;

    ask.onclick = async () => {
      const instruction = box.value.trim();
      if (!instruction) return box.focus();
      ask.disabled = true;
      out.replaceChildren();
      say('Working out what that means…');
      try {
        const res = await post(`/api/admin/books/${book.id}/correction`, { instruction });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          say(body.detail || 'That did not work.', true);
          return;
        }
        say(body.notes);
        out.append(proposedNode(book, body, collapse, say));
      } catch {
        say('The server could not be reached.', true);
      } finally {
        ask.disabled = false;
      }
    };

    wrap.replaceChildren(
      el('div', { className: 'correct-form' },
        box,
        el('div', { className: 'correct-actions' }, ask, cancel),
        msg,
        out),
    );
    box.focus();
  };

  return wrap;
}

// What the correction would do, and the only button here that writes.
function proposedNode(book, body, collapse, say) {
  const accept = el('button', { className: 'primary', type: 'button', textContent: 'Accept' });
  const discard = el('button', { className: 'ghost', type: 'button', textContent: 'Discard' });

  accept.onclick = async () => {
    accept.disabled = true;
    discard.disabled = true;
    try {
      const res = await post(`/api/admin/books/${book.id}/correction/accept`, {
        correction: body.correction,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        say(err.detail || 'That could not be applied.', true);
        accept.disabled = false;
        discard.disabled = false;
        return;
      }
      // Re-read the book rather than patching the card: the correction may have
      // moved it into a series, which changes the sibling strip too. Reload the
      // grid behind it for the same reason -- its tile is now out of date.
      await open(book.id);
      reload();
    } catch {
      say('The server could not be reached.', true);
      accept.disabled = false;
      discard.disabled = false;
    }
  };
  discard.onclick = collapse;

  const confidence =
    body.confidence == null ? '' : `The model's confidence: ${body.confidence.toFixed(2)}`;

  return el('div', { className: 'correct-proposal' },
    el('h3', { textContent: 'This would change' }),
    ...body.rows.map(rowNode),
    confidence ? el('p', { className: 'correct-confidence', textContent: confidence }) : null,
    el('div', { className: 'correct-actions' }, accept, discard));
}

function renderDetail(book) {
  const art = el('div', { className: 'art' });
  if (book.cover) {
    // Also the thumbnail: .detail-cover is 180px wide (132 on a phone), so the
    // 400px copy is oversized for it already -- and it is the file the grid has
    // just cached, which is why the card's cover appears the moment it opens.
    art.append(el('img', { alt: '', src: `/covers/${book.cover.type}/thumb/${book.cover.id}` }));
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

  // Under the files they belong to, because the question they answer is "when
  // did I get *this* one?" -- which is the question the facts line above,
  // showing only the earliest, cannot answer.
  if (isAdmin && (book.epubs.length || book.m4bs.length)) {
    meta.append(el('div', { className: 'acquired' },
      el('span', { className: 'acquired-head', textContent: 'Acquired' }),
      ...book.epubs.map((e) => acquiredField(e, 'epub', book)),
      ...book.m4bs.map((m) => acquiredField(m, 'm4b', book))));
  }

  const card = el('div', {}, close, el('div', { className: 'detail-top' },
    el('div', { className: 'detail-cover' }, art), meta));

  if (book.description) {
    const desc = el('div', { className: 'desc' });
    desc.innerHTML = sanitize(book.description);
    card.append(desc);
  }

  // Last, and below the description: it is the rarest thing anyone does here,
  // and it should not sit between a reader and the book.
  if (isAdmin) card.append(correctionPanel(book));

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
  els.sort.value = 'acquired';
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
  const { name, is_admin } = await getJSON('/api/me');
  els.whoami.textContent = name;
  isAdmin = is_admin;
  // Courtesy, not security: /admin, /ingest and every route behind them check
  // this server-side. Hiding the links just keeps them out of the way of people
  // they would only refuse.
  els.guests.hidden = !is_admin;
  els.addBooks.hidden = !is_admin;
}

const account = loadAccount();
loadFilters();
loadPage();
// Deep link waits on the account: whether the card gets its admin controls is
// decided while it renders, and rendering first would silently omit them.
const deepLink = location.hash.match(/^#book\/(\d+)$/);
if (deepLink) account.then(() => open(Number(deepLink[1])));
