// Adding books, in two lists.
//
//   List A  files with no row yet — a work queue. The server owns everything in
//           it except the browser→server upload, so this page is mostly a view
//           of state it polls rather than state it holds.
//   List B  rows with no book — Add works out where one belongs, Remove forgets
//           it. Both are buttons; the row itself does nothing.
//
// The only thing this page really owns is an open proposal. Nothing is written
// until Accept, so cancelling is dropping a variable and reloading is the same
// thing by accident.

const els = {
  files: document.getElementById('files'),
  drop: document.getElementById('drop'),
  rescan: document.getElementById('rescan'),
  clear: document.getElementById('clear'),
  uploadWarning: document.getElementById('upload-warning'),
  listA: document.getElementById('list-a'),
  listB: document.getElementById('list-b'),
  aNote: document.getElementById('a-note'),
  bNote: document.getElementById('b-note'),
  msg: document.getElementById('msg'),
};

// Server-reported state, replaced wholesale on every poll.
let jobs = [];
let ready = [];

// Uploads in flight. Browser-owned, invisible to the server until they land,
// so they are merged into list A for display.
const uploads = new Map();
let nextUpload = 1;

// The one open proposal, or null. Never on the server.
let open = null;

// The row whose Remove button is waiting to be confirmed, and what went wrong
// the last time one was. Also never on the server: an unconfirmed removal, like
// an unaccepted proposal, has changed nothing.
let discarding = null;
let discardError = null;

const el = (tag, props = {}, ...children) => {
  const node = Object.assign(document.createElement(tag), props);
  for (const c of children) if (c != null) node.append(c);
  return node;
};

function say(text, bad = false) {
  els.msg.textContent = text;
  els.msg.classList.toggle('bad', bad);
  els.msg.hidden = !text;
}

// --- api --------------------------------------------------------------

async function api(path, options) {
  const res = await fetch(path, options);
  if (res.status === 401) {
    location.assign(`/login?next=${encodeURIComponent(location.pathname)}`);
    return new Promise(() => {});
  }
  return res;
}

async function problem(res) {
  try {
    const { detail } = await res.json();
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return 'The server rejected that request.';
  } catch {
    /* not JSON */
  }
  return `Something went wrong (${res.status}).`;
}

async function get(path) {
  const res = await api(path);
  if (!res.ok) throw new Error(await problem(res));
  return res.json();
}

async function post(path, body) {
  const res = await api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) throw new Error(await problem(res));
  return res.status === 204 ? null : res.json();
}

// Uploads go through XHR, not fetch: only XHR reports upload progress, and an
// audiobook is big enough that a silent minute looks like a hung page.
function upload(file, onProgress) {
  return new Promise((resolve, reject) => {
    const body = new FormData();
    body.append('file', file);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/admin/ingest/upload');
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let parsed = null;
      try {
        parsed = JSON.parse(xhr.responseText);
      } catch {
        /* not JSON */
      }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(parsed);
      if (xhr.status === 401) {
        location.assign(`/login?next=${encodeURIComponent(location.pathname)}`);
        return;
      }
      reject(new Error((parsed && parsed.detail) || `Upload failed (${xhr.status}).`));
    };
    xhr.onerror = () => reject(new Error('The upload did not reach the server.'));
    xhr.send(body);
  });
}

// --- list A -----------------------------------------------------------

const PHASES = {
  queued: 'Waiting',
  uploading: 'Uploading',
  downloading: 'Fetching from the bucket',
  storing: 'Putting it in the bucket',
  reading: 'Reading its metadata',
  done: 'Added',
  failed: 'Failed',
};

const WITH_BAR = new Set(['uploading', 'downloading', 'storing']);

function jobNode(job) {
  const head = el(
    'div',
    { className: 'item-head' },
    el('span', { className: 'item-label', textContent: job.label }),
    el('span', { className: `stage ${job.phase}`, textContent: PHASES[job.phase] || job.phase }),
  );
  if (WITH_BAR.has(job.phase)) {
    head.append(
      el('progress', { className: 'bar', value: job.percent ?? 0, max: 1 }),
      el('span', { className: 'pct', textContent: `${Math.round((job.percent ?? 0) * 100)}%` }),
    );
  }

  const node = el('div', { className: 'item' }, head);
  if (job.error) node.append(el('p', { className: 'item-error', textContent: job.error }));
  return node;
}

