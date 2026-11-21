// Top bar for every page. Everyone sees Log + Text it; signed-in people also get My notes; admins get the admin side.
// Pages that need to know who's here listen for the 'me' event (or read window.IMPACT_ME).
(function () {
  const nav = document.querySelector('nav[data-nav]');
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const here = location.pathname;
  const ADMIN = [['/dashboard', 'Dashboard'], ['/report', 'Report'], ['/connect', 'Connect'], ['/team', 'Team'], ['/console', 'Console']];

  function render(me) {
    if (!nav) return;
    const tabs = [['/', 'Log'], ['/phone', 'Text it']];
    if (me.signed_in && me.name !== 'Admin') tabs.push(['/me', 'My notes']);
    if (me.admin) tabs.push(...ADMIN);
    const link = ([href, label]) => `<a class="tab" href="${href}"${href === here ? ' aria-current="page"' : ''}>${label}</a>`;
    const who = me.name === 'Admin' ? '' : esc(me.name);  // the built-in admin has no person's name to show
    // name links to two-step sign-in settings; an admin who hasn't set it up yet gets a nudge instead
    const badge = me.admin ? '<span class="pill admin">Admin</span> '
      : me.needs_2fa ? '<a class="pill warn" href="/security">Finish admin setup</a> ' : '';
    const account = me.signed_in
      ? `<a class="who small" href="/security" title="Two-step sign-in">${badge}${who}${me.mfa ? ' 🔒' : ''}</a><button type="button" class="tab linkish" id="signout">Sign out</button>`
      : `<a class="tab signin" href="/login?next=${encodeURIComponent(here)}">Sign in</a>`;
    // brand + account on top; tabs in their own row that scrolls sideways on a phone if they don't fit
    nav.innerHTML = `<a class="brand" href="/">ImpactText</a><span class="account">${account}</span><div class="tabs">${tabs.map(link).join('')}</div>`;
    nav.classList.toggle('many', tabs.length > 3);
    const cur = nav.querySelector('[aria-current="page"]');
    if (cur && nav.classList.contains('many')) cur.scrollIntoView({ block: 'nearest', inline: 'center' });
    const out = document.getElementById('signout');
    if (out) out.onclick = async () => {
      await fetch('/api/auth/logout', { method: 'POST' }).catch(() => {});
      try { localStorage.removeItem('worker'); } catch {}
      location.href = '/';
    };
  }

  render({ signed_in: false });  // instant, then corrected once we know who this is
  window.IMPACT_ME_READY = fetch('/api/auth/me', { cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : { signed_in: false }))
    .catch(() => ({ signed_in: false, offline: true }))
    .then((me) => {
      window.IMPACT_ME = me;
      render(me);
      document.dispatchEvent(new CustomEvent('me', { detail: me }));
      return me;
    });
})();
