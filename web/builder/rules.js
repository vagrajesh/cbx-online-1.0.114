// Rules — "when this happens, (for each row of …) only if …, then do this" — the Builder's plain
// form of a flow (1.0.53). A rule is read from a flow's steps (decompile) and written back as steps
// (compile); the flow file stays what the engine runs. A flow whose shape a rule cannot hold (an If
// with a "no" branch, a loop inside a loop) opens read-only, with that said in plain words.

export const WHEN = [['button', 'Someone clicks a button'], ['row_added', 'A row is added'], ['row_changed', 'A row changes'], ['schedule', 'On a schedule']];
export const EVERY = [['hour', 'every hour'], ['day', 'every day'], ['weekday', 'every weekday (Mon–Fri)'], ['week', 'once a week'], ['month', 'once a month']];
export const DAYS = [['mon', 'Monday'], ['tue', 'Tuesday'], ['wed', 'Wednesday'], ['thu', 'Thursday'], ['fri', 'Friday'], ['sat', 'Saturday'], ['sun', 'Sunday']];
export const OPS = [['equals', 'is'], ['not_equals', 'is not'], ['gt', 'is more than'], ['gte', 'is at least'], ['lt', 'is less than'], ['lte', 'is at most'], ['contains', 'contains'], ['not_contains', 'does not contain'],
  ['empty', 'is empty'], ['not_empty', 'is not empty'], ['days_from_today', 'is exactly … days away'], ['within_days', 'is within … days'], ['past', 'is in the past'],
  ['this_week', 'is this week'], ['this_month', 'is this month'], ['this_year', 'is this year']];
export const NO_VALUE = new Set(['empty', 'not_empty', 'past', 'this_week', 'this_month', 'this_year']);
export const DO = [
  ['notify', 'Tell someone in CBX-Connect'], ['email', 'Send an email'], ['approval', 'Ask someone to approve'], ['update_row', 'Change the row'], ['add_row', 'Add a row to a table'],
  ['require', 'Stop unless … (and say why)'], ['action', 'Run a server job (read a quote, work out a month …)'], ['make_doc', 'Make a Word document'],
  ['wait', 'Wait'], ['api', 'Call an API connection'], ['set', 'Remember a value'], ['stop', 'Stop here']];

const blank = (type) => ({ notify: { type, who: { kind: 'owner' }, title: '', text: '' }, email: { type, to: '', subject: '', text: '' }, approval: { type, who: { kind: 'owner' }, question: 'May this go ahead?' },
  update_row: { type, data: '', row: '', values: {} }, add_row: { type, data: '', values: {} }, wait: { type, days: 1 }, api: { type, connection: '', method: 'GET', path: '' }, set: { type, name: '', value: '' }, stop: { type, outcome: 'done', message: '' },
  action: { type, action: '', data: '', row: '', in: {}, out: {} }, require: { type, match: 'all', when: [{ left: '', op: 'equals', value: '' }], message: '' }, make_doc: { type, template: '', data: '', row: '', into: '', name: '' } }[type] || { type });
export const newStep = blank;

/** A flow's steps as a rule, or {readOnly: why}. */
export function decompile(f) {
  const steps = f.steps || {};
  const R = { when: { ...(f.trigger || {}) }, go: null, only: [], then: [], after: [] };
  let cur = f.start, rowName = null;
  const seen = new Set();
  const take = (id) => { if (!id) return null; if (seen.has(id)) throw new Error('it loops back on itself'); seen.add(id); const s = steps[id]; if (!s) throw new Error(`a step points at '${id}', which is not there`); return s; };
  const actions = (id, into) => {
    while (id) {
      const s = take(id);
      if (['if', 'for_each', 'find_rows'].includes(s.type)) throw new Error(`a "${s.type.replace('_', ' ')}" comes after an action`);
      if (s.type === 'approval') { const st = clean(s); if (s.no) { st.declined = []; actions(s.no, st.declined); } into.push(st); id = s.yes; continue; }
      into.push(clean(s)); id = s.next;
    }
  };
  try {
    let first = take(cur);
    if (first && first.type === 'find_rows') {
      const nx = first.next ? steps[first.next] : null;
      R.go = { data: first.data, provider: first.provider, sheet: first.sheet, params: first.params, where: [...(first.where || [])], as: first.as || 'rows', limit: first.limit, each: false, item: 'row' };
      if (nx && nx.type === 'for_each' && nx.items === (first.as || 'rows')) {
        take(first.next);
        R.go.each = true; R.go.item = nx.as || 'item'; rowName = R.go.item;
        cur = nx.body; if (nx.next) { const after = []; actions(nx.next, after); R.after = after; }
      } else {
        // once, with the whole list: its conditions (e.g. {count}) come before the steps
        rowName = null; let x = first.next; let sx = x ? take(x) : null;
        while (sx && sx.type === 'if') { if (sx.no) throw new Error('an If has steps for "no" as well as "yes"'); R.only.push({ match: sx.match === 'any' ? 'any' : 'all', conds: (sx.when || []).map((c) => ({ ...c })) }); x = sx.yes; sx = x ? take(x) : null; }
        if (sx) { seen.delete(x); actions(x, R.after); }
        cur = null;
      }
      first = cur ? take(cur) : null;
    } else if (['row_added', 'row_changed'].includes(R.when.type)) rowName = 'trigger.row';
    let s = first;
    while (s && s.type === 'if') {
      if (s.no) throw new Error('an If has steps for "no" as well as "yes"');
      R.only.push({ match: s.match === 'any' ? 'any' : 'all', conds: (s.when || []).map((c) => ({ ...c })) });
      cur = s.yes; s = cur ? take(cur) : null;
    }
    if (s) { seen.delete(cur); actions(cur, R.then); }
  } catch (e) {
    return { readOnly: `This rule has a shape the plain form cannot show: ${e.message}. It runs as it is; to change it here, make it again as a new rule.` };
  }
  R.row = rowName;
  return R;
}

