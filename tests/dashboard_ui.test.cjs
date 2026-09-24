const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const appRoot = path.resolve(__dirname, '../app/static');
const source = fs.readFileSync(path.join(appRoot, 'app.js'), 'utf8').split('\nwirePreferences();')[0];

function harness() {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {innerHTML: '', textContent: '', value: '', hidden: false,
      disabled: false, dataset: {}, classList: {toggle() {}}, addEventListener() {},
      querySelectorAll() { return []; }, querySelector() { return element(id + '/child'); }});
    return elements.get(id);
  };
  let storageReads = 0, storageWrites = 0, mapCalls = 0;
  const ctx = vm.createContext({console, URL, Set, Map,
    window: {location: {origin: 'http://127.0.0.1:8765'}, confirm: () => true},
    document: {querySelector: element, getElementById: id => element('#' + id),
      querySelectorAll: () => []},
    localStorage: {getItem() { storageReads++; return null; }, setItem() { storageWrites++; }},
  });
  vm.runInContext(source, ctx);
  const run = code => vm.runInContext(code, ctx);
  run(`state.data = {profile: {anchor_label: 'Anchor', move_window: {lease_end: '2030-10-01', start: '2030-09-01', end: '2030-10-31'}},
    candidates: [], score_categories: [], category_labels: {}, areas: [], risk_flags: [], pending_proposals: []};`);
  ctx.renderLeafletMap = () => { mapCalls++; };
  return {ctx, run, element, counts: () => ({storageReads, storageWrites, mapCalls})};
}

test('availability retains recorded dates outside preference window', () => {
  const h = harness();
  for (const value of ['Available 2029-01-01 (confirmed)', 'Available 2032-01-01', 'Unknown', 'Available now']) {
    assert.equal(h.run(`normalizeAvailability(${JSON.stringify(value)})`), value);
  }
  const html = h.run(`renderUnitOptions({unit_options:[{label:'Studio', bedrooms:0, availability:'Available 2029-01-01'}]})`);
  assert.match(html, /Available 2029-01-01/);
  assert.match(html, /0BR/);
});

test('search and candidate selections never persist by default', () => {
  const h = harness();
  h.element('#candidateSearch').value = 'sensitive query';
  h.run("state.selectedCandidateId='private-id'; savePersistedUi(); loadPersistedUi();");
  assert.equal(h.counts().storageReads, 0);
  assert.equal(h.counts().storageWrites, 0);
});

test('map is offline until opted in, and can be disabled', () => {
  const h = harness();
  h.run(`state.data.candidates=[{id:'a', name:'A', map_location:{lat:1,lng:2}}];
    visibleCandidates=()=>state.data.candidates; renderMap();`);
  assert.equal(h.counts().mapCalls, 0);
  assert.match(h.element('#candidateMap').innerHTML, /enable|disabled|off/i);
  assert.equal(h.run('typeof setExternalMapsEnabled'), 'function');
  h.run('setExternalMapsEnabled(true)');
  assert.equal(h.counts().mapCalls, 1);
  h.run('setExternalMapsEnabled(false)');
  assert.equal(h.counts().mapCalls, 1);
});

test('vendored runtime resources are local and present', () => {
  const html = fs.readFileSync(path.join(appRoot, 'index.html'), 'utf8');
  const urls = [...html.matchAll(/(?:src|href)="([^"]+)"/g)].map(m => m[1]);
  for (const url of urls) {
    assert.ok(url.startsWith('/'), `external runtime resource: ${url}`);
    assert.ok(fs.existsSync(path.join(appRoot, url.split('?')[0])), `missing ${url}`);
  }
  assert.match(html, /OpenStreetMap/);
  assert.match(html, /IP address/);
  assert.match(html, /viewed area/);
  assert.doesNotMatch(html, /South Florida/);
});

test('score input labels and identifiers are escaped', () => {
  const h = harness();
  h.run(`state.data.score_categories=['x" onfocus="bad']; state.data.category_labels={'x" onfocus="bad':'<img src=x onerror=bad>'}; renderScoreInputs();`);
  const html = h.element('#scoreInputs').innerHTML;
  assert.doesNotMatch(html, /<img|name="x" onfocus/);
  assert.match(html, /&lt;img/);
  assert.match(html, /x&quot; onfocus=&quot;bad/);
});

