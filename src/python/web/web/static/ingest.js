// Adding books, in two lists.
//
//   List A  files with no row yet — a work queue. The server owns everything in
//           it except the browser→server upload, so this page is mostly a view
//           of state it polls rather than state it holds.
//   List B  rows with no book — click one to work out where it belongs.
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
    return el('div', { className: 'item open' }, row, open.card ? cardNode(open) : busyNode(open));
  }

  const choose = el('button', { className: 'ready', type: 'button' }, row);
  choose.onclick = () => resolveAsset(entry);
  choose.disabled = open != null;

  const remove = el('button', {
    className: 'ghost danger remove', type: 'button', textContent: 'Remove',
    title: 'Forget this file — deletes the record, not the file',
  });
  remove.onclick = () => {
    discarding = key;
    renderB();
  };
  remove.disabled = open != null;

  const item = el('div', { className: 'item ready-item' },
    el('div', { className: 'ready-line' }, choose, remove));
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

const busyNode = (state) =>
  el('div', { className: 'card' },
    el('p', { className: state.error ? 'item-error' : 'dim',
              textContent: state.error || 'Working out where this belongs…' }));

function renderB() {
  els.listB.replaceChildren(...ready.map(readyNode));
  els.bNote.textContent = ready.length
    ? `${ready.length} file(s) read and waiting to be added.`
    : 'Nothing waiting.';
}

// --- the proposal card ------------------------------------------------

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
  const confidence =
    card.confidence == null ? 'confidence ?' : `confidence ${card.confidence.toFixed(2)}`;

  const acquired = el('input', {
    type: 'date', className: 'acquired', value: card.acquired_on || '',
  });

  const instruction = el('input', {
    type: 'text',
    className: 'instruction',
    placeholder: 'What should change? e.g. “the series is Pern, position 11”',
    hidden: true,
  });
  instruction.onkeydown = (e) => {
    if (e.key === 'Enter') revise(state, instruction.value.trim());
  };

  const accept = el('button', { className: 'primary', type: 'button', textContent: 'Accept' });
  accept.onclick = () => acceptProposal(state, acquired.value);

  const change = el('button', { className: 'ghost', type: 'button', textContent: 'Request changes' });
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
        el('h3', { textContent: 'In the file' }), ...card.raw_rows.map(rowNode)),
      el('div', { className: 'col' },
        el('h3', { textContent: 'Proposed' }), ...card.proposal_rows.map(rowNode)),
      card.cover
        ? el('div', { className: 'col-thumb' },
            el('img', { className: 'thumb', alt: '',
                        src: `/covers/${card.cover.type}/${card.cover.id}` }))
        : null,
    ),
    el('p', { className: 'why', textContent: `${confidence} · ${card.reason}` }),
    card.notes ? el('p', { className: 'notes', textContent: card.notes }) : null,
    state.error ? el('p', { className: 'item-error', textContent: state.error }) : null,
    el('div', { className: 'card-actions' }, accept, change, cancel,
      el('label', { className: 'acquired-label' }, 'Acquired', acquired)),
    instruction,
  );
}

async function resolveAsset(entry) {
  discarding = null;  // asking to add a row answers the question about removing one
  discardError = null;
  open = { key: `${entry.asset_type}:${entry.asset_id}`, entry, card: null, error: null };
  renderB();
  try {
    open.card = await post('/api/admin/ingest/resolve', {
      asset_type: entry.asset_type,
      asset_id: entry.asset_id,
    });
  } catch (exc) {
    open.error = exc.message;
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

async function revise(state, instruction) {
  if (!instruction) return;
  const previous = state.card;
  state.card = null;
  state.error = null;
  renderB();
  try {
    state.card = await post('/api/admin/ingest/revise', {
      asset_type: state.entry.asset_type,
      asset_id: state.entry.asset_id,
      proposal: previous.proposal,
      instruction,
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

rescan();
poll();
