/* jev-diff UI: router, views, and the components that extend the design system.
   Rendering is string templates dropped into innerHTML, exactly as the system's
   bundle.js does; the existing components come from window.JevDiff and are never
   re-implemented here. */
(function () {
"use strict";

var D = window.__CHANGESET__;
var S = window.JevDiff;
var esc = S.esc;
var $ = function (sel) { return document.querySelector(sel); };

/* The components live in the design system's bundle (vendored under
   design/) and are reached through window.JevDiff. Only behaviour -- the
   router's use, the lens arithmetic, search, filtering and the views -- is
   this project's. */
var Nav = S.Nav, FilterBar = S.FilterBar, Crumb = S.Crumb;
var SheetTable = S.SheetTable, KeyedTable = S.KeyedTable;
var GridTable = S.GridTable, AxisLineup = S.AxisLineup, Router = S.Router;

/* ===================================================================== *
 * Application state and views. This is jev-diff's behaviour, not the     *
 * design system's.                                                      *
 * ===================================================================== */

var DEFAULTS = {
  lens: 'ordering',
  bands: {material: true, review: true, cosmetic: false},
  q: '', sheet: '', trim: '', kind: '', onlyChanged: false
};

var st = {
  view: 'changes', path: [],
  lens: DEFAULTS.lens,
  weights: null, material: 0, review: 0, floor: 0,
  bands: Object.assign({}, DEFAULTS.bands),
  q: '', sheet: '', trim: '', kind: '', onlyChanged: false
};

function applyLens(name) {
  var L = D.lenses[name] || D.lenses[DEFAULTS.lens];
  st.lens = D.lenses[name] ? name : DEFAULTS.lens;
  st.weights = Object.assign({}, L.weights);
  st.material = L.bands.material;
  st.review = L.bands.review;
  st.floor = L.confidence_floor;
}

function lensValue(e) {
  var total = 0, div = 0;
  for (var q in st.weights) {
    var a = e.judgments[q];
    if (a && a.score != null) total += st.weights[q] * a.score;
    div += Math.abs(st.weights[q]);
  }
  return div ? total / div : 0;
}
function minConfidence(e) {
  var cs = [];
  for (var q in st.weights) {
    if (st.weights[q] > 0 && e.judgments[q] && e.judgments[q].confidence != null) {
      cs.push(e.judgments[q].confidence);
    }
  }
  return cs.length ? Math.min.apply(null, cs) : null;
}
function bandOf(e) {
  if (!Object.keys(e.judgments).length) return 'unjudged';
  var v = lensValue(e), c = minConfidence(e);
  if (v >= st.material) return (c !== null && c < st.floor) ? 'review' : 'material';
  if (v >= st.review) return 'review';
  return 'cosmetic';
}

/* Search matches code, description, resolved condition text and the templated
   detail. NOT trim or sheet name: those are finite domains, and free-texting
   them makes a result impossible to explain. They are selects. */
function haystack(e) {
  if (e._hay) return e._hay;
  var parts = [e.code || '', e.description, e.detail || ''];
  (e.deltas || []).forEach(function (d) {
    parts = parts.concat(d.added || [], d.dropped || []);
    (d.reworded || []).forEach(function (p) { parts = parts.concat(p); });
  });
  e._hay = parts.join(' \u0000 ').toLowerCase();
  return e._hay;
}
function matches(e) {
  var q = st.q.trim().toLowerCase();
  if (q && haystack(e).indexOf(q) < 0) return false;
  if (st.sheet && e.sheet !== st.sheet &&
      (e.also_on || []).indexOf(st.sheet) < 0) return false;
  if (st.kind && e.kind !== st.kind) return false;
  if (st.trim && !(e.deltas || []).some(function (d) {
    return d.trim === st.trim && d.changed;
  })) return false;
  return true;
}

function stateParams() {
  var p = {};
  if (st.lens !== DEFAULTS.lens) p.lens = st.lens;
  var L = D.lenses[st.lens];
  for (var k in st.weights) {
    if (st.weights[k] !== L.weights[k]) p['w.' + k] = st.weights[k];
  }
  if (st.material !== L.bands.material) p.m = st.material;
  if (st.review !== L.bands.review) p.r = st.review;
  if (st.floor !== L.confidence_floor) p.c = st.floor;
  var on = Object.keys(st.bands).filter(function (b) { return st.bands[b]; }).sort();
  var def = Object.keys(DEFAULTS.bands).filter(function (b) {
    return DEFAULTS.bands[b];
  }).sort();
  if (on.join(',') !== def.join(',')) p.b = on.join(',');
  ['q', 'sheet', 'trim', 'kind'].forEach(function (k) { if (st[k]) p[k] = st[k]; });
  if (st.onlyChanged) p.changed = '1';
  return p;
}

function readParams(params) {
  applyLens(params.lens || DEFAULTS.lens);
  for (var k in params) {
    if (k.indexOf('w.') === 0) st.weights[k.slice(2)] = +params[k];
  }
  if (params.m != null) st.material = +params.m;
  if (params.r != null) st.review = +params.r;
  if (params.c != null) st.floor = +params.c;
  st.bands = {material: false, review: false, cosmetic: false};
  var on = params.b != null ? params.b.split(',').filter(Boolean)
                            : Object.keys(DEFAULTS.bands).filter(function (b) {
                                return DEFAULTS.bands[b];
                              });
  on.forEach(function (b) { st.bands[b] = true; });
  ['q', 'sheet', 'trim', 'kind'].forEach(function (k) { st[k] = params[k] || ''; });
  st.onlyChanged = params.changed === '1';
}

/* A slider drag must not push forty history entries; a view change must push
   exactly one. */
function pushState(replace) {
  var url = Router.format({view: st.view, path: st.path}, stateParams());
  if (replace) history.replaceState(null, '', url);
  else location.hash = url;
}

/* --------------------------------------------------------------------- *
 * Views                                                                  *
 * --------------------------------------------------------------------- */

var SHEETS = {};
D.sheets.forEach(function (s) { SHEETS[s.slug] = s; });
var EVENTS = {};
D.events.forEach(function (e) { EVENTS[e.eid] = e; });

/* The system's EventCard emits no id, and a deep link needs one to scroll to.
   Rather than change that component's contract, the anchor is added here; the
   scroll-margin stays on .ev where the system put it. */
function card(e, band, value, extra) {
  return S.EventCard(e, band, value, D.old, D.new).replace(
    '<article class="card ev"',
    '<article id="e-' + esc(e.eid) + '" class="card ev' + (extra || '') + '"');
}

function stats(pairs) {
  return '<div class="card stats">' + pairs.map(function (p) {
    return S.Stat(p[0], p[1]);
  }).join('') + '</div>';
}

function navHtml() {
  return Nav([
    {id: 'changes', label: 'changes', count: D.events.length, href: '#/changes'},
    {id: 'sheets', label: 'sheets', count: D.sheets.length, href: '#/sheets'},
    {id: 'pairing', label: 'pairing',
     count: D.summary.residue + D.summary.judged, href: '#/pairing'}
  ], st.view);
}

function tunerHtml() {
  var ctl = function (label, id, attr, max, value) {
    return '<div class="ctl"><label>' + esc(label) + '</label>' +
      '<input type="range" ' + attr + ' min="0" max="' + max +
      '" step="0.05" value="' + value + '"><output>' +
      (+value).toFixed(2) + '</output></div>';
  };
  var lens = Object.keys(D.lenses).map(function (name) {
    return '<button data-lens="' + esc(name) + '"' +
      (name === st.lens ? ' class="on"' : '') + '>' +
      esc(name === 'ordering' ? 'ordering accuracy' : 'content ops') + '</button>';
  }).join('');
  var tiers = Object.keys(D.summary.tiers).sort().map(function (t) {
    return 'tier ' + t + ': ' + D.summary.tiers[t];
  }).join(' · ');
  return '<div class="card tuner"><div class="trow">' +
    '<label>Lens</label>' + lens + '<span class="where">' + esc(tiers) + '</span>' +
    '</div><div class="trow">' +
    ctl('order risk', '', 'data-w="order_risk"', 1, st.weights.order_risk) +
    ctl('content churn', '', 'data-w="content_churn"', 1, st.weights.content_churn) +
    ctl('material \u2265', '', 'id="material"', 2, st.material) +
    ctl('review \u2265', '', 'id="review"', 2, st.review) +
    ctl('min confidence', '', 'id="floor"', 1, st.floor) +
    '<button id="export">export profile</button></div></div>';
}

function filterFacts() {
  var on = Object.keys(st.bands).filter(function (b) { return st.bands[b]; });
  return [
    on.length === 3 ? '' : on.join(', '),
    st.sheet ? 'sheet ' + st.sheet : '',
    st.trim ? 'trim ' + st.trim : '',
    st.kind ? 'kind ' + st.kind.replace(/_/g, ' ') : '',
    st.q ? '"' + st.q + '"' : ''
  ];
}

function allTrims() {
  var seen = {}, out = [];
  D.sheets.forEach(function (s) {
    s.trims.new.forEach(function (t) {
      if (!seen[t.name]) { seen[t.name] = 1; out.push(t.name); }
    });
  });
  return out;
}

function viewChanges() {
  var linkedId = st.path[0] === 'e' ? st.path[1] : null;
  var linked = linkedId ? EVENTS[linkedId] : null;

  var scored = D.events.map(function (e) {
    return {e: e, band: bandOf(e), value: lensValue(e)};
  });
  var counts = {material: 0, review: 0, cosmetic: 0, unjudged: 0};
  scored.forEach(function (r) { counts[r.band]++; });

  var visible = scored.filter(function (r) {
    return (st.bands[r.band] || r.band === 'unjudged') && matches(r.e);
  }).sort(function (a, b) { return b.value - a.value; });

  // A deep-linked event is ALWAYS rendered, or every link to a cosmetic
  // change lands on nothing -- cosmetic is off by default.
  var hiddenLink = linked && !visible.some(function (r) {
    return r.e.eid === linked.eid;
  });

  var pills = ['material', 'review', 'cosmetic'].map(function (b) {
    return S.BandPill(b, counts[b], st.bands[b]);
  }).join('') + (counts.unjudged
    ? S.BandPill('unjudged', counts.unjudged) : '');

  var body = visible.length
    ? visible.map(function (r) { return card(r.e, r.band, r.value); }).join('')
    : '<div class="card empty">Nothing matches ' +
      (filterFacts().filter(Boolean).join(' · ') || 'the current bands') + '.</div>';

  var linkedHtml = '';
  if (hiddenLink) {
    linkedHtml = card(linked, bandOf(linked), lensValue(linked), ' linked');
  }

  return stats([
    [D.summary.rows_old + '\u2192' + D.summary.rows_new, 'rows'],
    [D.summary.paired, 'paired'],
    [D.events.length, 'changes'],
    [D.summary.multi_sheet_codes + '/' + D.summary.codes, 'codes on 2+ sheets'],
    [D.summary.requests, 'jev requests'],
    ['$' + (D.summary.input_tokens * 0.042 / 1e6).toFixed(4), 'spend']
  ]) + tunerHtml() +
    FilterBar([
      {id: 'sheet', all: 'all sheets',
       options: D.sheets.map(function (s) { return s.name; })},
      {id: 'trim', all: 'all trims', options: allTrims()},
      {id: 'kind', all: 'all kinds',
       options: Object.keys(D.events.reduce(function (a, e) {
         a[e.kind] = 1; return a;
       }, {})).sort().map(function (k) {
         return {value: k, label: k.replace(/_/g, ' ')};
       })}
    ],
    // Deliberately not `st`: "only changed" belongs to the sheets view, where
    // most rows are unchanged. Every row on this view is a change already.
    {q: st.q, sheet: st.sheet, trim: st.trim, kind: st.kind},
    'code, description or condition text') +
    '<div class="bands" id="bands">' + pills + '</div>' +
    Crumb(visible.length, D.events.length, filterFacts(), hiddenLink) +
    linkedHtml + '<div id="events">' + body + '</div>';
}

function viewSheets() {
  var slug = st.path[0] && SHEETS[st.path[0]] ? st.path[0] : D.sheets[0].slug;
  var sheet = SHEETS[slug];
  var hit = st.path[1] === 'r' ? st.path[2] : null;

  var groups = {feature: [], keyed: [], grid: []};
  D.sheets.forEach(function (s) { (groups[s.shape] || groups.feature).push(s); });
  var items = [];
  [['feature sheets', 'feature'], ['spec sheets', 'keyed'],
   ['ratings', 'grid']].forEach(function (g) {
    if (!groups[g[1]].length) return;
    items.push({group: g[0]});
    groups[g[1]].forEach(function (s) {
      items.push({id: s.slug, label: s.name, count: s.counts.changed,
                  href: '#/sheets/' + s.slug});
    });
  });

  var rows = sheet.rows.new.length ? sheet.rows.new : sheet.rows.old;
  var q = st.q.trim().toLowerCase();
  var shown = rows.filter(function (r) {
    if (st.onlyChanged && !r.eid) return false;
    if (!q) return true;
    var hay = [r.code || '', r.desc].concat(
      Object.keys(r.v).map(function (k) { return (r.v[k].c || []).join(' '); })
    ).join(' ').toLowerCase();
    return hay.indexOf(q) >= 0;
  });

  var table = sheet.shape === 'keyed' ? KeyedTable(sheet, D.old, D.new)
            : sheet.shape === 'grid' ? GridTable(sheet, D.old, D.new)
            : SheetTable(sheet, shown, sheet.trims.new, {hit: hit});

  return stats([
    [sheet.counts.rows_old + '\u2192' + sheet.counts.rows_new, 'rows'],
    [sheet.counts.paired, 'paired'],
    [sheet.counts.changed, 'changed'],
    [sheet.trims.new.length, 'trims']
  ]) +
    FilterBar([], {q: st.q, onlyChanged: st.onlyChanged},
              'code, description or condition text') +
    Crumb(shown.length, rows.length,
          [st.onlyChanged ? 'only changed' : '', st.q ? '"' + st.q + '"' : ''],
          false) +
    '<div class="rail">' + Nav(items, slug, {vertical: true}) +
    '<div class="rail-body card ev">' + AxisLineup(sheet, D.old, D.new) +
    table + '</div></div>';
}

function viewPairing() {
  var order = ['needs_review', 'retired', 'introduced', 'moved_off_sheet',
               'moved_onto_sheet', 'listing_removed', 'listing_added'];
  var by = {};
  D.residue.forEach(function (r) {
    (by[r.disposition] = by[r.disposition] || []).push(r);
  });
  var groups = order.filter(function (d) { return by[d]; }).map(function (d) {
    return '<div class="grp-h">' + esc(d.replace(/_/g, ' ')) + ' \u00b7 ' +
      by[d].length + '</div>' + by[d].map(function (r) {
        return '<div class="card unp" id="u-' + esc(r.uid) + '">' +
          '<span class="code">' + esc(r.code || '\u2014') + '</span>' +
          '<span class="desc">' + esc(r.description) + '</span>' +
          '<span class="where">' + esc(r.sheet) + ' \u00b7 ' + esc(r.year) + '</span>' +
          '</div>';
      }).join('');
  }).join('');

  var judged = '<table class="plain"><thead><tr><th>Outcome</th><th>Score</th>' +
    '<th>Conf.</th><th>Succession</th><th>Candidate</th></tr></thead><tbody>' +
    D.pairings.map(function (p) {
      return '<tr class="jrow" id="j-' + esc(p.jid) + '">' +
        '<td>' + esc(p.outcome) + '</td>' +
        '<td class="mono">' + (p.score || 0).toFixed(2) + '</td>' +
        '<td class="mono">' + (p.confidence || 0).toFixed(2) + '</td>' +
        '<td class="mono">' + (p.succession || 0).toFixed(2) + '</td>' +
        '<td>' + esc(p.key.replace(/^pair:/, '').slice(0, 110)) + '</td></tr>';
    }).join('') + '</tbody></table>';

  return stats([
    [D.summary.residue, 'unpaired rows'],
    [D.summary.needs_review, 'need a judgment'],
    [D.summary.judged, 'identity judgments'],
    [Object.keys(D.summary.tiers).length, 'tiers used']
  ]) +
    '<p class="sub">Rows the deterministic tiers could not pair, and why each ' +
    'candidate was matched or rejected. Everything but ' +
    '<span class="mono">needs review</span> was settled by the option ' +
    'dictionary alone.</p>' + groups +
    '<h2>Identity judgments</h2>' + '<div class="card ev">' + judged + '</div>';
}

/* --------------------------------------------------------------------- *
 * Render and wiring                                                      *
 * --------------------------------------------------------------------- */

function render() {
  var views = {changes: viewChanges, sheets: viewSheets, pairing: viewPairing};
  var fn = views[st.view] || viewChanges;
  $('#app').innerHTML = navHtml() + fn();
  wire();
  var anchor = st.view === 'changes' && st.path[0] === 'e'
    ? document.getElementById('e-' + st.path[1])
    : st.view === 'sheets' && st.path[1] === 'r'
    ? document.getElementById('r-' + st.path[2]) : null;
  if (anchor) anchor.scrollIntoView({block: 'center'});
}

function on(sel, ev, fn) {
  var el = $(sel);
  if (el) el.addEventListener(ev, fn);
}

function wire() {
  document.querySelectorAll('[data-w]').forEach(function (el) {
    el.oninput = function () {
      st.weights[el.dataset.w] = +el.value;
      el.nextElementSibling.value = (+el.value).toFixed(2);
      pushState(true); render();
    };
  });
  ['material', 'review', 'floor'].forEach(function (k) {
    var el = $('#' + k);
    if (!el) return;
    el.oninput = function () {
      st[k] = +el.value;
      el.nextElementSibling.value = (+el.value).toFixed(2);
      pushState(true); render();
    };
  });
  document.querySelectorAll('[data-lens]').forEach(function (el) {
    el.onclick = function () { applyLens(el.dataset.lens); pushState(true); render(); };
  });
  on('#bands', 'click', function (ev) {
    var b = ev.target.dataset && ev.target.dataset.b;
    if (!b || b === 'unjudged') return;
    st.bands[b] = !st.bands[b];
    pushState(true); render();
  });
  on('#q', 'input', function (ev) {
    st.q = ev.target.value;
    pushState(true);
    var pos = ev.target.selectionStart;
    render();
    var again = $('#q');
    if (again) { again.focus(); again.setSelectionRange(pos, pos); }
  });
  ['sheet', 'trim', 'kind'].forEach(function (k) {
    on('#f-' + k, 'change', function (ev) {
      st[k] = ev.target.value; pushState(true); render();
    });
  });
  on('#only-changed', 'change', function (ev) {
    st.onlyChanged = ev.target.checked; pushState(true); render();
  });
  on('#clear', 'click', function () {
    st.q = ''; st.sheet = ''; st.trim = ''; st.kind = '';
    st.onlyChanged = false;
    st.bands = Object.assign({}, DEFAULTS.bands);
    st.path = [];
    pushState(true); render();
  });
  on('#export', 'click', function () {
    var w = Object.keys(st.weights).map(function (k) {
      return k + ' = ' + st.weights[k];
    }).join(', ');
    var toml = '# exported from the ' + D.old + ' \u2192 ' + D.new + ' report\n' +
      '[lens.' + st.lens + ']\nweights = { ' + w + ' }\n' +
      'bands    = { material = ' + st.material + ', review = ' + st.review + ' }\n' +
      'confidence_floor = ' + st.floor + '\n';
    var a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([toml], {type: 'text/plain'}));
    a.download = st.lens + '.toml';
    a.click();
  });
  on('#theme', 'click', function () {
    var now = document.documentElement.getAttribute('data-theme');
    document.documentElement.setAttribute('data-theme',
      now === 'dark' ? 'light' : 'dark');
  });
}

function route() {
  var r = Router.parse(location.hash);
  st.view = ['changes', 'sheets', 'pairing'].indexOf(r.view) >= 0
    ? r.view : 'changes';
  st.path = r.path;
  readParams(r.params);
  render();
}

window.addEventListener('hashchange', route);
applyLens(DEFAULTS.lens);
route();

window.JevDiffApp = {
  st: st, bandOf: bandOf, lensValue: lensValue,
  applyLens: applyLens, readParams: readParams, stateParams: stateParams,
  matches: matches, pushState: pushState, DEFAULTS: DEFAULTS
};
})();