function clean(s) { const o = { ...s }; delete o.next; delete o.yes; delete o.no; delete o.label; delete o.declined; return o; }

/** A rule back into a flow's trigger, start and steps (the rest of the flow is kept by the caller). */
export function compile(R) {
  const steps = {};
  let n = 0;
  const id = () => `s${++n}`;
  const chain = (list0) => { // returns the id of the first step
    let first = null, prev = null;
    // an approval needs somewhere to go when it is approved: the end, said out loud
    const list = list0.length && list0[list0.length - 1].type === 'approval' ? [...list0, { type: 'stop', outcome: 'done', message: 'Approved.' }] : list0;
    for (const st of list) {
      const k = id(); const s = { ...st };
      delete s.declined;
      steps[k] = s;
      if (st.type === 'approval' && (st.declined || []).length) s.no = chain(st.declined);
      if (prev) { if (steps[prev].type === 'approval') steps[prev].yes = k; else steps[prev].next = k; }
      if (!first) first = k;
      prev = k;
    }
    return first;
  };
  const withIfs = (list, pre) => {
    const ifs = R.only.filter((g) => (g.conds || []).length);
    const firstThen = chain(list);
    let next = firstThen, head = null;
    for (let i = ifs.length - 1; i >= 0; i--) { const k = `${pre}${i + 1}`; steps[k] = { type: 'if', match: ifs[i].match, when: ifs[i].conds, ...(next ? { yes: next } : {}) }; next = k; head = k; }
    return head || firstThen;
  };
  const perRow = () => {
    const ifs = R.only.filter((g) => (g.conds || []).length);
    const firstThen = chain(R.then);
    let next = firstThen, head = null;
    for (let i = ifs.length - 1; i >= 0; i--) { const k = `if${i + 1}`; steps[k] = { type: 'if', match: ifs[i].match, when: ifs[i].conds, ...(next ? { yes: next } : {}) }; next = k; head = k; }
    return head || firstThen;
  };
  let start;
  if (R.go) {
    const find = { type: 'find_rows', ...(R.go.provider ? { provider: R.go.provider, ...(R.go.params ? { params: R.go.params } : {}) } : R.go.sheet ? { sheet: R.go.sheet } : { data: R.go.data }), where: R.go.where || [], as: R.go.as || 'rows', ...(R.go.limit ? { limit: R.go.limit } : {}) };
    steps.find = find; start = 'find';
    if (R.go.each) {
      const after = chain(R.after);
      const body = perRow();
      steps.each = { type: 'for_each', items: find.as, as: R.go.item || 'item', ...(body ? { body } : {}), ...(after ? { next: after } : {}) };
      find.next = 'each';
    } else {
      const head = withIfs(R.after, 'ifw');
      if (head) find.next = head;
    }
  } else start = perRow();
  return { trigger: R.when, start: start || '', steps };
}

// ---- the words in a message: {{doc.days_left}} is shown as {Days left} and written back ----------------
export function showTokens(text, row, cols) {
  return String(text ?? '').replace(/\{\{\s*([^}]+?)\s*\}\}/g, (m, path) => {
    if (row && path.startsWith(row + '.')) { const k = path.slice(row.length + 1); const c = (cols || []).find((x) => x.key === k); return `{${c ? c.label : k}}`; }
    return `{${path}}`;
  });
}
export function storeTokens(text, row, cols) {
  return String(text ?? '').replace(/\{\{[^}]*\}\}|\{([^{}]+)\}/g, (m, inner) => {
    if (inner === undefined) return m; // already {{…}}
    const t = inner.trim(), c = (cols || []).find((x) => x.label.toLowerCase() === t.toLowerCase() || x.key === t);
    if (c && row) return `{{${row}.${c.key}}}`;
    return `{{${t}}}`;
  });
}