function renderA() {
  // Uploads first: they are the ones the person has to stay on the page for.
  const all = [...uploads.values(), ...jobs];
  els.listA.replaceChildren(...all.map(jobNode));

  els.uploadWarning.hidden = ![...uploads.values()].some((u) => u.phase === 'uploading');
  els.clear.hidden = !jobs.some((j) => j.phase === 'done' || j.phase === 'failed');
  els.aNote.textContent = all.length
    ? ''
    : 'Nothing waiting. Drop files above, or check the bucket for anything that arrived another way.';
}

async function addFile(file) {
  const id = `u${nextUpload++}`;
  uploads.set(id, { id, label: file.name, phase: 'uploading', percent: 0 });
  renderA();
  try {
    await upload(file, (p) => {
      uploads.get(id).percent = p;
      renderA();
    });
    // The server has the bytes and a job of its own now; drop ours so the two
    // do not both show. From here it survives this page being closed.
    uploads.delete(id);
    await poll();
  } catch (exc) {
    Object.assign(uploads.get(id), { phase: 'failed', error: exc.message, percent: null });
    renderA();
  }
}

// --- list B -----------------------------------------------------------

function readyNode(entry) {
  const key = `${entry.asset_type}:${entry.asset_id}`;
  const row = el(
    'div',
    { className: 'ready-row' },
    el('span', { className: 'item-label', textContent: entry.title || entry.s3_key }),
    el('span', { className: 'tag', textContent: entry.asset_type }),
  );

  if (open && open.key === key) {
    return el('div', { className: 'item open' }, row, cardNode(open));
  }

  // Named buttons rather than a clickable row: the two things you can do to a
  // file are opposite, and one of them costs a model call, which is not
  // something to start by clicking a title.
  const add = el('button', {
    className: 'ghost', type: 'button', textContent: 'Add',
    title: 'Work out where this belongs',
  });
  add.onclick = () => resolveAsset(entry);
  add.disabled = open != null;

  const remove = el('button', {
    className: 'ghost danger', type: 'button', textContent: 'Remove',
    title: 'Forget this file — deletes the record, not the file',
  });
  remove.onclick = () => {
    discarding = key;
    renderB();
  };
  remove.disabled = open != null;

  const item = el('div', { className: 'item ready-item' },
    el('div', { className: 'ready-line' }, row, add, remove));
  if (discarding === key) item.append(confirmNode(entry));
  return item;
}

// Two clicks rather than a confirm() box, because what has to be said does not
// fit in one: this removes a record and not a file, and the file being still in
// the bucket means the next check will read it straight back in.
function confirmNode(entry) {
  const go = el('button', { className: 'ghost danger', type: 'button', textContent: 'Remove record' });
  go.onclick = () => discardAsset(entry);

  const cancel = el('button', { className: 'ghost', type: 'button', textContent: 'Keep it' });
  cancel.onclick = () => {
    discarding = null;
    renderB();
  };

  return el(
    'div',
    { className: 'card' },
    el('p', { className: 'why', textContent:
      `Removes what was read from “${entry.s3_key}” — the object stays in the bucket, `
      + 'and the next bucket check will read it in again. Delete the object first if '
      + 'it should stay gone.' }),
    discardError ? el('p', { className: 'item-error', textContent: discardError }) : null,
    el('div', { className: 'card-actions' }, go, cancel),
  );
}

function renderB() {
  els.listB.replaceChildren(...ready.map(readyNode));
  els.bNote.textContent = ready.length
    ? `${ready.length} file(s) read and waiting to be added.`
    : 'Nothing waiting.';
}

// --- the proposal card ------------------------------------------------

const confidenceText = (card) =>
  card.confidence == null ? 'confidence ?' : `confidence ${card.confidence.toFixed(2)}`;

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

