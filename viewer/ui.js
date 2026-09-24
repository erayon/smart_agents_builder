/* Rendering + interaction. Depends on app.js. */

function render() {
  const m = S.model;
  $('#empty').hidden = true;
  $('#main').hidden = false;
  $('#title').textContent = m.title + (m.version ? ` v${m.version}` : '');

  const c = m.counts;
  $('#stats').innerHTML = [
    ['entities', c.entities], ['states', c.states], ['operations', c.operations],
    ['events', c.events], ['decision points', c.decisions],
    ['creations', c.creations], ['actors', c.actors],
    ['systems', c.systems], ['human-in-loop', c.hitl], ['high risk', c.highRisk],
  ].map(([k, v]) => `<div class="stat"><b>${v}</b><span>${k}</span></div>`).join('');

  const { errs, warns } = validate(m);
  const v = $('#validation');
  v.className = 'panel ' + (errs.length ? 'bad' : warns.length ? 'warn' : 'good');
  v.innerHTML = `<h2>${errs.length ? `✕ ${errs.length} error${errs.length > 1 ? 's' : ''}`
    : warns.length ? `▲ valid, ${warns.length} warning${warns.length > 1 ? 's' : ''}`
      : '✓ valid — reachable, deadlock-free, no dangling references'}</h2>`
    + (errs.length || warns.length
      ? `<ul>${[...errs.map(e => ['err', e]), ...warns.map(w => ['warn', w])]
        .map(([k, [code, msg]]) => `<li class="${k}"><code>${code}</code> ${esc(msg)}</li>`).join('')}</ul>`
      : '');

  $$('.tab').forEach(t => t.setAttribute('aria-selected', t.dataset.tab === S.tab));
  ({ lifecycles: tabLifecycles, operations: tabOperations, relations: tabRelations })[S.tab]();
}

function tabLifecycles() {
  const m = S.model;
  const lc = m.entities.filter(e => m.arr(e.hasState).length);
  if (!lc.length) return $('#view').innerHTML = `<p class="muted">No entity has a lifecycle.</p>`;
  if (!S.entity || !lc.find(e => e.id === S.entity)) S.entity = lc[0].id;
  const e = lc.find(x => x.id === S.entity);
  const name = local(e.id);
  const ops = m.opsByEntity[name] || [];

  $('#view').innerHTML = `
    <div class="chips">${lc.map(x => `<button class="chip${x.id === S.entity ? ' on' : ''}"
      data-entity="${esc(x.id)}">${esc(local(x.id))}</button>`).join('')}</div>
    <div class="card">
      <h3>${esc(name)} <span class="muted">${esc(e.description || '')}</span></h3>
      <div class="legend">
        <span><i class="sw init"></i>initial</span><span><i class="sw term"></i>terminal</span>
        <span><i class="sw dot"></i>decision point</span>
        <span><i class="sw line hitl"></i>human-in-loop</span><span><i class="sw line risk"></i>high risk</span>
      </div>
      <div class="scroll">${svgFor(m, e)}</div>
    </div>
    ${e.attributes ? `<div class="card"><h3>Attributes</h3><table><tbody>${
      Object.entries(e.attributes).map(([k, val]) =>
        `<tr><td><code>${esc(k)}</code></td><td class="muted">${esc(val)}</td></tr>`).join('')
    }</tbody></table></div>` : ''}
    ${m.arr(e.invariant).length ? `<div class="card"><h3>Invariants</h3><ul class="plain">${
      m.arr(e.invariant).map(i => `<li>${esc(i)}</li>`).join('')}</ul></div>` : ''}
    <div class="card"><h3>Operations (${ops.length})</h3>${opTable(m, ops)}</div>`;

  $$('[data-entity]').forEach(b => b.onclick = () => { S.entity = b.dataset.entity; render(); });
}

function badge(o) {
  return (o.requiresHuman ? `<span class="b hitl">human</span>` : '')
    + (o.risk ? `<span class="b r-${esc(o.risk)}">${esc(o.risk)}</span>` : '');
}

function opTable(m, ops) {
  if (!ops.length) return `<p class="muted">None.</p>`;
  return `<table><thead><tr><th>operation</th><th>transition</th><th>actor</th>
    <th>systems</th><th></th></tr></thead><tbody>${ops.map(o => `<tr>
    <td><b>${esc(o.name)}</b><div class="muted sm">${esc(o.description || '')}</div></td>
    <td class="sm"><code>${esc(local(o.from))}</code> → <code>${esc(local(o.to))}</code></td>
    <td class="sm">${esc(o.performedBy || '—')}</td>
    <td class="sm">${m.arr(o.usesSystem).map(s => `<code>${esc(s)}</code>`).join(' ') || '—'}</td>
    <td>${badge(o)}</td></tr>`).join('')}</tbody></table>`;
}

