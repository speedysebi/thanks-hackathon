// "Fix this note": one small form for changing a saved entry, used on My notes and the dashboard.
// window.editEntry(entry, onSaved) opens it. PATCH /api/entries/{id} checks who may change what,
// scrubs personal details and refuses youth > people, so this form only has to be friendly.
(() => {
  // Keep in step with app/extract.py ACTIVITY_TYPES / OUTCOMES (eval/data_correctness_test.py checks).
  const TYPES = {
    crisis_response: 'Crisis response', violence_prevention: 'Violence prevention', youth_program: 'Youth program',
    food_meals: 'Food & meals', referral: 'Referral', outreach: 'Outreach', cleanup: 'Cleanup',
    pet_support: 'Pet support', donation_distribution: 'Donations & supplies', other: 'Other',
  };
  const OUTCOMES = { resolved: 'Resolved', in_progress: 'In progress', needs_follow_up: 'Needs follow-up', unknown: 'Not sure' };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const opts = (map, cur) => Object.entries(map).map(([k, v]) => `<option value="${k}"${k === cur ? ' selected' : ''}>${esc(v)}</option>`).join('');

  const css = document.createElement('style');
  css.textContent = `
    dialog.edit { border: 1px solid var(--line); border-radius: 14px; background: var(--card); color: var(--ink);
      padding: 18px; width: min(520px, calc(100vw - 32px)); max-height: calc(100dvh - 32px); overflow: auto; }
    dialog.edit::backdrop { background: rgb(0 0 0 / .45); }
    dialog.edit form { display: grid; gap: 10px; }
    dialog.edit label { display: grid; gap: 4px; font-size: 14px; color: var(--muted); }
    dialog.edit .pair { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    dialog.edit .raw { font-size: 14px; color: var(--muted); border-left: 3px solid var(--line); padding-left: 10px; margin: 0; }
    dialog.edit .row { display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap; margin-top: 4px; }
    dialog.edit .err { color: var(--accent); font-size: 14px; min-height: 1em; margin: 0; }`;
  document.head.append(css);

  window.editEntry = (e, onSaved) => {
    const d = document.createElement('dialog');
    d.className = 'edit';
    d.setAttribute('aria-labelledby', 'editTitle');
    d.innerHTML = `<form method="dialog" novalidate>
      <h2 id="editTitle" style="margin:0">Fix note #${Number(e.id)}</h2>
      ${e.raw_text ? `<p class="raw">“${esc(e.raw_text)}”</p>` : ''}
      <label>What happened (one line for the report)<input name="summary" maxlength="300" value="${esc(e.summary)}" required></label>
      <div class="pair">
        <label>Activity<select name="activity_type">${opts(TYPES, e.activity_type)}</select></label>
        <label>Day<input name="activity_date" type="date" value="${esc(e.activity_date)}" max="${new Date().toLocaleDateString('en-CA')}"></label>
      </div>
      <div class="pair">
        <label>People helped<input name="people_served" type="number" inputmode="numeric" min="0" max="5000" value="${Number(e.people_served) || 0}"></label>
        <label>Of them, youth<input name="youth_served" type="number" inputmode="numeric" min="0" max="5000" value="${Number(e.youth_served) || 0}"></label>
      </div>
      <div class="pair">
        <label>Neighborhood<input name="neighborhood" maxlength="80" value="${esc(e.neighborhood)}"></label>
        <label>Hours<input name="hours" type="number" inputmode="decimal" min="0" max="24" step="0.25" value="${Number(e.hours) || 0}"></label>
      </div>
      <label>Status<select name="outcome">${opts(OUTCOMES, e.outcome)}</select></label>
      <label>Follow-up (what still needs doing)<input name="follow_up" maxlength="300" value="${esc(e.follow_up)}"></label>
      <p class="err" role="alert"></p>
      <div class="row"><button type="button" class="ghost" value="cancel">Cancel</button><button type="submit">Save</button></div>
    </form>`;
    document.body.append(d);
    const f = d.querySelector('form'), err = d.querySelector('.err');
    d.querySelector('button[value=cancel]').onclick = () => d.close();
    d.addEventListener('close', () => d.remove());
    f.onsubmit = async (ev) => {
      ev.preventDefault();
      const v = Object.fromEntries(new FormData(f));
      const body = {
        summary: v.summary.trim(), activity_type: v.activity_type, outcome: v.outcome,
        people_served: Math.max(0, parseInt(v.people_served, 10) || 0), youth_served: Math.max(0, parseInt(v.youth_served, 10) || 0),
        neighborhood: v.neighborhood.trim(), follow_up: v.follow_up.trim(), hours: Math.max(0, parseFloat(v.hours) || 0),
      };
      if (v.activity_date) body.activity_date = v.activity_date;
      if (!body.summary) { err.textContent = 'Say in a few words what happened.'; return; }
      if (body.youth_served > body.people_served) { err.textContent = 'Youth can’t be more than people helped.'; return; }
      const save = f.querySelector('button[type=submit]');
      save.disabled = true; err.textContent = '';
      try {
        const r = await fetch('/api/entries/' + e.id, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          err.textContent = typeof j.detail === 'string' ? j.detail : 'That didn’t save (' + r.status + '). Check the numbers and try again.';
          save.disabled = false; return;
        }
        d.close();
        onSaved?.(await r.json());
      } catch {
        err.textContent = 'No connection. Try again when you have signal.'; save.disabled = false;
      }
    };
    d.showModal();
    f.querySelector('[name=summary]').focus();
  };
})();