function cardNode(state) {
  const { card } = state;
  // The file's own half of the card is two queries away; the model's half takes
  // seconds. `side` is whichever of them has arrived -- the card repeats the
  // fields the meta call returned, so the left column never changes under the
  // reviewer when the proposal lands beside it.
  const side = card || state.meta || { raw_rows: [], cover: null };
  const acquired = el('input', {
    type: 'date', className: 'acquired', value: (card && card.acquired_on) || '',
  });
  // Reads as one more proposed field, because that is what it is -- the only
  // difference being that this one is the reviewer's to type rather than the
  // model's to guess.
  const acquiredRow = el(
    'div',
    { className: 'row' },
    el('span', { className: 'row-label', textContent: 'Acquired' }),
    el('span', { className: 'row-value' }, acquired),
  );

  // Until the proposal lands the right-hand column is the wait itself: the
  // spinner sits where the answer will be, so the shape of the card is the same
  // before and after.
  const proposedCol = card
    ? [...card.proposal_rows.map(rowNode), acquiredRow]
    : [
        el('p', { className: state.error ? 'item-error' : 'thinking' },
          state.error ? null : el('span', { className: 'spinner' }),
          el('span', { textContent: state.error || 'Working out where this belongs…' })),
      ];

  const instruction = el('input', {
    type: 'text',
    className: 'instruction',
    placeholder: 'What should change? e.g. “the series is Pern, position 11”',
    hidden: true,
  });
  instruction.onkeydown = (e) => {
    if (e.key === 'Enter') revise(state, instruction.value.trim(), acquired.value);
  };

  // Present but dead while the model is thinking, so the buttons do not appear
  // from nowhere under a cursor that is already there. Cancel always works:
  // waiting for a proposal is not a commitment to one.
  const accept = el('button', {
    className: 'primary', type: 'button', textContent: 'Accept', disabled: !card,
  });
  accept.onclick = () => acceptProposal(state, acquired.value);

  const change = el('button', {
    className: 'ghost', type: 'button', textContent: 'Request changes', disabled: !card,
  });
  change.onclick = () => {
    instruction.hidden = !instruction.hidden;
    if (!instruction.hidden) instruction.focus();
  };

  const cancel = el('button', { className: 'ghost', type: 'button', textContent: 'Cancel' });
  cancel.onclick = () => {
    // Nothing was written, so there is nothing to undo.
    open = null;
    renderB();
  };

  return el(
    'div',
    { className: 'card' },
    el(
      'div',
      { className: 'card-cols' },
      el('div', { className: 'col' },
        el('h3', { textContent: 'In the file' }), ...side.raw_rows.map(rowNode)),
      el('div', { className: 'col' },
        el('h3', { textContent: 'Proposed' }), ...proposedCol),
      side.cover
        ? el('div', { className: 'col-thumb' },
            el('img', { className: 'thumb', alt: '',
                        src: `/covers/${side.cover.type}/${side.cover.id}` }))
        : null,
    ),
    card ? el('p', { className: 'why', textContent: `${confidenceText(card)} · ${card.reason}` }) : null,
    card && card.notes ? el('p', { className: 'notes', textContent: card.notes }) : null,
    card && state.error ? el('p', { className: 'item-error', textContent: state.error }) : null,
    el('div', { className: 'card-actions' }, accept, change, cancel),
    instruction,
  );
}

async function resolveAsset(entry) {
  discarding = null;  // asking to add a row answers the question about removing one
  discardError = null;
  const state = {
    key: `${entry.asset_type}:${entry.asset_id}`, entry, meta: null, card: null, error: null,
  };
  open = state;
  renderB();

  // Both at once, and each renders as it arrives. The card is open from the
  // click; the file's own metadata fills the left of it almost immediately;
  // the model's proposal replaces the spinner on the right whenever it is
  // ready. `open === state` throughout because Cancel or a second Add replaces
  // it, and a reply to a request nobody is waiting for must not redraw.
  get(`/api/admin/ingest/meta/${entry.asset_type}/${entry.asset_id}`)
    .then((meta) => {
      if (open !== state || state.card) return;
      state.meta = meta;
      renderB();
    })
    .catch(() => {});  // the resolve call is about to report the same problem

  try {
    const card = await post('/api/admin/ingest/resolve', {
      asset_type: entry.asset_type,
      asset_id: entry.asset_id,
    });
    if (open !== state) return;
    state.card = card;
  } catch (exc) {
    if (open !== state) return;
    state.error = exc.message;
  }
  renderB();
}

async function discardAsset(entry) {
  try {
    await post('/api/admin/ingest/discard', {
      asset_type: entry.asset_type,
      asset_id: entry.asset_id,
    });
    discarding = null;
    discardError = null;
    await poll();
    say(`Removed the record of “${entry.s3_key}”. The file itself is untouched.`);
  } catch (exc) {
    discardError = exc.message;
    renderB();
  }
}

