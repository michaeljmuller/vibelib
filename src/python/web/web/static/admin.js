const els = {
  form: document.getElementById('add'),
  email: document.getElementById('email'),
  name: document.getElementById('name'),
  rows: document.getElementById('rows'),
  msg: document.getElementById('msg'),
};

let me = null;

const el = (tag, props = {}, ...children) => {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children.filter(Boolean));
  return node;
};

const date = (s) => (s ? s.slice(0, 10) : '—');

function say(text, bad = false) {
  els.msg.textContent = text;
  els.msg.classList.toggle('bad', bad);
  els.msg.hidden = false;
}

// The server is the authority on every rule enforced here -- it rejects these same
// cases with a 409. Hiding the button is courtesy, not security: it keeps people
// from clicking something that was only ever going to fail.
function removable(user) {
  if (user.id === me.id) return null;        // no removing yourself
  if (user.is_admin) return null;            // admins are demoted from the shell
  return el('button', {
    className: 'revoke',
    textContent: 'Remove',
    onclick: () => remove(user),
  });
}

function row(user) {
  return el(
    'tr',
    {},
    el('td', {}, user.email, user.is_admin ? el('span', { className: 'tag', textContent: 'admin' }) : null),
    el('td', { textContent: user.name || '—' }),
    el('td', { className: 'dim', textContent: date(user.added_on) }),
    el('td', { className: 'dim', textContent: user.last_login_at ? date(user.last_login_at) : 'never' }),
    el('td', {}, removable(user)),
  );
}

async function load() {
  const users = await (await fetch('/api/users')).json();
  els.rows.replaceChildren(...users.map(row));
}

async function remove(user) {
  if (!confirm(`Remove ${user.email}?\n\nThey lose access immediately.`)) return;
  const res = await fetch(`/api/users/${user.id}`, { method: 'DELETE' });
  if (res.ok) {
    say(`Removed ${user.email}.`);
    load();
  } else {
    say((await res.json()).detail || 'Could not remove.', true);
  }
}

els.form.onsubmit = async (e) => {
  e.preventDefault();
  const email = els.email.value.trim();
  const res = await fetch('/api/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, name: els.name.value.trim() || null }),
  });
  if (res.ok) {
    say(`${email} can now sign in with Google.`);
    els.form.reset();
    load();
  } else {
    const body = await res.json();
    // FastAPI reports a bad email as a validation error, whose detail is a list.
    const detail = Array.isArray(body.detail) ? 'That does not look like an email address.' : body.detail;
    say(detail || 'Could not add.', true);
  }
};

(async () => {
  me = await (await fetch('/api/me')).json();
  load();
})();