function tabOperations() {
  const m = S.model;
  const group = (key, fn) => {
    const g = {};
    m.ops.forEach(o => fn(o).forEach(k => (g[k] ||= []).push(o.name)));
    return Object.entries(g).sort((a, b) => b[1].length - a[1].length);
  };
  const actors = group('actor', o => [o.performedBy || 'unassigned']);
  const systems = group('system', o => m.arr(o.usesSystem));
  const decisions = m.states.filter(s => s.isDecisionPoint);

  $('#view').innerHTML = `
    <div class="card"><h3>Decision points (${decisions.length})</h3>
      <p class="muted sm">States with more than one outgoing operation — these become
      conditional edges in a LangGraph.</p>
      ${decisions.length ? `<ul class="plain">${decisions.map(s => {
        const outs = m.ops.filter(o => o.from === s.id);
        return `<li><b>${esc(local(s.id))}</b> → ${outs.map(o =>
          `<code>${esc(o.name)}</code>`).join(' | ')}</li>`;
      }).join('')}</ul>` : `<p class="muted">None.</p>`}</div>

    <div class="cols">
      <div class="card"><h3>Actors (${actors.length})</h3>${bars(actors)}</div>
      <div class="card"><h3>Systems / tools (${systems.length})</h3>${bars(systems)}</div>
    </div>
    <div class="card"><h3>All operations (${m.ops.length})</h3>${opTable(m, m.ops)}</div>`;
}

function bars(rows) {
  const max = Math.max(1, ...rows.map(r => r[1].length));
  return `<ul class="bars">${rows.map(([k, v]) => `<li>
    <span class="k">${esc(k)}</span>
    <span class="bar"><i style="width:${(v.length / max) * 100}%"></i></span>
    <span class="n">${v.length}</span>
    <div class="muted sm">${v.map(esc).join(', ')}</div></li>`).join('')}</ul>`;
}

function tabRelations() {
  const m = S.model;
  const rows = [];
  m.entities.forEach(e => {
    const rels = m.arr(e.rels);
    if (rels.length) rels.forEach(r => rows.push([local(e.id), r.target, r.cardinality, r.label]));
    else m.arr(e.relatesTo).forEach(t => rows.push([local(e.id), local(t), '—', 'relatesTo']));
  });
  $('#view').innerHTML = `
    <div class="card"><h3>Entities (${m.entities.length})</h3>
      <div class="grid">${m.entities.map(e => `<div class="ent">
        <b>${esc(local(e.id))}</b>
        <div class="muted sm">${esc(e.description || '')}</div>
        <div class="sm">${m.arr(e.hasState).length
          ? `<span class="b">${m.arr(e.hasState).length} states</span>` : `<span class="b muted">no lifecycle</span>`}
          ${e.identityKey ? `<code>${esc(e.identityKey)}</code>` : ''}</div>
      </div>`).join('')}</div></div>
    <div class="card"><h3>Relations (${rows.length})</h3>
      ${rows.length ? `<table><thead><tr><th>from</th><th>cardinality</th><th>to</th><th>label</th></tr></thead>
      <tbody>${rows.map(([a, b, c, l]) => `<tr><td><b>${esc(a)}</b></td>
        <td><code>${esc(c)}</code></td><td><b>${esc(b)}</b></td>
        <td class="muted sm">${esc(l)}</td></tr>`).join('')}</tbody></table>`
      : `<p class="muted">None.</p>`}</div>
    ${(() => {
      const cr = m.ops.filter(o => m.arr(o.creates).length);
      return cr.length ? `<div class="card"><h3>Creation (${cr.length})</h3>
        <p class="muted sm">Which operation brings each business object into existence.
        A transition cannot cross entities, so this is recorded explicitly.</p>
        <table><thead><tr><th>operation</th><th>creates</th></tr></thead><tbody>${
          cr.map(o => `<tr><td><b>${esc(local(o.id))}</b></td><td>${
            m.arr(o.creates).map(c => `<b>${esc(local(c))}</b>`).join(', ')}</td></tr>`).join('')
        }</tbody></table></div>` : '';
    })()}
    ${m.events.length ? `<div class="card"><h3>Events (${m.events.length})</h3>
      <div class="chips">${m.events.map(e => `<span class="chip static">${esc(local(e.id))}</span>`).join('')}</div>
      </div>` : ''}`;
}

/* ---------- loading -------------------------------------------------- */
function load(doc, label) {
  try {
    S.doc = doc; S.model = parse(doc); S.entity = null;
    $('#src').textContent = label || '';
    render();
  } catch (err) {
    $('#empty').hidden = false; $('#main').hidden = true;
    $('#err').textContent = `Could not read that file: ${err.message}`;
  }
}

function readFile(file) {
  const r = new FileReader();
  r.onload = () => { try { load(JSON.parse(r.result), file.name); }
    catch (e) { $('#err').textContent = `${file.name} is not valid JSON: ${e.message}`; } };
  r.readAsText(file);
}

addEventListener('DOMContentLoaded', () => {
  $$('.tab').forEach(t => t.onclick = () => { S.tab = t.dataset.tab; render(); });
  $('#file').onchange = e => e.target.files[0] && readFile(e.target.files[0]);
  const dz = document.body;
  ['dragover', 'drop'].forEach(ev => dz.addEventListener(ev, e => e.preventDefault()));
  dz.addEventListener('dragover', () => dz.classList.add('drag'));
  dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
  dz.addEventListener('drop', e => {
    dz.classList.remove('drag');
    e.dataTransfer.files[0] && readFile(e.dataTransfer.files[0]);
  });
  const src = new URLSearchParams(location.search).get('src');
  if (src) fetch(src).then(r => r.json()).then(d => load(d, src))
    .catch(e => { $('#err').textContent = `Could not fetch ${src}: ${e.message}`; });
});
