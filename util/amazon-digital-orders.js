// Save Amazon digital orders (Kindle and Audible) as one text file, for
// correcting acquisition dates.
//
// To use it:
//   1. Sign in to amazon.com in Safari or Chrome. Open Your Orders, then the
//      Digital Orders tab, and choose a time filter (one year at a time).
//   2. Open the JavaScript console (Safari: Cmd+Option+C; Chrome: Cmd+Option+J,
//      and type "allow pasting" if it asks).
//   3. Paste this whole file and press Enter.
//
// The script only reads. It loads each page of the list in a hidden frame,
// because Amazon encrypts the order cards and decrypts them in the browser;
// the raw HTML has no titles or dates. When the last page is done, the browser
// downloads amazon-digital-orders-<filter>.txt, one order card per block,
// blocks separated by "=====". Subscription charges are left out.
//
// The console still shows many errors and warnings. They come from Amazon's own
// ad and logging scripts and from browser extensions, not from this script, and
// they do not affect the result. To see only this script's lines, type
// "[orders]" in the console's filter box.

(async () => {
  const PAGE_SIZE = 10;           // orders per page on Amazon's list
  const PAUSE_MS = 1500;          // wait between pages, to be gentle
  const DECRYPT_WAIT_MS = 15000;  // longest wait for a page's cards to decrypt

  const log = (...args) => console.log('[orders]', ...args);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const url = new URL(location.href);
  const filter = url.searchParams.get('timeFilter') || 'current';

  const frame = document.createElement('iframe');
  frame.style.cssText = 'position:fixed;left:-5000px;top:0;width:1200px;height:900px';
  document.body.appendChild(frame);

  const seen = new Set();
  const blocks = [];
  let skipped = 0;

  try {
    for (let page = 0; ; page++) {
      url.searchParams.set('startIndex', page * PAGE_SIZE);
      await new Promise((r) => { frame.onload = r; frame.src = url.href; });

      // Wait until every card on the page shows a year, which means it has
      // been decrypted.
      let cards = [];
      for (let waited = 0; waited < DECRYPT_WAIT_MS; waited += 500) {
        await sleep(500);
        cards = [...frame.contentDocument.querySelectorAll('.js-order-card')];
        if (cards.length && cards.every((c) => /\b20\d\d\b/.test(c.innerText))) break;
      }

      let fresh = 0;
      let added = 0;
      for (const card of cards) {
        const id = (card.getAttribute('data-csa-c-slot-id') || '').split('.').pop();
        if (!id || seen.has(id)) continue;
        seen.add(id);
        fresh++;
        const text = card.innerText.replace(/\n\s*\n+/g, '\n').trim();
        if (!text.includes('ORDER PLACED')) { skipped++; continue; } // subscription charge
        if (!/\b20\d\d\b/.test(text)) log(`order ${id} did not decrypt; its block will be unreadable`);
        blocks.push(text);
        added++;
      }
      // Past the last page Amazon shows no cards, or repeats ones already seen.
      if (!fresh) break;
      log(`page ${page + 1}: ${added} orders`);
      if (cards.length < PAGE_SIZE) break;

      // Unload the page before pausing, so its ad and tracking scripts stop.
      frame.src = 'about:blank';
      await sleep(PAUSE_MS);
    }
  } finally {
    frame.remove();
  }

  const blob = new Blob([blocks.join('\n\n=====\n\n')], { type: 'application/octet-stream' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `amazon-digital-orders-${filter}.txt`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  log(`done: ${blocks.length} orders saved, ${skipped} subscription charges left out`);
})();
