/* Context Studio schema viewer - parsing, validation and layout.
   Works on any compiled .jsonld from this pipeline, no build step, no CDN. */

const S = { doc: null, model: null, entity: null, tab: 'lifecycles' };
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const local = id => String(id || '').split(':').pop();
const esc = s => String(s ?? '').replace(/[&<>"]/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/* ---------- parse ---------------------------------------------------- */
function parse(doc) {
  const graph = doc['@graph'] || [];
  const by = t => graph.filter(n => n.type === t || n['@type'] === t);
  const norm = n => ({ ...n, id: n.id || n['@id'] });
  const arr = v => v == null ? [] : Array.isArray(v) ? v : [v];

  const meta = graph.find(n => (n.type || n['@type'] || '').includes('Dataset')) || {};
  const entities = by('Entity').map(norm);
  const states = by('State').map(norm);
  const ops = by('Operation').map(norm);
  const events = by('Event').map(norm);

  const stateById = Object.fromEntries(states.map(s => [s.id, s]));
  const opsByEntity = {};
  ops.forEach(o => (opsByEntity[local(o.ownedBy)] ||= []).push(o));

  return {
    title: meta.name || doc.title || 'Schema',
    version: meta['schema:version'] || '',
    entities, states, ops, events, stateById, opsByEntity,
    arr,
    counts: {
      entities: entities.length, states: states.length,
      operations: ops.length, events: events.length,
      decisions: states.filter(s => s.isDecisionPoint).length,
      creations: ops.filter(o => arr(o.creates).length).length,
      actors: new Set(ops.map(o => o.performedBy).filter(Boolean)).size,
      systems: new Set(ops.flatMap(o => arr(o.usesSystem))).size,
      hitl: ops.filter(o => o.requiresHuman).length,
      highRisk: ops.filter(o => o.risk === 'high').length,
    },
  };
}

/* ---------- validation (mirrors compiler/compile.py) ------------------ */
function validate(m) {
  const errs = [], warns = [], arr = m.arr;
  const ids = new Set([...m.entities, ...m.states, ...m.ops, ...m.events].map(n => n.id));

  // connectivity is bidirectional: an entity that others point at is connected
  // even with no outgoing relations of its own.
  const related = new Set();
  m.entities.forEach(e => arr(e.relatesTo).forEach(t => {
    related.add(local(e.id)); related.add(local(t));
  }));
  m.entities.forEach(e => {
    if (!related.has(local(e.id)))
      errs.push(['A6', `${local(e.id)}: isolated entity - no relation to or from any other entity`]);
  });

  // a lifecycle entity with no origin: nothing creates it and nothing
  // transitions into its initial state. `f` and `t` belong to one entity, so
  // creation can never be an edge - `creates` is how it is recorded.
  const made = new Set(m.ops.flatMap(o => arr(o.creates).map(local)));
  const landed = new Set(m.ops.map(o => local(o.to)));
  m.entities.forEach(e => {
    const name = local(e.id);
    if (!arr(e.hasState).length || e.origin === 'external') return;
    const init = local(e.initialState);
    if (!made.has(name) && !landed.has(init))
      warns.push(['C3', `${name}: nothing creates it and nothing reaches ${init}`]);
  });

  m.entities.forEach(e => {
    const name = local(e.id);
    const states = arr(e.hasState).map(local);
    arr(e.relatesTo).forEach(r => {
      if (!ids.has(r)) errs.push(['B10', `${name}: relation target ${local(r)} does not exist`]);
    });
    if (!states.length) return;
    const init = local(e.initialState);
    const term = new Set(arr(e.terminalStates).map(local));
    if (!states.includes(init)) errs.push(['B8', `${name}: initial state ${init} not in hasState`]);
    [...term].forEach(t => {
      if (!states.includes(t)) errs.push(['B8', `${name}: terminal state ${t} not in hasState`]);
    });
    if (!term.size) warns.push(['B8', `${name}: lifecycle with no terminal states`]);

    const out = {};
    (m.opsByEntity[name] || []).forEach(o => {
      const f = local(o.from), t = local(o.to);
      if (!states.includes(f)) errs.push(['B8', `${o.name}: ${f} is not a state of ${name}`]);
      if (!states.includes(t)) errs.push(['B8', `${o.name}: ${t} is not a state of ${name}`]);
      (out[f] ||= []).push({ op: o.name, to: t });
    });

    const seen = new Set([init]), q = [init];
    while (q.length) (out[q.shift()] || []).forEach(({ to }) => {
      if (!seen.has(to)) { seen.add(to); q.push(to); }
    });
    states.forEach(s => {
      if (!seen.has(s)) errs.push(['B8', `${s}: unreachable from ${init}`]);
      const fan = out[s] || [];
      if (term.has(s) && fan.length)
        errs.push(['B8', `${s}: terminal state has outgoing operations (${fan.map(f => f.op).join(', ')})`]);
      if (!term.has(s) && !fan.length)
        errs.push(['B8', `${s}: non-terminal state has no outgoing operation (deadlock)`]);
    });
  });
  return { errs, warns };
}

/* ---------- state-machine layout ------------------------------------- */
function layout(m, entity) {
  const name = local(entity.id);
  const states = m.arr(entity.hasState).map(local);
  const init = local(entity.initialState);
  const term = new Set(m.arr(entity.terminalStates).map(local));
  const ops = m.opsByEntity[name] || [];

  const out = {};
  ops.forEach(o => (out[local(o.from)] ||= []).push({ op: o, to: local(o.to) }));

  const rank = { [init]: 0 }, q = [init];
  while (q.length) {
    const cur = q.shift();
    (out[cur] || []).forEach(({ to }) => {
      if (rank[to] === undefined) { rank[to] = rank[cur] + 1; q.push(to); }
    });
  }
  const maxRank = Math.max(0, ...Object.values(rank));
  states.forEach(s => { if (rank[s] === undefined) rank[s] = maxRank + 1; });

  const cols = {};
  states.forEach(s => (cols[rank[s]] ||= []).push(s));
  const COL = 220, ROW = 96, PAD = 30;
  const pos = {};
  Object.entries(cols).forEach(([r, list]) => list.forEach((s, i) => {
    pos[s] = { x: PAD + r * COL, y: PAD + i * ROW };
  }));

  const rows = Math.max(...Object.values(cols).map(c => c.length));
  return {
    pos, out, init, term, states, ops,
    w: PAD * 2 + maxRank * COL + 170,
    h: PAD * 2 + Math.max(1, rows) * ROW,
    decision: new Set(states.filter(s => (out[s] || []).length > 1)),
  };
}

function svgFor(m, entity) {
  const L = layout(m, entity);
  const NW = 150, NH = 46;
  const cx = s => L.pos[s].x + NW / 2, cy = s => L.pos[s].y + NH / 2;
  let edges = '', labels = '';

  Object.entries(L.out).forEach(([from, list]) => list.forEach(({ op, to }, i) => {
    const a = L.pos[from], b = L.pos[to];
    if (!a || !b) return;
    let d, lx, ly;
    if (from === to) {
      d = `M${a.x + NW - 20},${a.y} C${a.x + NW + 60},${a.y - 50} ${a.x + NW + 60},${a.y + NH + 10} ${a.x + NW - 20},${a.y + NH}`;
      lx = a.x + NW + 30; ly = a.y + NH / 2;
    } else {
      const back = b.x <= a.x;
      const x1 = back ? a.x : a.x + NW, x2 = back ? b.x + NW : b.x;
      const my = (cy(from) + cy(to)) / 2 + (back ? 52 : 0) + i * 6;
      d = `M${x1},${cy(from)} C${(x1 + x2) / 2},${cy(from)} ${(x1 + x2) / 2},${my} ${x2},${cy(to)}`;
      lx = (x1 + x2) / 2; ly = my - 8;
    }
    const cls = op.requiresHuman ? 'edge hitl' : op.risk === 'high' ? 'edge risk' : 'edge';
    edges += `<path class="${cls}" d="${d}" marker-end="url(#arrow)"><title>${esc(op.description || op.name)}</title></path>`;
    labels += `<text class="elabel" x="${lx}" y="${ly}" text-anchor="middle">${esc(op.name)}${op.requiresHuman ? ' 👤' : ''}</text>`;
  }));

  const nodes = L.states.map(s => {
    const p = L.pos[s], role = s === L.init ? 'init' : L.term.has(s) ? 'term' : '';
    const dec = L.decision.has(s) ? ' decision' : '';
    const label = s.startsWith(local(entity.id)) ? s.slice(local(entity.id).length) || s : s;
    const st = m.states.find(x => local(x.id) === s);
    return `<g class="node ${role}${dec}" transform="translate(${p.x},${p.y})">
      <rect width="${NW}" height="${NH}" rx="9"/>
      <text x="${NW / 2}" y="26" text-anchor="middle">${esc(label)}</text>
      ${L.decision.has(s) ? `<circle class="dot" cx="${NW - 12}" cy="12" r="5"/>` : ''}
      <title>${esc(st?.description || s)}</title></g>`;
  }).join('');

  return `<svg viewBox="0 0 ${L.w} ${L.h}" width="${L.w}" height="${L.h}" role="img"
     aria-label="State machine for ${esc(local(entity.id))}">
    <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5"
      markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z"/></marker></defs>
    ${edges}${labels}${nodes}</svg>`;
}
