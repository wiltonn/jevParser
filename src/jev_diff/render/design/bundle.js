/* @ds-bundle: {"format":4,"namespace":"JevDiff","components":[{"name":"Stat"},{"name":"Tag"},{"name":"BandPill"},{"name":"JudgmentChip"},{"name":"TrimMatrix"},{"name":"EventCard"},{"name":"Nav"},{"name":"FilterBar"},{"name":"Crumb"},{"name":"SheetTable"},{"name":"KeyedTable"},{"name":"GridTable"},{"name":"AxisLineup"},{"name":"Router"}]} */
/* The report renders with string templates, not a framework. These are the same functions
   as src/jev_diff/render/html.py (_stat) and its _JS (matrix, judgmentChips, render),
   returning HTML strings to drop into innerHTML. Styling comes from bundle.css. */
(function () {
  var esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  };
  function Stat(value, label) {
    return '<div class="stat"><b>' + esc(value) + '</b><span>' + esc(label) + '</span></div>';
  }
  function Tag(band) {
    return '<span class="tag" data-b="' + esc(band) + '">' + esc(band) + '</span>';
  }
  function BandPill(band, count, on) {
    if (band === "unjudged") {
      return '<button class="pill" data-b="unjudged" disabled>unjudged ' + esc(count) + '</button>';
    }
    return '<button class="pill' + (on ? " on" : "") + '" data-b="' + esc(band) + '">' +
      esc(band) + ' ' + esc(count) + '</button>';
  }
  function JudgmentChip(q, a) {
    var v = a.score != null ? a.score.toFixed(2)
          : a.noul != null ? a.noul.toFixed(2) : esc(a.choice);
    var c = a.confidence != null ? " ±" + a.confidence.toFixed(2) : "";
    return '<span class="q">' + esc(q) + '=' + v + c + '</span>';
  }
  function judgmentChips(judgments) {
    return Object.keys(judgments || {}).map(function (q) {
      return JudgmentChip(q, judgments[q]);
    }).join(" ");
  }
  function TrimMatrix(deltas, oldLabel, newLabel) {
    if (!deltas || !deltas.length) return "";
    var rows = deltas.map(function (d) {
      var cls = d.changed ? "" : (d.direction === "incomparable" ? "incomparable" : "unchanged");
      var conds = []
        .concat((d.dropped || []).map(function (t) { return '<span class="cond drop">− ' + esc(t) + '</span>'; }))
        .concat((d.added || []).map(function (t) { return '<span class="cond add">+ ' + esc(t) + '</span>'; }))
        .concat((d.reworded || []).map(function (p) { return '<span class="cond reword">~ ' + esc(p[0]) + ' → ' + esc(p[1]) + '</span>'; }))
        .join("");
      return '<tr class="' + cls + '">' +
        '<td>' + esc(d.trim) + '</td>' +
        '<td class="cell">' + esc(d.from || "·") + '<span class="arrow">→</span>' + esc(d.to || "·") + '</td>' +
        '<td><span class="d d-' + esc(d.direction) + '">' + esc(d.direction.replace(/_/g, " ")) + '</span>' + conds + '</td>' +
        '</tr>';
    }).join("");
    return '<table class="matrix"><thead><tr>' +
      '<th>Trim</th><th>' + esc(oldLabel) + ' → ' + esc(newLabel) + '</th><th>Change</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>';
  }
  function EventCard(e, band, value, oldLabel, newLabel) {
    var also = (e.also_on && e.also_on.length) ? " + " + e.also_on.map(esc).join(", ") : "";
    return '<article class="card ev">' +
      '<header>' + Tag(band) +
      '<span class="code">' + esc(e.code || "—") + '</span>' +
      '<span class="desc">' + esc(e.description) + '</span>' +
      '<span class="score">' + (+value).toFixed(2) + '</span>' +
      '</header>' +
      '<div class="where">' + esc(String(e.kind || "").replace(/_/g, " ")) + ' · ' + esc(e.sheet) + also + ' ' + judgmentChips(e.judgments) + '</div>' +
      (e.detail ? '<div class="detail">' + esc(e.detail) + '</div>' : "") +
      TrimMatrix(e.deltas, oldLabel, newLabel) +
      '</article>';
  }

  /* ---- UI components added for the application views ---- */

  /** The view switcher. Anchors, so links and navigation are one mechanism.
   *  Counts are totals, never the filtered number. */
  function Nav(items, current, opts) {
    var vertical = opts && opts.vertical;
    return '<nav class="nav' + (vertical ? ' vert' : '') + '">' +
      items.map(function (i) {
        if (i.group) return '<div class="nav-group">' + esc(i.group) + '</div>';
        return '<a class="nav-i' + (i.id === current ? ' on' : '') + '" href="' +
          esc(i.href) + '">' + esc(i.label) +
          (i.count == null ? '' : ' <span class="nav-n">' + esc(i.count) + '</span>') +
          '</a>';
      }).join('') + '</nav>';
  }
  
  /** Search input and the select filters in one bar, so nothing invites a
   *  stray second search box. Native selects: a custom one needs a chevron. */
  function FilterBar(fields, values, placeholder) {
    var parts = ['<input id="q" class="q-in" type="search" value="' +
      esc(values.q || '') + '" placeholder="' + esc(placeholder) + '">'];
    fields.forEach(function (f) {
      parts.push('<select id="f-' + esc(f.id) + '">' +
        ['<option value="">' + esc(f.all) + '</option>'].concat(
          f.options.map(function (o) {
            var v = typeof o === 'string' ? o : o.value;
            var l = typeof o === 'string' ? o : o.label;
            return '<option value="' + esc(v) + '"' +
              (values[f.id] === v ? ' selected' : '') + '>' + esc(l) + '</option>';
          })).join('') + '</select>');
    });
    if (values.onlyChanged !== undefined) {
      parts.push('<label class="chk"><input type="checkbox" id="only-changed"' +
        (values.onlyChanged ? ' checked' : '') + '> only changed</label>');
    }
    return '<div class="card bar">' + parts.join('') + '</div>';
  }
  
  /** Says what has been hidden and by what. The antidote to two filtering
   *  systems. The word "clear", never an icon. */
  function Crumb(shown, total, facts, linked) {
    var bits = ['showing <b>' + esc(shown) + '</b> of <b>' + esc(total) + '</b>'];
    facts.forEach(function (f) { if (f) bits.push(esc(f)); });
    var html = bits.join(' · ');
    if (linked) html = '<b>1</b> shown by link · filters would hide it';
    if (facts.filter(Boolean).length || linked) {
      html += ' &nbsp;<a id="clear">clear</a>';
    }
    return '<div class="crumb">' + html + '</div>';
  }
  
  /** Every row of a feature worksheet. Demotion follows the system's existing
   *  rule; no per-row "changed" marker is invented. */
  function SheetTable(sheet, rows, trims, opts) {
    opts = opts || {};
    var head = '<tr><th>Code</th><th>Description</th>' +
      trims.map(function (t) { return '<th>' + esc(t.name) + '</th>'; }).join('') +
      '<th></th></tr>';
    var section = null;
    var body = rows.map(function (r) {
      var out = '';
      if (r.section !== section) {
        section = r.section;
        if (section) {
          out += '<tr><td class="sec" colspan="' + (trims.length + 3) + '">' +
            esc(section) + '</td></tr>';
        }
      }
      var changed = !!r.eid;
      out += '<tr class="' + (changed ? '' : 'unchanged') +
        (opts.hit === r.rid ? ' hit' : '') + '" id="r-' + esc(r.rid) + '">' +
        '<td data-l="code" class="rowcode">' + esc(r.code || '—') + '</td>' +
        '<td data-l="description">' + esc(r.desc) + '</td>' +
        trims.map(function (t) {
          var c = r.v[t.axis];
          if (!c) return '<td data-l="' + esc(t.name) + '" class="val">\u00b7</td>';
          // A browse surface shows the token; the conditions are in the native
          // tooltip and, in full, on the event card. Dumping every clause into
          // every cell buries the availability this view exists to show -- and
          // search still matches the condition text either way.
          var n = (c.c || []).length;
          return '<td data-l="' + esc(t.name) + '" class="val"' +
            (n ? ' title="' + esc(c.c.join('\n')) + '"' : '') + '>' +
            esc(c.val || c.t || '\u00b7') +
            (n ? '<span class="condn">' + n + '</span>' : '') + '</td>';
        }).join('') +
        '<td>' + (changed
          ? '<a class="rowlink" href="#/changes/e/' + esc(r.eid) + '">change</a>'
          : '') + '</td></tr>';
      return out;
    }).join('');
    return '<table class="sheet"><thead>' + head + '</thead><tbody>' +
      body + '</tbody></table>';
  }
  
  /** label + drivetrain columns, both years. Five columns fits comfortably, and
   *  there is no single comparison to arrow here: these are values, not tokens. */
  function KeyedTable(sheet, oldLabel, newLabel) {
    var cols = sheet.trims.new;
    var byKey = {};
    sheet.rows.old.forEach(function (r) { byKey[r.desc] = {old: r}; });
    sheet.rows.new.forEach(function (r) {
      byKey[r.desc] = byKey[r.desc] || {};
      byKey[r.desc].new = r;
    });
    var head = '<tr><th>Specification</th>' +
      cols.map(function (c) {
        return '<th>' + esc(oldLabel) + ' ' + esc(c.name) + '</th>';
      }).join('') +
      cols.map(function (c) {
        return '<th>' + esc(newLabel) + ' ' + esc(c.name) + '</th>';
      }).join('') + '</tr>';
    var body = Object.keys(byKey).map(function (k) {
      var p = byKey[k];
      var cell = function (row, c) {
        if (!row) return '<td class="val">—</td>';
        var v = row.v[c.axis];
        return '<td class="val">' + esc((v && (v.val || v.t)) || '·') + '</td>';
      };
      var same = p.old && p.new && cols.every(function (c) {
        var a = p.old.v[c.axis], b = p.new.v[c.axis];
        return ((a && (a.val || a.t)) || '') === ((b && (b.val || b.t)) || '');
      });
      return '<tr class="' + (same ? 'unchanged' : '') + '">' +
        '<td>' + esc((p.new || p.old).desc) + '</td>' +
        cols.map(function (c) { return cell(p.old, c); }).join('') +
        cols.map(function (c) { return cell(p.new, c); }).join('') + '</tr>';
    }).join('');
    return '<table class="keyed"><thead>' + head + '</thead><tbody>' +
      body + '</tbody></table>';
  }
  
  /** Multi-level headers with axes that differ between years. The years stack
   *  vertically rather than sitting side by side, to avoid overflow. */
  function GridTable(sheet, oldLabel, newLabel) {
    return ['old', 'new'].map(function (year) {
      var rows = sheet.rows[year];
      var cols = sheet.trims[year];
      if (!rows.length) return '';
      return '<div class="yr">' + esc(year === 'old' ? oldLabel : newLabel) + '</div>' +
        '<table class="grid"><thead><tr><th>Model</th>' +
        cols.map(function (c) { return '<th>' + esc(c.name) + '</th>'; }).join('') +
        '</tr></thead><tbody>' + rows.map(function (r) {
          return '<tr><td>' + esc(r.desc) + '</td>' +
            cols.map(function (c) {
              var v = r.v[c.axis];
              return '<td class="val">' + esc((v && (v.val || v.t)) || '·') + '</td>';
            }).join('') + '</tr>';
        }).join('') + '</tbody></table>';
    }).join('');
  }
  
  /** The trim lineup across both years, where a renamed or dropped trim becomes
   *  legible instead of being buried in an identity event's prose. */
  function AxisLineup(sheet, oldLabel, newLabel) {
    if (!sheet.axis.changed) return '';
    var pairs = {};
    sheet.axis.pairs.forEach(function (p) { pairs[p[0]] = p[1]; });
    var name = {};
    sheet.trims.old.concat(sheet.trims.new).forEach(function (t) {
      name[t.axis] = t.name;
    });
    var rows = sheet.trims.old.map(function (t) {
      var to = pairs[t.axis];
      return '<tr><td>' + esc(t.name) + '</td><td>' +
        (to ? esc(name[to]) : '—') + '</td></tr>';
    }).concat(sheet.axis.added.map(function (t) {
      return '<tr><td>—</td><td>' + esc(t.name) + '</td></tr>';
    })).join('');
    return '<div class="yr">trim lineup</div><table class="lineup"><thead><tr>' +
      '<th>' + esc(oldLabel) + '</th><th>' + esc(newLabel) + '</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>';
  }
  
  /** Hash parse and serialise, so routing does not scatter across the views. */
  var Router = {
    parse: function (hash) {
      var raw = (hash || '').replace(/^#\/?/, '');
      var q = raw.indexOf('?');
      var path = (q < 0 ? raw : raw.slice(0, q)).split('/').filter(Boolean);
      var params = {};
      if (q >= 0) {
        raw.slice(q + 1).split('&').forEach(function (kv) {
          if (!kv) return;
          var i = kv.indexOf('=');
          var k = decodeURIComponent(i < 0 ? kv : kv.slice(0, i));
          params[k] = decodeURIComponent((i < 0 ? '' : kv.slice(i + 1)).replace(/\+/g, ' '));
        });
      }
      // The whole path is kept: entity routes are four segments
      // (#/sheets/<slug>/r/<rid>), and a two-slot model silently drops the id.
      return {view: path[0] || 'changes', path: path.slice(1), params: params};
    },
    format: function (route, params) {
      var path = ['#/' + route.view].concat(
        (route.path || []).filter(function (p) { return p != null && p !== ''; }));
      var keys = Object.keys(params || {}).filter(function (k) {
        return params[k] !== '' && params[k] != null;
      });
      return path.join('/') + (keys.length ? '?' + keys.map(function (k) {
        return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]);
      }).join('&') : '');
    }
  };

  window.JevDiff = {
    esc: esc, Stat: Stat, Tag: Tag, BandPill: BandPill,
    JudgmentChip: JudgmentChip, judgmentChips: judgmentChips,
    TrimMatrix: TrimMatrix, EventCard: EventCard,
    Nav: Nav, FilterBar: FilterBar, Crumb: Crumb, SheetTable: SheetTable,
    KeyedTable: KeyedTable, GridTable: GridTable,
    AxisLineup: AxisLineup, Router: Router
  };
})();