test('studio is not a rejection badge without configured rejection', () => {
  const h = harness();
  h.run(`state.data.profile.reject_flags=[]; state.data.profile.unit={allow_studio:true};`);
  const html = h.run(`candidateRow({id:'a', name:'Studio', status:'active', risk_flags:['studio_unit'], score_summary:{}})`);
  assert.doesNotMatch(html, /row-badge ">STUDIO/);
  h.run(`state.data.profile.reject_flags=['studio_unit']`);
  assert.match(h.run(`candidateRow({id:'a', name:'Studio', status:'active', risk_flags:['studio_unit'], score_summary:{}})`), /row-badge ">STUDIO/);
});

test('computed rejections and baseline flags excluded from default selection', () => {
  const h = harness();
  h.run(`state.data.candidates=[{id:'reject',status:'active',evaluation:{rejected:true},score_summary:{weighted_score:10}},
    {id:'base',status:'active',is_baseline:true,score_summary:{weighted_score:9}},
    {id:'live',status:'active',score_summary:{weighted_score:6}}];`);
  assert.equal(h.run('defaultSelectedCandidateId()'), 'live');
});

test('read-only rows have no archive or status mutation controls', () => {
  const h = harness();
  h.run('state.data.read_only=true');
  const html = h.run(`candidateRow({id:'a',name:'A',status:'active',score_summary:{}})`);
  assert.doesNotMatch(html, /data-delete-candidate|data-row-status/);
  h.run('renderProfile()');
  assert.equal(h.element('#editSelectedBtn').disabled, true);
});

test('neutral scores do not imply an owner-specific acceptance threshold', () => {
  const h = harness();
  for (const score of [5, 6, 7.5, 9]) {
    const html = h.run(`candidateRow({id:'a',name:'A',status:'active',score_summary:{weighted_score:${score}}})`);
    assert.doesNotMatch(html, /band-good|band-ok|band-low/);
  }
});

test('vendor hashes match pinned release manifest and JavaScript parses', () => {
  const {createHash} = require('node:crypto');
  const manifest = JSON.parse(fs.readFileSync(path.join(appRoot, 'vendor/manifest.json')));
  for (const pkg of manifest) for (const file of pkg.files) {
    const bytes = fs.readFileSync(path.join(appRoot, 'vendor', file.path));
    assert.equal(createHash('sha256').update(bytes).digest('hex'), file.sha256, file.path);
    if (file.path.endsWith('.js')) new vm.Script(bytes.toString());
  }
});

test('stale proposals show a warning and disable Apply', () => {
  const h = harness();
  h.run(`state.data.pending_proposals=[{id:'prop-old',stale:true,before:{anchors:[]},after:{anchors:[{id:'work'}]},patch:{anchors:[{id:'work'}]}}]; renderPrefsProposals();`);
  const html = h.element('#prefsProposals').innerHTML;
  assert.match(html, /data-prop-apply="prop-old" disabled/);
  assert.match(html, /Profile changed/);
});

test('proposal shows exact full nested before and after, including removals', () => {
  const h = harness();
  h.run(`prefs.profile={anchors:[{id:'work', adjustments:[{max_minutes:15,delta:0.3}]}], risk_caps:{noise:{cap:4}}, categories:[]};
    state.data.pending_proposals=[{id:'prop-a', actor:'llm:test', patch:{anchors:[{id:'new',adjustments:[{max_minutes:null,delta:-0.7}]}],risk_caps:{noise:{cap:2}}}}]; renderPrefsProposals();`);
  const html = h.element('#prefsProposals').innerHTML;
  for (const text of ['Before', 'After', 'max_minutes', 'delta', '0.3', '-0.7', 'work', 'new', 'risk_caps']) assert.ok(html.includes(text), text);
  assert.doesNotMatch(html, /anchors changed/);
});