async function revise(state, instruction, acquiredOn) {
  if (!instruction) return;
  const previous = state.card;
  // A revision is the same wait as the first proposal, so it looks the same:
  // the file's column stays, the proposal's turns back into a spinner. The old
  // card stands in for the meta call, carrying the same fields.
  state.meta = state.meta || previous;
  state.card = null;
  state.error = null;
  renderB();
  try {
    state.card = await post('/api/admin/ingest/revise', {
      asset_type: state.entry.asset_type,
      asset_id: state.entry.asset_id,
      proposal: previous.proposal,
      instruction,
      acquired_on: acquiredOn || null,
    });
  } catch (exc) {
    state.card = previous;
    state.error = exc.message;
  }
  renderB();
}

async function acceptProposal(state, acquiredOn) {
  const title = state.card.raw_rows.find((r) => r.label === 'Title')?.text || 'It';
  try {
    const { book_id } = await post('/api/admin/ingest/accept', {
      asset_type: state.entry.asset_type,
      asset_id: state.entry.asset_id,
      proposal: state.card.proposal,
      acquired_on: acquiredOn || null,
    });
    open = null;
    await poll();
    els.msg.hidden = false;
    els.msg.classList.remove('bad');
    els.msg.replaceChildren(
      `${title} is in the library. `,
      el('a', { href: `/#book/${book_id}`, textContent: 'Open it' }),
    );
  } catch (exc) {
    state.error = exc.message;
    renderB();
  }
}

// --- polling ----------------------------------------------------------

let timer = null;

async function poll() {
  const res = await api('/api/admin/ingest/state');
  if (!res.ok) {
    say(await problem(res), true);
    return;
  }
  const state = await res.json();
  jobs = state.list_a;
  ready = state.list_b;
  // A confirmation belongs to a row; if the row is gone -- removed here, or
  // added to a book in another tab -- the question it was asking is moot.
  if (discarding && !ready.some((e) => `${e.asset_type}:${e.asset_id}` === discarding)) {
    discarding = null;
    discardError = null;
  }
  renderA();
  renderB();

  // Only while something is moving. An idle page should be idle.
  clearTimeout(timer);
  if (state.busy || uploads.size) timer = setTimeout(poll, 1000);
}

async function rescan() {
  els.rescan.disabled = true;
  els.rescan.textContent = 'Checking…';
  try {
    const { queued } = await post('/api/admin/ingest/scan');
    if (queued) say(`Found ${queued} file(s) in the bucket with no record yet.`);
  } catch (exc) {
    say(exc.message, true);
  } finally {
    els.rescan.disabled = false;
    els.rescan.textContent = 'Check the bucket';
    poll();
  }
}

// --- wiring -----------------------------------------------------------

els.files.onchange = () => {
  for (const file of els.files.files) addFile(file);
  els.files.value = '';
};

els.rescan.onclick = rescan;
els.clear.onclick = async () => {
  await post('/api/admin/ingest/clear-finished');
  poll();
};

for (const name of ['dragenter', 'dragover']) {
  els.drop.addEventListener(name, (e) => {
    e.preventDefault();
    els.drop.classList.add('over');
  });
}
for (const name of ['dragleave', 'drop']) {
  els.drop.addEventListener(name, (e) => {
    e.preventDefault();
    els.drop.classList.remove('over');
  });
}
els.drop.addEventListener('drop', (e) => {
  for (const file of e.dataTransfer.files) addFile(file);
});

// Losing an upload is losing real work; losing an open proposal costs a click.
window.addEventListener('beforeunload', (e) => {
  if ([...uploads.values()].some((u) => u.phase === 'uploading')) e.preventDefault();
});

// Opening the page draws a line under whatever the worker finished while nobody
// was here: those files are rows now, and rows are list B's business. Done
// before the first poll, or the page paints the very thing it is about to drop.
// Failures survive it -- see forget_done() -- and are cleared by hand.
(async () => {
  try {
    await post('/api/admin/ingest/forget-done');
  } catch {
    /* a stale job on the page is not a reason to open it empty */
  }
  poll();    // what is here now
  rescan();  // then look in the bucket, which takes seconds
})();
