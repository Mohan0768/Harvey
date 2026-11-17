let currentTab = 'today';
let companyDrill = false;      // true while viewing a single company's contacts
let _companies = [], _prospects = [], _campaigns = [];
let _gmailSecretSaved = false;
let _serperSaved = false;
let _signals = null;           // last /api/signals payload, for the cohort builder
let _desk = { items: [], i: 0, editing: null };

// ── Appearance ──
//
// Three states, not two: "auto" follows the OS and is the default, so the
// dashboard matches the rest of your machine until you deliberately override
// it. Stored per-browser; nothing is sent anywhere.

const THEMES = ['auto', 'light', 'dark'];

function applyTheme(mode) {
  const root = document.documentElement;
  if (mode === 'auto') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', mode);
  const btn = document.getElementById('theme-btn');
  if (btn) btn.textContent = mode;
}

function cycleTheme() {
  const now = localStorage.getItem('harvey-theme') || 'auto';
  const next = THEMES[(THEMES.indexOf(now) + 1) % THEMES.length];
  try { localStorage.setItem('harvey-theme', next); } catch { /* private mode */ }
  applyTheme(next);
}

(function initTheme() {
  let saved = 'auto';
  try { saved = localStorage.getItem('harvey-theme') || 'auto'; } catch { /* ignore */ }
  applyTheme(THEMES.includes(saved) ? saved : 'auto');
})();

// ── Utilities ──

function escHtml(s) {
  if (s === null || s === undefined || s === '') return '';
  return String(s).replace(/[&<>"']/g, ch => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]
  ));
}

// ── One status vocabulary ──
//
// Harvey's tables each carry their own words for state: a prospect is `new`,
// a campaign is `draft`, an email is `pending_review`, a signal is `proposed`.
// Five of those mean "waiting on you" and the old dashboard styled every one
// differently. Everything now resolves through this map, so a status reads the
// same way no matter which table it came out of.
//
// tone: waiting (needs a human) | active (in flight) | good | bad | idle
const STATUS = {
  // prospects
  new:            ['New', 'active'],
  contacted:      ['Contacted', 'active'],
  replied:        ['Replied', 'good'],
  interested:     ['Interested', 'good'],
  meeting:        ['Meeting booked', 'note'],
  not_interested: ['Not interested', 'bad'],
  bounced:        ['Bounced', 'bad'],
  // campaigns
  draft:          ['Draft', 'waiting'],
  active:         ['Active', 'good'],
  completed:      ['Completed', 'idle'],
  paused:         ['Paused', 'bad'],
  // conversations
  open:           ['Open', 'active'],
  closed:         ['Closed', 'idle'],
  closed_won:     ['Won', 'good'],
  closed_lost:    ['Lost', 'bad'],
  objection:      ['Objection', 'waiting'],
  // outbox
  pending_review: ['Waiting on you', 'waiting'],
  approved:       ['Approved', 'good'],
  scheduled:      ['Scheduled', 'active'],
  sent:           ['Sent', 'good'],
  failed:         ['Failed', 'bad'],
  rejected:       ['Rejected', 'idle'],
  cancelled:      ['Cancelled', 'idle'],
  // signals
  proposed:       ['Waiting on you', 'waiting'],
  confirmed:      ['Confirmed', 'good'],
  // email deliverability
  verified:       ['Verified', 'good'],
  risky:          ['Catch-all', 'waiting'],
  guess:          ['Unverified', 'idle'],
  invalid:        ['Invalid', 'bad'],
  // runs
  running:        ['Running', 'active'],
  stale:          ['Stale', 'bad'],
};

function statusMeta(status) {
  const key = String(status || '').toLowerCase().replace(/[^a-z0-9]+/g, '_');
  const hit = STATUS[key];
  if (hit) return { label: hit[0], tone: hit[1] };
  const label = String(status || 'unknown').replace(/_/g, ' ');
  return { label: label.charAt(0).toUpperCase() + label.slice(1), tone: 'idle' };
}

function badge(status) {
  const m = statusMeta(status);
  return '<span class="badge t-' + m.tone + '">' + escHtml(m.label) + '</span>';
}

function formatDate(d) {
  if (!d) return '';
  try {
    const dt = new Date(d);
    if (isNaN(dt)) return escHtml(d);
    return dt.toLocaleString('en-US', {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
  } catch { return escHtml(d); }
}

function emptyState(glyph, title, copy) {
  return '<div class="empty"><div class="glyph">' + glyph + '</div>' +
    '<div class="title">' + title + '</div>' +
    '<div class="copy">' + copy + '</div></div>';
}

function offlineState() {
  return emptyState('&#9888;', 'Dashboard can\'t reach the server',
    'The dashboard process may have stopped. Restart it with <b>harvey dashboard</b> and refresh this page.');
}

async function api(path, opts) {
  try {
    const r = await fetch(path, opts);
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

function showToast(msg, type) {
  const t = document.createElement('div');
  t.className = 'toast ' + type;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2600);
}

function toggleVisibility(inputId) {
  const el = document.getElementById(inputId);
  el.type = el.type === 'password' ? 'text' : 'password';
}

// ── Tabs ──

function showTab(id, btn) {
  currentTab = id;
  if (id === 'companies') companyDrill = false;
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  if (btn) btn.classList.add('active');
  loadCurrentTab();
}

function loadCurrentTab() {
  switch (currentTab) {
    case 'today': loadToday(); loadSetupStatus(); loadRuns(); loadTodayActivity(); break;
    case 'signals': loadSignals(); break;
    case 'discover': loadDiscoverProviders(); break;
    case 'companies': if (!companyDrill) loadCompanies(); break;
    case 'prospects': loadProspects(); break;
    case 'campaigns': loadCampaigns(); break;
    case 'outbox': loadOutbox(); break;
    case 'conversations': loadConversations(); break;
    case 'activity': loadActivity(); break;
    case 'usage': loadUsage(); break;
    case 'settings': loadSettings(); break;
    case 'controls': loadHarveyStatus(); loadLogs(); break;
  }
}

// ── Today: what needs a human ──

async function loadToday() {
  const data = await api('/api/today');
  const el = document.getElementById('today-queue');
  if (!data) { el.innerHTML = offlineState(); return; }

  navCount('nav-today', (data.items || []).filter(i => i.tone !== 'good').length);
  navCount('nav-outbox', (data.stats || {}).outbox_pending || 0);
  renderFigures(data.stats || {});

  const items = data.items || [];
  if (!items.length) {
    el.innerHTML = '<div class="queue-clear">' +
      '<div class="glyph">&#10003;</div><div>' +
      '<div class="qtitle">Nothing needs you</div>' +
      '<div class="qdetail">Harvey has everything it needs. Anything that requires a ' +
      'decision — an email to approve, a signal to confirm — shows up here.</div>' +
      '</div></div>';
    return;
  }

  el.innerHTML = '<div class="queue">' + items.map(it =>
    '<div class="queue-item ' + escHtml(it.tone || 'warn') + '">' +
      '<div class="qtext">' +
        '<div class="qtitle">' + escHtml(it.title) + '</div>' +
        '<div class="qdetail">' + escHtml(it.detail) + '</div>' +
      '</div>' +
      '<button class="btn btn-secondary btn-sm" onclick="goTab(\'' + escHtml(it.tab) + '\')">' +
        escHtml(it.action) + '</button>' +
    '</div>'
  ).join('') + '</div>';
}

function renderFigures(stats) {
  const el = document.getElementById('today-figures');
  if (!el) return;
  const row = (label, value) =>
    '<div class="figure"><span class="k">' + label + '</span>' +
    '<span class="v' + (value ? '' : ' zero') + '">' + value + '</span></div>';

  el.innerHTML = '<div class="figures">' +
    row('Companies', stats.companies || 0) +
    row('Contacts', stats.prospects || 0) +
    row('Signals on', stats.signals_confirmed || 0) +
    row('Not yet read', stats.unprofiled || 0) +
    row('Awaiting approval', stats.outbox_pending || 0) +
    row('Scheduled', stats.outbox_approved || 0) +
    row('Live conversations', stats.open_conversations || 0) +
  '</div>';
}

async function loadTodayActivity() {
  const el = document.getElementById('today-activity');
  if (!el) return;
  const data = await api('/api/activity');
  if (!Array.isArray(data) || !data.length) {
    el.innerHTML = '<p class="muted" style="font-size:12.5px">' +
      'Nothing yet. Every action Harvey takes shows up here.</p>';
    return;
  }
  el.innerHTML = '<div class="activity-feed">' + data.slice(0, 12).map(a =>
    '<div class="activity-item">' +
      '<span class="time">' + formatDate(a.created_at) + '</span>' +
      '<span class="agent">' + escHtml(a.agent) + '</span>' +
      '<span class="action">' + escHtml(String(a.action_type).replace(/_/g, ' ')) + '</span>' +
    '</div>').join('') + '</div>';
}

function navCount(id, n) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = n > 0 ? n : '';
  el.className = 'nav-count' + (n > 0 ? ' on' : '');
}

// Jump to a tab from a link that isn't itself a nav button.
function goTab(id) {
  const btns = [...document.querySelectorAll('nav button')];
  const btn = btns.find(b => (b.getAttribute('onclick') || '').includes("'" + id + "'"));
  showTab(id, btn);
}

async function loadRuns() {
  const el = document.getElementById('today-runs');
  const block = document.getElementById('today-runs-block');
  if (!el) return;
  const runs = await api('/api/runs');
  if (!Array.isArray(runs) || !runs.length) {
    if (block) block.style.display = 'none';
    el.innerHTML = '';
    return;
  }
  if (block) block.style.display = '';

  // A rail is 300px wide — a seven-column table does not belong here.
  el.innerHTML = '<div class="figures">' + runs.slice(0, 6).map(r =>
    '<div class="figure" style="align-items:flex-start">' +
      '<span class="k">' + escHtml(r.stage) +
        '<br><span class="muted" style="font-size:11px">' +
        formatDate(r.started_at) + '</span></span>' +
      '<span style="text-align:right;flex:none">' + badge(r.status) +
        '<br><span class="muted" style="font-family:var(--mono);font-size:11px">' +
        (r.records || 0) + ' rec &middot; ' +
        (r.cost_usd ? '$' + Number(r.cost_usd).toFixed(4) : 'free') + '</span></span>' +
    '</div>').join('') + '</div>';
}

// ── Setup checklist (lives on Today, and disappears once it's done) ──

async function loadSetupStatus() {
  const el = document.getElementById('today-setup');
  if (!el) return;
  const data = await api('/api/setup-status');
  if (!data || !data.checks) { el.innerHTML = ''; return; }

  const pct = data.percent || 0;
  // Finished setup disappears completely rather than greeting you forever.
  if (pct === 100) { el.innerHTML = ''; return; }

  const renderCheck = (c, optional) =>
    '<div class="check-item">' +
      (c.done ? '<span class="check-icon done">&#10003;</span>'
              : '<span class="check-icon pending"></span>') +
      '<div class="check-info">' +
        '<div class="check-label ' + (c.done ? 'done' : '') + '">' + escHtml(c.label) +
          (optional ? '<span class="optional-tag">optional</span>' : '') + '</div>' +
        (!c.done ? '<div class="check-help">' + escHtml(c.help) + '</div>' : '') +
      '</div></div>';

  const required = data.checks.filter(c => c.required);
  const optional = data.checks.filter(c => !c.required);

  el.innerHTML =
    '<div class="rail-block">' +
      '<div class="subhead">Setup &mdash; ' + pct + '%</div>' +
      '<div class="progress-bar" style="margin-bottom:14px">' +
        '<div class="progress-fill yellow" style="width:' + pct + '%"></div></div>' +
      required.map(c => renderCheck(c, false)).join('') +
      (optional.length
        ? '<details style="margin-top:10px"><summary class="muted" ' +
          'style="font-size:12px">' + optional.length + ' optional</summary>' +
          '<div style="margin-top:6px">' +
          optional.map(c => renderCheck(c, true)).join('') + '</div></details>'
        : '') +
    '</div>';
}

// ── Signals: Harvey proposes, you confirm ──

async function loadSignals() {
  const data = await api('/api/signals');
  const sumEl = document.getElementById('signals-summary');
  const grpEl = document.getElementById('signals-groups');
  if (!data || data.error) { grpEl.innerHTML = offlineState(); sumEl.innerHTML = ''; return; }
  _signals = data;

  const s = data.summary || {};
  navCount('nav-signals', s.proposed || 0);
  sumEl.innerHTML = '<div class="sig-summary">' +
    ['confirmed', 'proposed', 'rejected'].map(k =>
      '<div class="sig-stat ' + k + '"><div class="n">' + (s[k] || 0) + '</div>' +
      '<div class="k">' + (k === 'proposed' ? 'awaiting you' : k) + '</div></div>'
    ).join('') +
    '</div>';

  // Start Discovery sits with the stats it reports against.
  renderStartDiscovery(await api('/api/discover/providers'));

  grpEl.innerHTML = (data.groups || []).map(g => {
    const codes = g.signals.map(x => x.code);
    const undecided = g.signals.filter(x => x.status === 'proposed').length;
    return '<div class="sig-group">' +
      '<div class="sig-group-head"><div>' +
        '<h3>' + escHtml(g.label) + '</h3><p>' + escHtml(g.blurb) + '</p>' +
      '</div>' +
      (undecided
        ? '<div style="display:flex;gap:6px;flex-shrink:0">' +
            '<button class="btn btn-primary btn-sm" onclick=\'setSignals(' +
              JSON.stringify(codes) + ", \"confirmed\")'>Confirm all " + undecided + '</button>' +
            '<button class="btn btn-secondary btn-sm" onclick=\'setSignals(' +
              JSON.stringify(codes) + ", \"rejected\")'>Skip all</button>" +
          '</div>'
        : '') +
      '</div>' +
      '<div class="card" style="padding:4px 0">' + g.signals.map(sigRow).join('') + '</div>' +
    '</div>';
  }).join('');

  renderCohortBuilder();
}

function sigRow(sig) {
  const free = /free|included/i.test(sig.cost_note || '');
  const costCls = free ? 'free' : (/\$/.test(sig.cost_note || '') ? 'paid' : '');
  const seen = sig.companies
    ? '<span class="cost-chip">seen on ' + sig.companies +
      (sig.companies === 1 ? ' company' : ' companies') + ' so far</span>' : '';
  const floor = sig.confidence_floor > 0
    ? '<span class="cost-chip">only recorded above ' +
      Math.round(sig.confidence_floor * 100) + '% confidence</span>' : '';

  const decide = sig.status === 'confirmed'
    ? '<button class="btn btn-secondary btn-sm" onclick="setSignals([\'' + sig.code +
        '\'],\'rejected\')">Turn off</button>'
    : sig.status === 'rejected'
      ? '<button class="btn btn-secondary btn-sm" onclick="setSignals([\'' + sig.code +
          '\'],\'confirmed\')">Turn on</button>'
      : '<button class="btn btn-primary btn-sm" onclick="setSignals([\'' + sig.code +
          '\'],\'confirmed\')">Confirm</button>' +
        '<button class="btn btn-secondary btn-sm" onclick="setSignals([\'' + sig.code +
          '\'],\'rejected\')">Skip</button>';

  return '<div class="sig-row ' + (sig.status === 'rejected' ? 'rejected' : '') + '">' +
    '<div class="sig-main">' +
      '<div class="sig-label">' + escHtml(sig.label || sig.code) +
        '<span class="sig-code">' + escHtml(sig.code) + '</span></div>' +
      '<div class="sig-desc">' + escHtml(sig.description) + '</div>' +
      '<div class="sig-meta">' +
        '<span class="cost-chip ' + costCls + '">' + escHtml(sig.cost_note || 'cost unknown') + '</span>' +
        floor + seen + badge(sig.status) +
      '</div>' +
    '</div>' +
    '<div class="sig-decide">' + decide + '</div>' +
  '</div>';
}

async function setSignals(codes, status) {
  const data = await api('/api/signals/status', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({codes: codes, status: status}),
  });
  if (data && data.success) {
    showToast(
      status === 'confirmed'
        ? 'Confirmed ' + data.changed + ' signal' + (data.changed === 1 ? '' : 's') +
          ' — Harvey will collect ' + (data.changed === 1 ? 'it' : 'them') + ' from now on.'
        : data.changed + ' signal' + (data.changed === 1 ? '' : 's') + ' turned off.',
      'success');
  } else {
    showToast('Could not update that signal.', 'error');
  }
  loadSignals();
}

// ── Discover: pick a source, price it, then run it ──

let _provider = null;
let _discoverPoll = null;

async function loadDiscoverProviders() {
  loadDiscoverConfigs();
  const el = document.getElementById('discover-providers');
  const data = await api('/api/discover/providers');
  if (!data || !data.providers) { el.innerHTML = offlineState(); return; }

  _provider = _provider || data.selected || data.default;
  el.innerHTML = '<div class="prov-grid">' + data.providers.map(p => {
    const ready = p.configured
      ? '<span class="badge t-good">ready</span>'
      : '<span class="badge t-waiting">needs a key</span>';
    return '<div class="prov-card ' + (p.key === _provider ? 'selected' : '') +
      '" onclick="pickProvider(\'' + p.key + '\')">' +
      '<div class="prov-head"><h3>' + escHtml(p.label) + '</h3>' + ready + '</div>' +
      '<div class="blurb">' + escHtml(p.blurb) + '</div>' +
      '<div class="row"><span class="k">Cost</span><span class="v">' +
        escHtml(p.cost_note) + '</span></div>' +
      '<div class="row"><span class="k">Free</span><span class="v">' +
        escHtml(p.free_tier) + '</span></div>' +
      (p.needs_key
        ? '<div class="row"><span class="k">Setup</span><span class="v">' +
          escHtml(p.env_keys.join(', ')) + ' in .env &middot; ' +
          '<a href="' + escHtml(p.signup_url) + '" target="_blank" rel="noopener">get a key</a>' +
          '</span></div>'
        : '') +
      (p.caveat ? '<div class="caveat">' + escHtml(p.caveat) + '</div>' : '') +
    '</div>';
  }).join('') + '</div>';

  const running = data.running;
  document.getElementById('disc-stop').style.display = running ? '' : 'none';
  if (running && !_discoverPoll) {
    _discoverPoll = setInterval(loadDiscoverProviders, 4000);
  } else if (!running && _discoverPoll) {
    clearInterval(_discoverPoll);
    _discoverPoll = null;
    // A finished run leaves the button stuck on "Running…" otherwise.
    const btn = document.getElementById('disc-run');
    btn.disabled = true;
    btn.textContent = 'Estimate first';
    showToast('Discovery finished.', 'success');
  }
  renderDiscoverResult(data.last_report, running);
}

function pickProvider(key) {
  _provider = key;
  document.getElementById('disc-run').disabled = true;
  document.getElementById('disc-run').textContent = 'Estimate first';
  document.getElementById('discover-estimate').innerHTML = '';
  loadDiscoverProviders();
}

function discoverBody() {
  const raw = document.getElementById('disc-cities').value.trim();
  return {
    provider: _provider,
    // Semicolons or newlines, never commas: "Denver, CO" is one city.
    cities: raw ? raw.split(/[;\n]/).map(c => c.trim()).filter(Boolean) : [],
    // Categories are a real query axis now, not just whatever harvey.yaml said.
    terms: splitList('disc-terms'),
    depth: Number(document.getElementById('disc-depth').value) || 30,
    limit: Number(document.getElementById('disc-limit').value) || 100,
    max_spend: Number(document.getElementById('disc-cap').value) || 1,
  };
}

async function estimateDiscovery() {
  const el = document.getElementById('discover-estimate');
  el.innerHTML = '<p class="muted" style="font-size:13px">Pricing it…</p>';
  const data = await api('/api/discover/estimate', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(discoverBody()),
  });
  if (!data || data.error) {
    el.innerHTML = '<div class="test-result error">' +
      escHtml((data && data.error) || 'Could not estimate.') + '</div>';
    return;
  }

  const cap = discoverBody().max_spend;
  const over = data.estimated_cost > cap;
  el.innerHTML = '<div class="estimate-box">' +
    '<div class="amount ' + (data.free ? 'free' : 'paid') + '">' +
      (data.free ? 'Free' : '$' + data.estimated_cost.toFixed(4)) + '</div>' +
    '<div class="muted" style="font-size:13px;margin-top:2px">' +
      data.query_count + ' quer' + (data.query_count === 1 ? 'y' : 'ies') +
      (data.free ? '' : ' &middot; cap is $' + cap.toFixed(2)) + '</div>' +
    (over ? '<div class="test-result error" style="margin-top:12px">' +
      'Over your cap. Raise the cap or narrow the search.</div>' : '') +
    '<div class="query-list">' +
      data.queries.slice(0, 40).map(escHtml).join('<br>') +
      (data.queries.length > 40 ? '<br>… and ' + (data.queries.length - 40) + ' more' : '') +
    '</div></div>';

  const btn = document.getElementById('disc-run');
  btn.disabled = over;
  btn.textContent = over ? 'Over cap'
    : (data.free ? 'Run — free' : 'Run — spend up to $' + data.estimated_cost.toFixed(2));
}

async function runDiscovery() {
  const btn = document.getElementById('disc-run');
  btn.disabled = true;
  btn.textContent = 'Running…';
  const data = await api('/api/discover/run', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(discoverBody()),
  });
  if (data && data.success) {
    showToast('Discovery started — ' + data.queries + ' queries.', 'success');
  } else {
    showToast((data && data.message) || 'Could not start.', 'error');
    btn.disabled = false;
  }
  loadDiscoverProviders();
}

async function stopDiscovery() {
  const data = await api('/api/discover/stop', {method: 'POST'});
  showToast(data && data.success ? 'Stopping after the current query.'
                                 : 'Could not stop.', data ? 'success' : 'error');
  loadDiscoverProviders();
}

function renderDiscoverResult(report, running) {
  const el = document.getElementById('discover-result');
  if (running) {
    el.innerHTML = '<div class="card"><h2>Running…</h2>' +
      '<p class="muted" style="font-size:13px">Harvey is working through the ' +
      'queries. Results land in Companies, and the run log is on Today.</p></div>';
    return;
  }
  if (!report) { el.innerHTML = ''; return; }

  const stat = (label, value, tone) =>
    '<div class="stat-card"><div class="label">' + label + '</div>' +
    '<div class="value"' + (tone ? ' style="color:var(--' + tone + ')"' : '') + '>' +
    value + '</div></div>';

  el.innerHTML = '<div class="subhead">Last run</div>' +
    '<div class="stats-grid">' +
      stat('New companies', report.new_companies || 0, 'accent') +
      stat('Already known', report.known_companies || 0) +
      stat('Observations', report.observations || 0) +
      stat('Filtered as junk', report.junk || 0) +
      stat('Actual cost', report.actual_cost ? '$' + report.actual_cost.toFixed(4) : 'free') +
    '</div>' +
    (report.stopped ? '<div class="test-result error" style="margin-top:14px">' +
      'Stopped early: ' + escHtml(report.stopped) + '</div>' : '') +
    ((report.errors || []).length
      ? '<div class="card" style="margin-top:14px"><h2>Problems</h2>' +
        (report.errors || []).slice(0, 8).map(e =>
          '<div class="check-help">' + escHtml(e) + '</div>').join('') + '</div>'
      : '');
}

// ── Cohort builder: a prospect list is a query ──

let _cohort = { require: new Set(), exclude: new Set() };

function renderCohortBuilder() {
  const el = document.getElementById('cohort-builder');
  if (!el || !_signals) return;

  const confirmed = (_signals.groups || [])
    .flatMap(g => g.signals)
    .filter(s => s.status === 'confirmed');

  if (!confirmed.length) {
    el.innerHTML = '<p class="muted" style="font-size:13px">' +
      'Confirm some signals above and they become the building blocks here.</p>';
    document.getElementById('cohort-result').innerHTML = '';
    return;
  }

  const col = (title, key, hint) =>
    '<div><div class="subhead" style="margin-top:0">' + title +
      ' <span class="muted" style="font-weight:400;text-transform:none;letter-spacing:0">' +
      hint + '</span></div><div class="cohort-grid">' +
    confirmed.map(s =>
      '<label class="cohort-pick"><input type="checkbox" ' +
        (_cohort[key].has(s.code) ? 'checked ' : '') +
        'onchange="toggleCohort(\'' + key + '\',\'' + s.code + '\',this.checked)">' +
        '<span>' + escHtml(s.label || s.code) + '</span>' +
        '<span class="n">' + (s.companies || 0) + '</span></label>'
    ).join('') + '</div></div>';

  el.innerHTML = col('Must have', 'require', '&mdash; every company in the cohort carries all of these') +
    '<div style="height:18px"></div>' +
    col('Must not have', 'exclude', '&mdash; disqualifiers');
}

function toggleCohort(key, code, on) {
  if (on) _cohort[key].add(code); else _cohort[key].delete(code);
  // A company can't be both required and excluded on the same signal.
  const other = key === 'require' ? 'exclude' : 'require';
  if (on) _cohort[other].delete(code);
  runCohort();
  renderCohortBuilder();
}

async function runCohort() {
  const el = document.getElementById('cohort-result');
  if (!el) return;
  const require = [..._cohort.require], exclude = [..._cohort.exclude];
  if (!require.length) { el.innerHTML = ''; return; }

  const data = await api('/api/cohort', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({require: require, exclude: exclude}),
  });
  if (!data) { el.innerHTML = ''; return; }

  let html = '<div style="border-top:1px solid var(--border);margin-top:20px;padding-top:20px">' +
    '<div class="cohort-size">' + data.size + '</div>' +
    '<div class="muted" style="font-size:13px;margin-top:2px">compan' +
      (data.size === 1 ? 'y matches' : 'ies match') + ' this cohort right now</div>';

  if (data.companies && data.companies.length) {
    html += '<div class="table-card" style="margin-top:16px"><table><thead><tr>' +
      '<th>Company</th><th>Domain</th><th>Industry</th><th>Location</th>' +
      '</tr></thead><tbody>' +
      data.companies.slice(0, 50).map(c =>
        '<tr><td>' + escHtml(c.name) + '</td><td class="muted">' + escHtml(c.domain) + '</td>' +
        '<td class="muted">' + escHtml(c.industry) + '</td>' +
        '<td class="muted">' + escHtml(c.location) + '</td></tr>'
      ).join('') + '</tbody></table></div>';
    if (data.size > 50) {
      html += '<p class="muted" style="font-size:12px;margin-top:10px">Showing the first 50.</p>';
    }
  } else if (data.size === 0) {
    html += '<p class="muted" style="font-size:13px;margin-top:12px">' +
      'No company carries all of those yet. Either loosen the cohort, or run ' +
      'prospecting to collect more.</p>';
  }
  el.innerHTML = html + '</div>';
}

// ── Settings ──

async function loadSettings() {
  const data = await api('/api/settings');
  if (!data) return;

  // Where Harvey thinks. Populated before the export-only early return
  // below, because thinking is needed whether or not Harvey also sends.
  const setVal = (id, v) => {
    const el = document.getElementById(id);
    if (el) el.value = v || '';
  };
  const brainSel = document.getElementById('brain-provider');
  if (brainSel && data.brain_provider) brainSel.value = data.brain_provider;
  const keyEl = document.getElementById('brain-api-key');
  if (keyEl) {
    keyEl.value = '';
    keyEl.placeholder = data.anthropic_api_key_set
      ? 'Saved - enter a new key to change' : 'sk-ant-...';
  }
  brainProviderChanged();
  setVal('gc-project', data.gc_project);
  setVal('gc-workspace', data.gc_workspace);
  setVal('gc-credentials', data.gc_credentials);
  loadBrainState();

  // Active provider indicator
  const prov = data.provider || 'instantly';
  const provEl = document.getElementById('active-provider');
  if (provEl) provEl.textContent = prov;

  // Secret fields never echo a value — show a "saved" placeholder instead.
  const savedPh = (id, isSet, base) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.value = '';
    el.placeholder = isSet ? 'Saved — enter new value to change' : base;
  };
  const tag = (id, ok, okLabel) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.className = 'email-tag ' + (ok ? 'verified' : 'guess');
    el.textContent = ok ? okLabel : 'not set';
  };

  // Export-only setup: hide the sending forms rather than prompt for
  // credentials that nothing reads.
  const sending = data.email_sending_enabled !== false;
  const show = (id, on) => {
    const el = document.getElementById(id);
    if (el) el.style.display = on ? '' : 'none';
  };
  show('sending-settings', sending);
  show('verification-settings', sending);
  show('export-only-note', !sending);
  // Connecting a mailbox is how you LEAVE export-only mode, so this card is
  // always shown. Its fields must therefore be filled in BEFORE the
  // early return below, or the one card you can always see is the one card
  // that never gets its saved values back.
  show('gmail-integration', true);
  const gid = document.getElementById('gmail-id');
  if (gid) gid.value = data.gmail_client_id || '';
  _gmailSecretSaved = !!data.gmail_client_secret_set;
  savedPh('gmail-secret', data.gmail_client_secret_set, 'Enter client secret');
  loadGmailState();

  // Gmail auth status chip
  const g = document.getElementById('gmail-status');
  if (g) {
    if (data.gmail_authorized) { g.className = 'email-tag verified'; g.textContent = 'authorized'; }
    else if (data.gmail_client_secret_set) { g.className = 'email-tag risky'; g.textContent = 'press Connect Gmail'; }
    else { g.className = 'email-tag guess'; g.textContent = 'not set up'; }
  }

  _serperSaved = !!data.serper_api_key_set;
  savedPh('serper-key', data.serper_api_key_set, '2,500 searches free, no card');
  const dks = document.getElementById('discovery-keys-state');
  if (dks) {
    dks.innerHTML = data.serper_api_key_set
      ? '<div class="test-result success">Serper key saved. Discovery can search Google.</div>'
      : '<div class="test-result error">No search key. Discovery falls back to ' +
        'OpenStreetMap, which cannot find universities and will return nothing.</div>';
  }

  if (!sending) return;

  // Non-secret values repopulate
  document.getElementById('smtp-host').value = data.smtp_host || '';
  document.getElementById('smtp-port').value = data.smtp_port || '';
  document.getElementById('smtp-user').value = data.smtp_username || '';

  savedPh('smtp-pass', data.smtp_password_set, 'Enter password / app password');
  savedPh('reoon-key', data.reoon_api_key_set, '600 free/mo — reoon.com/email-verifier');
  tag('reoon-status', data.reoon_api_key_set, 'set');
}

function saveGmail() {
  const idEl = document.getElementById('gmail-id');
  const secEl = document.getElementById('gmail-secret');
  const id = ((idEl && idEl.value) || '').trim();
  const sec = (secEl && secEl.value) || '';
  // Refuse an empty id instead of posting it: sending GMAIL_CLIENT_ID=""
  // overwrote a saved one, which is how a save could leave you worse off
  // than before while still showing a success toast.
  if (!id) { showToast('Paste the client ID first.', 'error'); return; }
  if (!sec && !_gmailSecretSaved) {
    showToast('Paste the client secret first.', 'error'); return;
  }
  const payload = {GMAIL_CLIENT_ID: id};
  if (sec) payload.GMAIL_CLIENT_SECRET = sec;
  saveEnv(payload, 'Saved. Now press Connect Gmail to authorise.')
    .then(loadSettings).then(loadGmailState);
}

function saveSmtp() {
  const payload = {
    SMTP_HOST: document.getElementById('smtp-host').value.trim(),
    SMTP_PORT: document.getElementById('smtp-port').value.trim(),
    SMTP_USERNAME: document.getElementById('smtp-user').value.trim(),
  };
  const pass = document.getElementById('smtp-pass').value;
  if (pass) payload.SMTP_PASSWORD = pass;
  saveEnv(payload, 'SMTP settings saved.').then(loadSettings);
}

function saveDiscoveryKeys() {
  const el = document.getElementById('serper-key');
  const key = ((el && el.value) || '').trim();
  // Refuse an empty save rather than posting "" over a stored key, which is
  // how a credential gets quietly wiped by a hopeful click.
  if (!key) {
    showToast(_serperSaved
      ? 'The key is already saved. Paste a new one to replace it.'
      : 'Paste your Serper key first.', 'error');
    return;
  }
  saveEnv({SERPER_API_KEY: key}, 'Discovery key saved.')
    .then(loadSettings)
    .then(() => { if (typeof loadDiscoverProviders === 'function') loadDiscoverProviders(); });
}

function saveVerifiers() {
  const r = document.getElementById('reoon-key').value;
  if (!r) { showToast('Enter a Reoon key first.', 'error'); return; }
  saveEnv({REOON_API_KEY: r}, 'Verification key saved.').then(loadSettings);
}

// Only the fields the chosen provider actually uses. Showing a Google
// Cloud project box to someone pasting an API key invites them to fill in
// settings that will never be read.
function brainProviderChanged() {
  const how = ((document.getElementById('brain-provider') || {}).value) || '';
  const show = (id, on) => {
    const el = document.getElementById(id);
    if (el) el.style.display = on ? '' : 'none';
  };
  show('brain-key-row', how === 'anthropic');
  show('brain-gc-rows', how === 'google_cloud');
}

async function saveBrainAccess() {
  const v = id => ((document.getElementById(id) || {}).value || '').trim();
  const how = v('brain-provider') || 'anthropic';
  const payload = {};

  if (how === 'anthropic') {
    const key = v('brain-api-key');
    // Blank means "leave what is stored alone", so a saved key survives a
    // save made to change something else.
    if (key) payload.ANTHROPIC_API_KEY = key;
  } else if (how === 'google_cloud') {
    if (!v('gc-project') || !v('gc-workspace')) {
      showToast('The project and workspace ids are both required.', 'error');
      return;
    }
    payload.ANTHROPIC_GOOGLE_CLOUD_PROJECT = v('gc-project');
    payload.ANTHROPIC_GOOGLE_CLOUD_WORKSPACE_ID = v('gc-workspace');
    payload.GOOGLE_APPLICATION_CREDENTIALS = v('gc-credentials');
  }

  if (Object.keys(payload).length) await saveEnv(payload, 'Saved.');
  const d = await contactApi('/api/brain/provider', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({provider: how})
  });
  if (d && d.success) showToast('Saved. Restart Harvey to apply.', 'success');
  else showToast((d && d.message) || 'Could not save the provider.', 'error');
  const el = document.getElementById('brain-api-key');
  if (el) { el.value = ''; el.placeholder = 'Saved - enter a new key to change'; }
  loadBrainState();
}

// A real round trip, because every other signal lies. The settings can all
// look right while the role is missing or the key file points nowhere, and
// the only way to know is to ask the model something.
async function testBrain() {
  const out = document.getElementById('brain-test-result');
  if (out) out.innerHTML = '<div class="test-result pending">Asking the model...</div>';
  const d = await contactApi('/api/brain/test', {method: 'POST'});
  if (!out) return;
  if (d && d.success) {
    out.innerHTML = '<div class="test-result success">Connected. ' +
      escHtml(d.provider || '') + ' answered: ' + escHtml(d.reply || '') + '</div>';
  } else {
    out.innerHTML = '<div class="test-result error">' +
      escHtml((d && d.message) || 'No answer.') + '</div>';
  }
  loadBrainState();
}

async function loadBrainState() {
  const el = document.getElementById('brain-state');
  if (!el) return;
  const d = await api('/api/brain/status');
  if (!d || !d.success) { el.innerHTML = ''; return; }
  el.innerHTML = '<div class="test-result ' +
    (d.ready ? 'success' : 'pending') + '">' +
    escHtml(d.detail || '') + '</div>';
}

async function saveEnv(payload, okMsg) {
  const data = await api('/api/settings/env', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  if (data && data.success) showToast(okMsg, 'success');
  else showToast((data && data.message) || 'Save failed — is the dashboard still running?', 'error');
}

// ── Controls ──

async function loadHarveyStatus() {
  const data = await api('/api/harvey/status');
  const headerDot = document.getElementById('header-dot');
  const headerText = document.getElementById('header-status-text');

  if (!data) {
    headerDot.className = 'status-dot offline';
    headerText.textContent = 'Offline';
    return;
  }
  const running = !!data.running;

  headerDot.className = 'status-dot ' + (running ? 'running' : 'stopped');
  headerText.textContent = running ? 'Harvey is running' : 'Harvey is stopped';
  document.getElementById('control-dot').className = 'dot ' + (running ? 'running' : 'stopped');
  const label = document.getElementById('control-label');
  label.className = 'label ' + (running ? 'running' : 'stopped');
  label.textContent = running ? 'Running' : 'Stopped';

  const meta = document.getElementById('control-meta');
  if (running && data.pid) {
    let info = 'PID ' + escHtml(String(data.pid));
    if (data.started_at) info += ' &middot; started ' + formatDate(data.started_at);
    meta.innerHTML = info;
  } else {
    meta.innerHTML = 'Harvey wakes every few minutes, does what needs doing, and sleeps.';
  }

  document.getElementById('btn-start').style.display = running ? 'none' : '';
  document.getElementById('btn-stop').style.display = running ? '' : 'none';
}

async function startHarvey() {
  const btn = document.getElementById('btn-start');
  btn.disabled = true;
  const data = await api('/api/harvey/start', {method: 'POST'});
  if (data && data.success) showToast('Harvey started.', 'success');
  else showToast((data && data.message) || 'Failed to start.', 'error');
  btn.disabled = false;
  loadHarveyStatus();
}

async function stopHarvey() {
  const btn = document.getElementById('btn-stop');
  btn.disabled = true;
  const data = await api('/api/harvey/stop', {method: 'POST'});
  if (data && data.success) showToast('Harvey stopped.', 'success');
  else showToast((data && data.message) || 'Failed to stop.', 'error');
  btn.disabled = false;
  loadHarveyStatus();
}

async function loadLogs() {
  const data = await api('/api/harvey/logs');
  const el = document.getElementById('log-viewer');
  if (data && data.lines && data.lines.length) {
    const stick = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
    el.textContent = data.lines.join('\n');
    if (stick) el.scrollTop = el.scrollHeight;
  } else {
    el.textContent = 'No logs yet. Start Harvey to see activity.';
  }
}

// ── Pipeline data ──

async function loadStats() {
  const grid = document.getElementById('stats-grid');
  const data = await api('/api/stats');
  if (!data) { grid.innerHTML = offlineState(); return; }
  if (data.error) {
    grid.innerHTML = emptyState('&#9670;', 'No pipeline data yet',
      'Start Harvey from the <b>Controls</b> tab and it will begin prospecting, writing, and sending on its own.');
    return;
  }
  const p = data.prospects || {}, c = data.campaigns || {}, v = data.conversations || {};
  const chips = (map) => {
    const entries = Object.entries(map || {});
    if (!entries.length) return '<span class="chip muted">none yet</span>';
    return entries.map(([k, n]) =>
      '<span class="chip">' + escHtml(k) + ' <b>' + escHtml(String(n)) + '</b></span>'
    ).join('');
  };
  const card = (label, value, breakdown) =>
    '<div class="stat-card"><div class="label">' + label + '</div>' +
    '<div class="value">' + value + '</div>' +
    '<div class="breakdown">' + breakdown + '</div></div>';

  grid.innerHTML =
    card('Prospects', p.total || 0, chips(p.by_status)) +
    card('Campaigns', c.total || 0, chips(c.by_status)) +
    card('Conversations', v.total || 0, chips(v.by_status)) +
    card('Actions Logged', data.actions_total || 0, brainCallChips(data));
}

// Calls today per subscription — each labelled with the provider it ran on.
function brainCallChips(data) {
  const chip = (label, n) => '<span class="chip">' + label + ' calls today <b>' + escHtml(String(n || 0)) + '</b></span>';
  const codex = data.codex_calls_today || 0;
  const st = data.brain_status || {};
  // A paused brain must not look healthy: say so, and why.
  const paused = st.status === 'paused'
    ? '<span class="chip" title="' + escHtml(st.reason || '') + '">' +
      escHtml(st.provider === 'codex' ? 'Codex' : 'Brain') + ' <b>paused</b>: ' +
      escHtml((st.reason || '').slice(0, 120)) + '</span>'
    : '';
  if (data.brain_provider === 'codex') {
    return paused + chip('Codex', codex) + (data.claude_calls_today ? chip('Claude', data.claude_calls_today) : '');
  }
  return paused + chip('Claude', data.claude_calls_today) + (codex ? chip('Codex', codex) : '');
}

function fmtTokens(n) {
  n = n || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(n);
}


function emailTag(p) {
  if (!p.email) return '';
  // Fall back to the legacy boolean for rows predating email_status.
  const status = p.email_status || (p.email_verified ? 'verified' : 'guess');
  if (!STATUS[status]) return '';
  const m = statusMeta(status);
  return ' <span class="badge t-' + m.tone + '" style="margin-left:6px">' + escHtml(m.label) + '</span>';
}

// Quota gauges for the ACTIVE provider only: Claude's are the numbers
// `/usage` shows in Claude Code; Codex's are the ChatGPT plan's limits.
// When they can't be read, say so rather than hiding the card.
function quotaCardHtml(data) {
  const provider = data && data.quota_provider;
  if (!provider) return '';
  const title = provider === 'codex' ? 'Codex (ChatGPT plan) Quota' : 'Claude Subscription Quota';
  let html = '<h2>' + escHtml(title) + '</h2>';
  if (!data.quota || !Object.keys(data.quota).length) {
    const why = provider === 'codex'
      ? "Quota unavailable: Harvey couldn't read your ChatGPT plan's limits (check codex login, the Codex CLI version, and network)."
      : "Quota unavailable: Harvey couldn't read your Claude subscription's limits (check claude login and network).";
    return html + '<p class="muted">' + escHtml(why) + ' Until it can, budgets count Harvey\'s own calls.</p>';
  }
  const labels = {five_hour: '5-hour window', seven_day: 'Weekly', primary: 'Primary window',
                  secondary: 'Secondary window', limit_reached: 'Usage limit reached'};
  for (const [key, w] of Object.entries(data.quota)) {
    const pct = Math.min(100, Math.max(0, w.utilization || 0));
    const color = pct >= 80 ? 'yellow' : 'green';
    const resets = w.resets_at ? 'resets ' + formatDate(w.resets_at) : '';
    html += '<div class="progress-wrap">' +
      '<div class="progress-label">' +
        '<span class="text">' + escHtml(labels[key] || key) + (resets ? ' &middot; ' + escHtml(resets) : '') + '</span>' +
        '<span class="pct">' + pct.toFixed(0) + '%</span>' +
      '</div>' +
      '<div class="progress-bar"><div class="progress-fill ' + color + '" style="width:' + pct + '%"></div></div>' +
    '</div>';
  }
  return html;
}

async function loadUsage() {
  const data = await api('/api/usage');
  const statsEl = document.getElementById('usage-stats');
  if (!data) { statsEl.innerHTML = offlineState(); return; }

  const quotaEl = document.getElementById('usage-quota');
  const qHtml = quotaCardHtml(data);
  quotaEl.innerHTML = qHtml;
  quotaEl.style.display = qHtml ? 'block' : 'none';

  // Totals cards — usage-first (calls is the headline; tokens as chips).
  // No dollars: on a subscription plan usage isn't billed per token.
  const t = data.totals || {};
  const card = (label, p) => {
    p = p || {};
    return '<div class="stat-card"><div class="label">' + label + '</div>' +
      '<div class="value">' + (p.calls || 0) + '</div>' +
      '<div class="breakdown">' +
        '<span class="chip">calls</span>' +
        '<span class="chip">out <b>' + fmtTokens(p.output_tokens) + '</b></span>' +
        '<span class="chip">in <b>' + fmtTokens(p.input_tokens) + '</b></span>' +
        '<span class="chip">cached <b>' + fmtTokens(p.cache_read_tokens) + '</b></span>' +
      '</div></div>';
  };
  statsEl.innerHTML = card('Today', t.today) + card('Last 7 Days', t.week) + card('Last 30 Days', t.month);

  // Daily bars — output tokens per day (the work done)
  const dailyEl = document.getElementById('usage-daily');
  const days = data.by_day || [];
  if (days.length) {
    const maxOut = Math.max(...days.map(d => d.output_tokens || 0), 1);
    let dHtml = '<h2>Daily Output Tokens (30 days)</h2>';
    for (const d of days.slice(-30)) {
      const pct = Math.max(2, (d.output_tokens || 0) / maxOut * 100);
      dHtml += '<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;font-size:12px">' +
        '<span class="muted" style="width:78px;flex-shrink:0;font-family:var(--mono)">' + escHtml(d.day || '') + '</span>' +
        '<div style="flex:1;background:rgba(255,255,255,0.05);border-radius:99px;height:10px;overflow:hidden">' +
          '<div style="width:' + pct + '%;height:100%;border-radius:99px;background:linear-gradient(90deg,var(--accent-deep),var(--accent))"></div>' +
        '</div>' +
        '<span style="width:130px;text-align:right;font-variant-numeric:tabular-nums">' + fmtTokens(d.output_tokens) + ' out' +
          ' <span class="muted">&middot; ' + (d.calls || 0) + ' calls</span></span>' +
      '</div>';
    }
    dailyEl.innerHTML = dHtml;
    dailyEl.style.display = 'block';
  } else {
    dailyEl.style.display = 'none';
  }

  // Breakdown tables — calls + tokens, no cost column
  const tablesEl = document.getElementById('usage-tables');
  const table = (title, rows, keyName) => {
    if (!rows || !rows.length) return '';
    let h = '<div class="card"><h2>' + title + '</h2><div class="table-card"><table><thead><tr>' +
      '<th>' + keyName + '</th><th>Calls</th><th>Input</th><th>Output</th><th>Cache read</th>' +
      '</tr></thead><tbody>';
    for (const r of rows) {
      h += '<tr><td>' + escHtml(String(r[keyName.toLowerCase()] || '')) + '</td>' +
        '<td>' + (r.calls || 0) + '</td>' +
        '<td class="muted">' + fmtTokens(r.input_tokens) + '</td>' +
        '<td>' + fmtTokens(r.output_tokens) + '</td>' +
        '<td class="muted">' + fmtTokens(r.cache_read_tokens) + '</td></tr>';
    }
    return h + '</tbody></table></div></div>';
  };

  const anyRows = (data.by_agent || []).length || (data.by_task || []).length;
  if (!anyRows) {
    tablesEl.innerHTML = emptyState('&#9680;', 'No usage recorded yet',
      'Once Harvey starts making model calls (Claude or Codex), every one is logged here with exact calls and tokens by provider, agent and task.');
  } else {
    const providers = data.by_provider || [];
    const showProviders = providers.length > 1 || (providers.length === 1 && providers[0].provider !== 'claude');
    tablesEl.innerHTML =
      (showProviders ? table('By Provider (30 days)', providers, 'Provider') : '') +
      table('By Agent (30 days)', data.by_agent, 'Agent') +
      table('By Task (30 days)', data.by_task, 'Task') +
      table('By Model (30 days)', data.by_model, 'Model');
  }
}

// ── Outbox: a decisions desk, not a wall of drafts ──
//
// Approving mail is a queue of one-at-a-time judgements. Showing all of them
// stacked invites a single "approve all" reflex, which is exactly the review
// the approval ladder exists to prevent. One email fills the pane; the rest
// wait in the rail.

// Outbox view state. The desk reviews one email at a time; this is
// everything around it: what is being looked at, and what is selected.
var _ob = {q: '', role: '', status: '', selected: {}};

// Unclassified needs a value of its own. An <option value=""> for it would
// be a second empty-valued option after "Every category", and the browser
// would show whichever came last as the selection.
var OB_NO_ROLE = '__none__';

function obMatch(r) {
  if (_ob.role === OB_NO_ROLE) { if (r.role) return false; }
  else if (_ob.role && (r.role || '') !== _ob.role) return false;
  if (!_ob.q) return true;
  var hay = [r.recipient_name, r.to_email, r.subject, r.recipient_company,
             r.role_label].join(' ').toLowerCase();
  return hay.indexOf(_ob.q) !== -1;
}

function obSetSearch(v) { _ob.q = (v || '').trim().toLowerCase(); loadOutbox(); }
function obSetRole(v) { _ob.role = v || ''; loadOutbox(); }
function obSetStatus(v) { _ob.status = v || ''; loadOutbox(); }

function obToggle(id, on) {
  if (on) _ob.selected[id] = true; else delete _ob.selected[id];
  var n = Object.keys(_ob.selected).length;
  var el = document.getElementById('ob-selcount');
  if (el) el.textContent = n ? n + ' selected' : '';
}

function obSelectedIds() { return Object.keys(_ob.selected); }

function obExport(scope) {
  // Export is a download, so it goes through the browser rather than fetch:
  // the server already knows how to filter, and the file should land in the
  // downloads folder like any other.
  var p = [];
  if (scope === 'selected') {
    var ids = obSelectedIds();
    if (!ids.length) { showToast('Nothing selected.', 'error'); return; }
    p.push('ids=' + encodeURIComponent(ids.join(',')));
  } else {
    if (_ob.role) p.push('role=' + encodeURIComponent(_ob.role));
    if (_ob.status) p.push('status=' + encodeURIComponent(_ob.status));
  }
  window.location = '/api/export/outbox.csv' + (p.length ? '?' + p.join('&') : '');
}

async function loadOutbox() {
  const data = await api('/api/outbox');
  const banner = document.getElementById('outbox-banner');
  const desk = document.getElementById('outbox-desk');
  const list = document.getElementById('outbox-list');
  const actions = document.getElementById('outbox-actions');
  if (!data) { desk.innerHTML = offlineState(); list.innerHTML = ''; return; }

  const all = [].concat(data.drafts || [], data.pending || [],
                        data.approved || [], data.sending || [],
                        data.sent || [], data.failed || []);
  const roleCounts = {};
  all.forEach(r => {
    const k = r.role || '';
    if (!roleCounts[k]) roleCounts[k] = {label: r.role_label || 'Unclassified', n: 0};
    roleCounts[k].n++;
  });

  const pending = (data.pending || []).filter(obMatch);
  _desk.items = pending;
  if (_desk.i >= pending.length) _desk.i = Math.max(0, pending.length - 1);
  navCount('nav-outbox', (data.pending || []).length);

  actions.innerHTML =
    '<span class="muted" id="ob-selcount" style="font-size:12px;align-self:center"></span>' +
    '<button class="btn btn-secondary btn-sm" onclick="obExport(\'selected\')">Export selected</button>' +
    '<button class="btn btn-secondary btn-sm" onclick="obExport(\'filtered\')">Export all</button>' +
    (pending.length
      ? '<button class="btn btn-secondary btn-sm" onclick="outboxRegenerateAll()">' +
        (obNarrowed() ? 'Regenerate ' + pending.length + ' shown'
                      : 'Regenerate all ' + pending.length) + '</button>' : '') +
    (pending.length
      ? '<button class="btn btn-secondary btn-sm" onclick="outboxRejectAll()">' +
        (obNarrowed() ? 'Reject ' + pending.length + ' shown'
                      : 'Reject all ' + pending.length) + '</button>' : '') +
    (pending.length && !data.paused
      ? '<button class="btn btn-primary btn-sm" onclick="outboxApproveAll()">' +
        (obNarrowed() ? 'Approve ' + pending.length + ' shown'
                      : 'Approve all ' + pending.length) + '</button>' : '') +
    (data.paused
      ? '<button class="btn btn-secondary btn-sm" onclick="sendingToggle(\'resume\')">' +
        'Resume sending</button>'
      : '<button class="btn btn-secondary btn-sm" onclick="sendingToggle(\'pause\')">' +
        'Pause all sending</button>');

  // The kill switch gets a banner only when it's actually on.
  banner.innerHTML =
    (data.paused
      ? '<div class="card" style="border-color:var(--red);margin-bottom:16px">' +
          '<h2 style="color:var(--red)">&#9888; Sending is paused</h2>' +
          '<p style="color:var(--text-2);font-size:13px">' + escHtml(data.paused) +
          '. Approved mail stays queued until you resume.</p></div>'
      : '') +
    '<div class="card" style="margin-bottom:16px"><div style="display:flex;gap:8px;flex-wrap:wrap">' +
      '<input class="form-input" id="ob-search" placeholder="Search name, address, subject" ' +
        'style="flex:1;min-width:200px" value="' + escHtml(_ob.q) +
        '" oninput="obSetSearch(this.value)">' +
      '<select class="form-input" style="max-width:220px" onchange="obSetRole(this.value)">' +
        ['<option value="">Every category</option>'].concat(
          Object.keys(roleCounts).sort().map(k => {
            var v = k || OB_NO_ROLE;
            return '<option value="' + escHtml(v) + '"' +
              (_ob.role === v ? ' selected' : '') + '>' +
              escHtml(roleCounts[k].label) + ' (' + roleCounts[k].n + ')</option>';
          })
        ).join('') +
      '</select>' +
      '<select class="form-input" style="max-width:180px" onchange="obSetStatus(this.value)">' +
        ['', 'draft', 'pending_review', 'approved', 'sent', 'failed'].map(s =>
          '<option value="' + s + '"' + (_ob.status === s ? ' selected' : '') + '>' +
          (s ? escHtml(statusMeta(s).label) : 'Every status') + '</option>').join('') +
      '</select>' +
    '</div></div>';

  desk.innerHTML = pending.length ? renderDesk(pending, _desk.i) :
    '<div class="card">' + emptyState('&#10003;',
      (data.pending || []).length ? 'Nothing matches that filter' : 'Nothing to review',
      (data.pending || []).length
        ? 'Clear the search or category filter to see the rest.'
        : 'Every draft Harvey writes lands here first. Approve one and it sends on schedule.') +
    '</div>';

  const act = r =>
    '<button class="btn btn-secondary btn-sm" onclick="previewEmail(\'' + r.id +
    '\')">Preview</button>';
  const pick = r =>
    '<input type="checkbox" onchange="obToggle(\'' + r.id + '\', this.checked)"' +
    (_ob.selected[r.id] ? ' checked' : '') + '>';

  const table = (title, rows, cols, key) => {
    rows = (rows || []).filter(obMatch);
    if (_ob.status && key !== _ob.status) return '';
    if (!rows.length) return '';
    let h = '<div class="card"><h2>' + title + ' <span class="muted" style="font-weight:400">(' +
      rows.length + ')</span></h2><div class="table-card"><table><thead><tr><th></th>' +
      cols.map(c => '<th>' + c[0] + '</th>').join('') + '</tr></thead><tbody>';
    for (const r of rows) {
      h += '<tr><td>' + pick(r) + '</td>' + cols.map(c => '<td' + (c[2] ? ' class="muted"' : '') + '>' +
        (c[3] ? c[1](r) : escHtml(String(c[1](r) ?? ''))) + '</td>').join('') + '</tr>';
    }
    return h + '</tbody></table></div></div>';
  };

  list.innerHTML =
    table('Saved drafts', data.drafts, [
      ['To', r => r.recipient_name ? r.recipient_name + ' <' + r.to_email + '>' : r.to_email],
      ['Category', r => r.role_label || '', true],
      ['Subject', r => r.subject],
      ['Created', r => formatDate(r.created_at), true],
      ['', r => act(r) + ' <button class="btn btn-secondary btn-sm" onclick="outboxPromote(\'' +
        r.id + '\')">Move to Outbox</button>', false, true],
    ], 'draft') +
    table('Waiting for review', data.pending, [
      ['To', r => r.recipient_name ? r.recipient_name + ' <' + r.to_email + '>' : r.to_email],
      ['Category', r => r.role_label || '', true],
      ['Subject', r => r.subject],
      ['Created', r => formatDate(r.created_at), true],
      ['', r => act(r), false, true],
    ], 'pending_review') +
    table('Approved &amp; scheduled', data.approved, [
      ['To', r => r.to_email], ['Category', r => r.role_label || '', true],
      ['Subject', r => r.subject],
      ['Sends', r => formatDate(r.send_at), true],
      ['', r => act(r), false, true],
    ], 'approved') +
    table('Sending now', data.sending, [
      ['To', r => r.to_email], ['Subject', r => r.subject],
      ['Started', r => formatDate(r.updated_at), true],
    ], 'sending') +
    table('Recently sent', data.sent, [
      ['To', r => r.recipient_name ? r.recipient_name + ' <' + r.to_email + '>' : r.to_email],
      ['Subject', r => r.subject],
      ['Gmail message', r => r.message_id || '-', true],
      ['Thread', r => r.thread_ref || '-', true],
      ['Sent', r => formatDate(r.sent_at), true],
      ['', r => act(r), false, true],
    ], 'sent') +
    table('Didn\'t send', data.failed, [
      ['To', r => r.to_email], ['Status', r => badge(r.status), false, true],
      ['Reason', r => r.error, true], ['Updated', r => formatDate(r.updated_at), true],
      ['', r => act(r), false, true],
    ], 'failed');

  if (!list.innerHTML) {
    list.innerHTML = '<div class="card">' + emptyState('&#128269;', 'No emails match',
      'Nothing in the Outbox matches that search, category or status.') + '</div>';
  }
}

// ── Preview ──
//
// Shows the email exactly as stored and composes nothing. A preview that
// regenerated would show you something other than what you are approving,
// which defeats the only purpose the screen has.
async function previewEmail(id) {
  const root = document.getElementById('modal-root');
  if (!root) return;
  root.innerHTML =
    '<div class="modal-backdrop" onclick="modalBackdropClick(event)">' +
    '<div class="modal" role="dialog" aria-modal="true" aria-label="Email preview">' +
    '<h2>Email preview</h2><div id="pv-body">' +
    '<div class="test-result pending">Loading...</div></div></div></div>';
  _modalEscHandler = e => { if (e.key === 'Escape') closeModal(); };
  document.addEventListener('keydown', _modalEscHandler);

  const d = await contactApi('/api/outbox/item/' + encodeURIComponent(id));
  const body = document.getElementById('pv-body');
  if (!body) return;
  if (!d || !d.success) {
    body.innerHTML = '<div class="modal-error show">' +
      escHtml((d && d.message) || 'Could not load that email.') + '</div>';
    return;
  }
  renderPreview(d.item);
}

function renderPreview(it) {
  const body = document.getElementById('pv-body');
  if (!body) return;
  const row = (k, v) => v
    ? '<tr><td class="muted" style="padding:2px 12px 2px 0;white-space:nowrap">' +
      k + '</td><td style="padding:2px 0">' + v + '</td></tr>' : '';
  const editable = ['draft', 'pending_review', 'failed'].indexOf(it.status) !== -1;

  body.innerHTML =
    '<div class="card" style="margin-bottom:12px"><table style="font-size:13px">' +
      row('To', escHtml(it.recipient_name || '') +
          (it.recipient_name ? ' &lt;' + escHtml(it.to_email) + '&gt;' : escHtml(it.to_email))) +
      row('Organisation', escHtml(it.recipient_company || '')) +
      row('Title', escHtml(it.recipient_title || '')) +
      row('Category', escHtml(it.role_label || '')) +
      row('Type', escHtml(it.email_type || '')) +
      row('Status', badge(it.status)) +
      row('Created', escHtml(formatDate(it.created_at))) +
      row('Sent', escHtml(formatDate(it.sent_at))) +
      row('Gmail message', escHtml(it.message_id || '')) +
    '</table></div>' +
    (it.error ? '<div class="test-result error">Last attempt failed: ' +
       escHtml(it.error) + '</div>' : '') +
    '<div class="card">' +
      '<div class="subject" style="margin-bottom:8px">' +
        escHtml(it.subject || '(no subject)') + '</div>' +
      '<div class="body" style="white-space:pre-wrap">' + escHtml(it.body || '') + '</div>' +
    '</div>' +
    '<div class="btn-group" style="margin-top:12px">' +
      (editable
        ? '<button class="btn btn-secondary" onclick="previewRegenerate(\'' + it.id +
          '\')">Regenerate</button>' +
          '<button class="btn btn-secondary" onclick="previewDelete(\'' + it.id +
          '\')">Delete</button>'
        : '') +
      '<button class="btn btn-secondary" onclick="obExportOne(\'' + it.id +
        '\')">Export</button>' +
      '<button class="btn btn-secondary" onclick="closeModal()">Close</button>' +
    '</div>';
}

function obExportOne(id) {
  window.location = '/api/export/outbox.csv?ids=' + encodeURIComponent(id);
}

async function previewRegenerate(id) {
  const body = document.getElementById('pv-body');
  if (body) body.innerHTML = '<div class="test-result pending">Writing a fresh version...</div>';
  const d = await contactApi('/api/outbox/' + encodeURIComponent(id) + '/regenerate',
                             {method: 'POST'});
  if (!d || !d.success) {
    showToast((d && d.message) || 'Could not regenerate.', 'error');
    previewEmail(id);
    return;
  }
  showToast('Rewritten.', 'success');
  previewEmail(id);
  loadOutbox();
}

async function previewDelete(id) {
  var go = await askConfirm('Delete this draft? This cannot be undone.',
                            'Delete');
  if (!go) return;
  const d = await contactApi('/api/outbox/' + encodeURIComponent(id), {method: 'DELETE'});
  if (!d || !d.success) {
    showToast((d && d.message) || 'Could not delete.', 'error');
    return;
  }
  delete _ob.selected[id];
  showToast('Deleted.', 'success');
  closeModal();
  loadOutbox();
}

function renderDesk(items, i) {
  const cur = items[i];
  const rail = items.map((it, n) =>
    '<div class="desk-item ' + (n === i ? 'active' : '') + '" onclick="deskGo(' + n + ')">' +
      '<div class="to">' + escHtml(it.to_email) + '</div>' +
      '<div class="sub">' + escHtml(it.subject || '(no subject)') + '</div>' +
    '</div>').join('');

  return '<div class="desk">' +
    '<div class="desk-list">' + rail + '</div>' +
    '<div class="desk-pane">' +
      '<div class="to-line">To <b>' +
        escHtml(cur.recipient_name || cur.to_email) + '</b>' +
        (cur.recipient_name ? ' &lt;' + escHtml(cur.to_email) + '&gt;' : '') +
        (cur.recipient_company ? ' &middot; ' + escHtml(cur.recipient_company) : '') +
        ' &middot; ' + escHtml(cur.email_type || 'Automated') +
        ' &middot; created ' + formatDate(cur.created_at) +
        ' &middot; ' + badge(cur.status) + '</div>' +
      (cur.error
        ? '<div class="test-result error">Last attempt failed: ' +
          escHtml(cur.error) + '</div>'
        : '') +
      (_desk.editing === cur.id
        ? '<div class="form-group"><label class="form-label" for="desk-subject">Subject</label>' +
            '<input class="form-input" id="desk-subject" value="' + escHtml(cur.subject || '') + '"></div>' +
          '<div class="form-group"><label class="form-label" for="desk-body">Body</label>' +
            '<textarea class="form-input" id="desk-body" rows="12" style="font-family:var(--mono);font-size:12.5px;line-height:1.6">' +
            escHtml(cur.body || '') + '</textarea></div>'
        : '<div class="subject">' + escHtml(cur.subject || '(no subject)') + '</div>' +
          '<div class="body">' + escHtml(cur.body) + '</div>') +
      '<div class="desk-actions">' +
        (_desk.editing === cur.id
          ? '<button class="btn btn-primary" onclick="deskSaveEdit(\'' + cur.id + '\')">Save draft</button>' +
            '<button class="btn btn-secondary" onclick="deskCancelEdit()">Cancel</button>'
          : '<button class="btn btn-primary" onclick="outboxAct(\'' + cur.id + '\',\'approve\')">' +
              'Approve &amp; send <kbd>A</kbd></button>' +
            '<button class="btn btn-secondary" onclick="outboxAct(\'' + cur.id + '\',\'reject\')">' +
              'Reject <kbd>R</kbd></button>' +
            '<button class="btn btn-secondary" onclick="deskEdit(\'' + cur.id + '\')">Edit</button>' +
            '<button class="btn btn-secondary" id="desk-regen" onclick="deskRegenerate(\'' +
              cur.id + '\')">Regenerate</button>') +
        '<span class="muted" style="font-size:12px;margin-left:auto">' +
          (i + 1) + ' of ' + items.length + '</span>' +
      '</div>' +
    '</div></div>';
}

function deskGo(n) {
  if (n < 0 || n >= _desk.items.length) return;
  _desk.i = n;
  document.getElementById('outbox-desk').innerHTML = renderDesk(_desk.items, n);
}

// Keyboard review. Ignored while typing into a field, so Settings still works.
document.addEventListener('keydown', e => {
  if (currentTab !== 'outbox' || e.metaKey || e.ctrlKey || e.altKey) return;
  const tag = (e.target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea' || e.target.isContentEditable) return;
  const cur = _desk.items[_desk.i];
  const k = e.key.toLowerCase();
  if (k === 'j') { e.preventDefault(); deskGo(_desk.i + 1); }
  else if (k === 'k') { e.preventDefault(); deskGo(_desk.i - 1); }
  else if (k === 'a' && cur) {
    e.preventDefault();
    // The button is labelled "Approve & send" and you are looking at it.
    // A bare keypress is not, so this one asks first.
    askConfirm('Send this email to ' + cur.to_email + ' now?', 'Send')
      .then(function (go) { if (go) outboxAct(cur.id, 'approve'); });
  }
  else if (k === 'r' && cur) { e.preventDefault(); outboxAct(cur.id, 'reject'); }
});

async function outboxAct(id, action) {
  // contactApi, not api: when a send fails the provider's own words are the
  // whole point of the message, and api() drops the body on a non-2xx.
  const data = await contactApi(
    '/api/outbox/' + encodeURIComponent(id) + '/' + action, {method: 'POST'});
  if (data && data.success) {
    showToast(action === 'approve'
      ? (data.message || 'Sent.') +
        (data.message_id ? ' Message ' + data.message_id : '')
      : 'Rejected.', 'success');
  } else {
    showToast((data && data.message) || 'Action failed.', 'error');
  }
  loadOutbox();
}

function obNarrowed() { return !!(_ob.q || _ob.role); }

// The ids currently on the review desk, which is the filtered list. Acting
// on anything else would mean a button labelled with one number doing its
// work to a different set of emails.
function obPendingIds() { return (_desk.items || []).map(r => r.id); }

async function obBulk(action, ids) {
  return contactApi('/api/outbox/bulk', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ids: ids, action: action})
  });
}

async function outboxApproveAll() {
  const ids = obPendingIds();
  if (!ids.length) return;
  const data = await obBulk('approve', ids);
  if (data && data.success) showToast('Approved ' + data.changed + ' email(s).', 'success');
  else showToast((data && data.message) || 'Approve-all failed.', 'error');
  loadOutbox();
}

async function outboxRegenerateAll() {
  const ids = obPendingIds();
  if (!ids.length) return;
  // Rewriting replaces text a human may have edited by hand, so this asks
  // even though nothing is sent and nothing is approved.
  const what = obNarrowed()
    ? 'the ' + ids.length + ' draft(s) currently shown'
    : 'all ' + ids.length + ' draft(s) waiting for review';
  var go = await askConfirm(
    'Rewrite ' + what + ' from the template? Any wording you edited by hand '
    + 'will be replaced. Nothing is sent.', 'Rewrite');
  if (!go) return;

  showToast('Rewriting ' + ids.length + ' draft(s)...', 'success');
  const data = await contactApi('/api/outbox/regenerate-all', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ids: ids})
  });
  if (data && data.success) {
    let msg = 'Rewrote ' + data.changed + ' draft(s).';
    if (data.skipped) msg += ' ' + data.skipped + ' skipped.';
    if (data.failed) msg += ' ' + data.failed + ' failed.';
    showToast(msg, data.failed ? 'error' : 'success');
    (data.notes || []).filter(noteWorthSaying).forEach(
      n => showToast(n, 'success'));
  } else {
    showToast((data && data.message) || 'Rewrite failed.', 'error');
  }
  loadOutbox();
}

async function outboxRejectAll() {
  const ids = obPendingIds();
  if (!ids.length) return;
  // Rejecting is one-way: a rejected email can no longer be approved or
  // sent. Approving in bulk only stages, so only this one asks.
  const what = obNarrowed()
    ? 'the ' + ids.length + ' email(s) currently shown'
    : 'all ' + ids.length + ' email(s) waiting for review';
  var go = await askConfirm(
    'Reject ' + what + '? Rejected emails cannot be approved or sent '
    + 'afterwards.', 'Reject');
  if (!go) return;
  const data = await obBulk('reject', ids);
  if (data && data.success) showToast('Rejected ' + data.changed + ' email(s).', 'success');
  else showToast((data && data.message) || 'Reject-all failed.', 'error');
  loadOutbox();
}

async function sendingToggle(action) {
  const data = await api('/api/sending/' + action, {method: 'POST'});
  if (data && data.success) showToast(action === 'pause' ? 'Sending paused.' : 'Sending resumed.', 'success');
  else showToast('Failed.', 'error');
  loadOutbox();
}

async function loadCompanies() {
  companyDrill = false;
  const el = document.getElementById('companies-list');
  const data = await api('/api/companies');
  if (!data) { el.innerHTML = offlineState(); return; }
  _companies = data;
  if (!data.length) {
    el.innerHTML = emptyState('&#9906;', 'No companies yet',
      'Harvey\'s Scout agent hasn\'t researched any companies. Finish <b>Setup</b>, then start Harvey from the <b>Controls</b> tab.');
    return;
  }
  let html = '<div class="table-card"><table><thead><tr><th>Company</th><th>Domain</th><th>Industry</th><th>Size</th><th>Location</th><th>Contacts</th><th>Source</th><th>Added</th></tr></thead><tbody>';
  data.forEach((c, i) => {
    const website = c.website || (c.domain ? 'https://' + c.domain : '');
    const nameLink = website
      ? '<a href="' + escHtml(website) + '" target="_blank" rel="noopener" onclick="event.stopPropagation()">' + escHtml(c.name) + '</a>'
      : escHtml(c.name);
    html += '<tr style="cursor:pointer" onclick="showCompanyContacts(' + i + ')">' +
      '<td>' + nameLink + '</td><td class="muted">' + escHtml(c.domain) + '</td><td>' + escHtml(c.industry) + '</td>' +
      '<td>' + escHtml(c.company_size) + '</td><td>' + escHtml(c.location) + '</td>' +
      '<td>' + (c.contact_count || 0) + '</td><td class="muted">' + escHtml(c.source) + '</td>' +
      '<td class="muted">' + formatDate(c.created_at) + '</td></tr>';
  });
  el.innerHTML = html + '</tbody></table></div>';
}

async function showCompanyContacts(index) {
  const company = _companies[index];
  if (!company) return;
  companyDrill = true;
  const el = document.getElementById('companies-list');
  const data = await api('/api/companies/' + encodeURIComponent(company.id) + '/contacts');
  let html = '<div class="card"><h2>' + escHtml(company.name) + ' — Contacts</h2>' +
    '<button class="btn btn-secondary btn-sm" onclick="loadCompanies()" style="margin-bottom:16px">&larr; Back to Companies</button>';
  if (!data || !data.length) {
    html += '<p style="color:var(--text-3);font-size:13px">No contacts found at this company yet.</p></div>';
  } else {
    html += '<div class="table-card"><table><thead><tr><th>Name</th><th>Title</th><th>Email</th><th>Phone</th><th>LinkedIn</th><th>Status</th><th>Source</th></tr></thead><tbody>';
    for (const p of data) {
      const emailIcon = emailTag(p);
      const phoneIcon = p.phone_verified ? ' <span class="verified">&#10003;</span>' : '';
      html += '<tr><td>' + escHtml(p.first_name) + ' ' + escHtml(p.last_name) + '</td>' +
        '<td>' + escHtml(p.title) + '</td><td>' + escHtml(p.email) + emailIcon + '</td>' +
        '<td>' + escHtml(p.phone) + phoneIcon + '</td>' +
        '<td>' + (p.linkedin_url ? '<a href="' + escHtml(p.linkedin_url) + '" target="_blank" rel="noopener">Profile</a>' : '') + '</td>' +
        '<td>' + badge(p.status) + '</td><td class="muted">' + escHtml(p.source) + '</td></tr>';
    }
    html += '</tbody></table></div></div>';
  }
  el.innerHTML = html;
}

async function submitFeedback(entityType, entityId, promptText) {
  const comment = prompt(promptText || 'Add your feedback:');
  if (!comment) return;
  const data = await api('/api/feedback', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({entity_type: entityType, entity_id: entityId, comment: comment})
  });
  if (data && data.success) showToast('Feedback saved. Harvey will take it into account.', 'success');
  else showToast((data && data.message) || 'Could not save feedback.', 'error');
}

function fbProspect(i) {
  const p = _prospects[i];
  if (p) submitFeedback('contact', p.id, 'Feedback on this contact:');
}

function fbCampaign(i) {
  const c = _campaigns[i];
  if (c) submitFeedback('campaign', c.id, 'Leave feedback on this campaign:');
}

async function findContacts() {
  const btn = document.getElementById('find-contacts-btn');
  const note = document.getElementById('find-contacts-note');
  const say = (cls, text) => {
    if (note) note.innerHTML = '<div class="test-result ' + cls + '">' + escHtml(text) + '</div>';
  };
  if (btn) { btn.disabled = true; btn.textContent = 'Reading sites...'; }
  say('pending', 'Looking for published staff pages on companies already found.');

  const d = await contactApi('/api/contacts/find', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({limit: 25})
  });
  if (!d || !d.success) {
    if (btn) { btn.disabled = false; btn.textContent = 'Find contacts'; }
    say('error', (d && d.message) || 'Could not start.');
    return;
  }
  say('pending', 'Reading ' + d.companies + ' site(s). This takes a minute or two.');

  // Poll rather than hold the request open: reading two dozen sites is
  // minutes of work, and a hung request tells you nothing on the way.
  const tick = setInterval(async () => {
    const s = await contactApi('/api/contacts/find-status');
    if (!s || s.running) return;
    clearInterval(tick);
    if (btn) { btn.disabled = false; btn.textContent = 'Find contacts'; }
    if (s.error) { say('error', s.error); return; }
    say(s.created ? 'success' : 'pending',
        s.message + ' Checked ' + s.companies + ' company site(s).');
    if (s.created) loadProspects();
  }, 4000);
}

async function loadProspects() {
  const el = document.getElementById('prospects-table');
  const data = await api('/api/prospects');
  if (!data) { el.innerHTML = offlineState(); return; }
  _prospects = data;
  if (!data.length) {
    el.innerHTML = emptyState('&#9673;', 'No contacts yet',
      'Import a spreadsheet or add a contact by hand from the <b>Discover</b> tab, or let the Scout agent find people matching your ICP.');
    const pager = document.getElementById('prospects-pager');
    if (pager) pager.innerHTML = '';
    return;
  }
  ctFillOptions();
  renderProspects();
}

async function loadCampaigns() {
  const el = document.getElementById('campaigns-list');
  const data = await api('/api/campaigns');
  if (!data) { el.innerHTML = offlineState(); return; }
  _campaigns = data;
  if (!data.length) {
    el.innerHTML = emptyState('&#9993;', 'No campaigns yet',
      'The Writer agent hasn\'t drafted any sequences. It kicks in automatically once Harvey has scored prospects to write for.');
    return;
  }
  let html = '';
  data.forEach((c, i) => {
    let stepsHtml = '';
    for (const step of (c.sequence || [])) {
      stepsHtml += '<div class="email-step"><div class="step-num">Email ' + escHtml(String(step.step || '?')) +
        (step.delay_days ? ' &middot; send after ' + escHtml(String(step.delay_days)) + ' days' : '') + '</div>' +
        '<div class="subject">' + escHtml(step.subject) + '</div>' +
        '<div class="body">' + escHtml(step.body) + '</div></div>';
    }
    const pc = (c.prospect_ids || []).length;
    html += '<div class="campaign-card"><h3>' + escHtml(c.name || 'Untitled Campaign') + '</h3>' +
      '<div class="meta">' + badge(c.status) + '<span>' + escHtml(c.channel || 'email') + '</span>' +
      '<span>' + pc + ' prospect' + (pc !== 1 ? 's' : '') + '</span><span>' + formatDate(c.created_at) + '</span>' +
      '<button class="btn btn-secondary btn-sm" onclick="fbCampaign(' + i + ')">Feedback</button></div>' +
      (stepsHtml || '<p style="color:var(--text-3);font-size:13px">No email steps in this campaign.</p>') + '</div>';
  });
  el.innerHTML = html;
}

async function loadConversations() {
  const el = document.getElementById('conversations-list');
  const data = await api('/api/conversations');
  if (!data) { el.innerHTML = offlineState(); return; }
  if (!data.length) {
    el.innerHTML = emptyState('&#9737;', 'No conversations yet',
      'No prospects have replied so far. When they do, the Handler agent classifies each reply and responds — every thread shows up here.');
    return;
  }
  let html = '';
  for (const c of data) {
    let threadHtml = '';
    for (const msg of (c.thread || [])) {
      const cls = msg.sender === 'harvey' ? 'sent' : 'received';
      threadHtml += '<div class="thread-msg ' + cls + '"><div class="sender">' + escHtml(msg.sender) +
        ' &middot; ' + formatDate(msg.timestamp) + '</div>' + escHtml(msg.content) + '</div>';
    }
    const name = [c.first_name, c.last_name].filter(Boolean).join(' ') || 'Unknown';
    html += '<div class="convo-card">' +
      (c.title ? '<div class="muted" style="font-size:12px">' +
        escHtml(c.title) + '</div>' : '') +
      '<h3>' + escHtml(name) +
      (c.company ? ' <span style="color:var(--text-3);font-weight:500">&mdash; ' + escHtml(c.company) + '</span>' : '') + '</h3>' +
      '<div class="meta">' + badge(c.status) + (c.intent ? badge(c.intent) : '') +
      '<span>' + escHtml(c.prospect_email || '') + '</span><span>' + formatDate(c.updated_at) + '</span></div>' +
      (threadHtml || '<p style="color:var(--text-3);font-size:13px">No messages in this thread yet.</p>') + '</div>';
  }
  el.innerHTML = html;
}

async function refreshConversations() {
  const btn = document.getElementById('convo-refresh');
  const note = document.getElementById('convo-refresh-note');
  if (btn) { btn.disabled = true; btn.textContent = 'Checking...'; }
  const d = await contactApi('/api/conversations/refresh', {method: 'POST'});
  if (btn) { btn.disabled = false; btn.textContent = 'Refresh replies'; }
  if (!d || !d.success) {
    if (note) note.innerHTML = '<div class="test-result error">' +
      escHtml((d && d.message) || 'Could not check for replies.') + '</div>';
    return;
  }
  if (note) {
    note.innerHTML = '<div class="test-result ' + (d.added ? 'success' : 'pending') +
      '">Checked ' + d.fetched + ' message(s): ' + d.added + ' new repl' +
      (d.added === 1 ? 'y' : 'ies') +
      (d.unmatched ? ', ' + d.unmatched + ' could not be matched to a thread' : '') +
      '.</div>';
  }
  loadConversations();
}

async function loadActivity() {
  const el = document.getElementById('activity-list');
  const data = await api('/api/activity');
  if (!data) { el.innerHTML = offlineState(); return; }
  if (!data.length) {
    el.innerHTML = emptyState('&#9202;', 'No activity yet',
      'Harvey hasn\'t taken any actions. Every prospect found, email written, and reply handled will appear here the moment it happens.');
    return;
  }
  let html = '<div class="activity-feed">';
  for (const a of data) {
    html += '<div class="activity-item"><span class="time">' + formatDate(a.created_at) + '</span>' +
      '<span class="agent">' + escHtml(a.agent) + '</span>' +
      '<span class="action">' + escHtml(a.action_type) + '</span></div>';
  }
  el.innerHTML = html + '</div>';
}

// ── Init & live refresh ──

loadToday();
loadSetupStatus();
loadRuns();
loadTodayActivity();
loadHarveyStatus();

// Agent status: quick poll
setInterval(loadHarveyStatus, 8000);

// Nav counts stay live wherever you are, so "something needs me" is visible
// from any tab without polling that tab's contents.
setInterval(async () => {
  if (document.hidden || currentTab === 'today') return;
  const data = await api('/api/today');
  if (!data) return;
  navCount('nav-today', (data.items || []).filter(i => i.tone !== 'good').length);
  navCount('nav-outbox', (data.stats || {}).outbox_pending || 0);
}, 20000);

// Data tabs: auto-refresh live views without clobbering anything in progress.
// Outbox, settings and help are deliberately excluded — re-rendering the desk
// under someone mid-decision loses their place, and settings may be mid-edit.
setInterval(() => {
  if (document.hidden) return;
  switch (currentTab) {
    case 'today': loadToday(); loadRuns(); loadTodayActivity(); break;
    case 'companies': if (!companyDrill) loadCompanies(); break;
    case 'prospects': loadProspects(); break;
    case 'campaigns': loadCampaigns(); break;
    case 'conversations': loadConversations(); break;
    case 'activity': loadActivity(); break;
    case 'usage': loadUsage(); break;
    case 'controls': loadLogs(); break;
  }
}, 15000);

// ── Contact import and manual entry ──
// Discovery only finds what its providers map. These two paths let a human
// bring the list instead; both write to the same prospects table.

// The shared api() helper returns null on any non-2xx, which throws away the
// server's explanation ("already in your contacts", "not a valid email").
// Contact writes need that text, so they use this instead.
async function contactApi(path, opts) {
  try {
    const r = await fetch(path, opts);
    let data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    if (data && typeof data === 'object') return data;
    return {success: r.ok, message: r.ok ? '' : 'Request failed (' + r.status + ').'};
  } catch (e) {
    return {success: false, message: 'Could not reach the dashboard server.'};
  }
}

let _importRows = [];

async function previewImport() {
  const input = document.getElementById('import-file');
  const box = document.getElementById('import-preview');
  const file = input.files && input.files[0];
  _importRows = [];
  if (!file) { box.innerHTML = ''; return; }

  box.innerHTML = '<div class="test-result pending">Reading ' + escHtml(file.name) + '&hellip;</div>';
  const form = new FormData();
  form.append('file', file);
  let data;
  try {
    const res = await fetch('/api/contacts/preview', {method: 'POST', body: form});
    data = await res.json();
  } catch (e) {
    box.innerHTML = '<div class="test-result error">Could not reach the dashboard server.</div>';
    return;
  }
  if (!data || !data.success) {
    box.innerHTML = '<div class="test-result error">' +
      escHtml((data && data.message) || 'Could not read that file.') + '</div>';
    return;
  }

  _importRows = data.rows || [];
  const c = data.counts || {};
  const mapped = Object.keys(data.mapped || {})
    .map(h => escHtml(h) + ' &rarr; ' + escHtml(data.mapped[h])).join(', ');

  let html = '<div class="subhead">Preview</div>' +
    '<p class="lede">' + c.valid + ' row(s) readable, ' + c.duplicates +
    ' already in your contacts, ' + c.failed + ' unusable.' +
    (data.truncated ? ' Only the first 5000 rows were read.' : '') + '</p>' +
    '<p class="lede">Columns matched: ' + (mapped || 'none') + '</p>';

  if (!_importRows.length) {
    html += '<div class="test-result error">Nothing in that file can be imported.</div>';
  } else {
    html += '<div class="table-card"><table><thead><tr><th>Name</th><th>Title</th>' +
      '<th>Company</th><th>Email</th><th>Phone</th><th></th></tr></thead><tbody>';
    _importRows.slice(0, 25).forEach(r => {
      html += '<tr><td>' + escHtml((r.first_name || '') + ' ' + (r.last_name || '')) + '</td>' +
        '<td>' + escHtml(r.title || '') + '</td><td>' + escHtml(r.company || '') + '</td>' +
        '<td>' + escHtml(r.email || '') + '</td><td>' + escHtml(r.phone || '') + '</td>' +
        '<td>' + (r.duplicate ? '<span class="email-tag risky">already here</span>' : '') +
        '</td></tr>';
    });
    html += '</tbody></table></div>';
    if (_importRows.length > 25) {
      html += '<p class="lede">Showing the first 25 of ' + _importRows.length + '.</p>';
    }
  }

  if ((data.failed || []).length) {
    html += '<p class="lede">Skipped rows: ' + data.failed.slice(0, 8)
      .map(f => 'row ' + f.row + ' (' + escHtml(f.reason) + ')').join(', ') +
      (data.failed.length > 8 ? ', and ' + (data.failed.length - 8) + ' more' : '') + '</p>';
  }

  if (_importRows.length) {
    html += '<div class="form-group" style="margin-top:10px">' +
      '<label class="form-label" for="import-dup">When the email is already in your contacts</label>' +
      '<select class="form-input" id="import-dup" style="max-width:320px">' +
      '<option value="skip">Skip it, keep what I have</option>' +
      '<option value="update">Update the existing contact</option></select></div>' +
      '<div class="btn-group"><button class="btn btn-primary" onclick="confirmImport()">Import ' +
      _importRows.length + ' contact(s)</button>' +
      '<button class="btn btn-secondary" onclick="cancelImport()">Cancel</button></div>';
  }
  box.innerHTML = html;
}

function cancelImport() {
  _importRows = [];
  document.getElementById('import-file').value = '';
  document.getElementById('import-preview').innerHTML = '';
}

async function confirmImport() {
  if (!_importRows.length) return;
  const sel = document.getElementById('import-dup');
  const box = document.getElementById('import-preview');
  box.innerHTML = '<div class="test-result pending">Importing&hellip;</div>';
  const data = await contactApi('/api/contacts/import', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({rows: _importRows, on_duplicate: sel ? sel.value : 'skip'})
  });
  if (!data || !data.success) {
    box.innerHTML = '<div class="test-result error">' +
      escHtml((data && data.message) || 'Import failed.') + '</div>';
    return;
  }
  box.innerHTML = '<div class="test-result success">Imported ' + data.added +
    ' new, updated ' + data.updated + ', skipped ' + data.skipped +
    ', failed ' + data.failed + '.</div>';
  showToast('Imported ' + data.added + ' contact(s).', 'success');
  document.getElementById('import-file').value = '';
  _importRows = [];
  loadProspects();
}

function clearManualContact() {
  ['mc-name', 'mc-title', 'mc-company', 'mc-email', 'mc-phone', 'mc-source']
    .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
  const s = document.getElementById('mc-status');
  if (s) s.value = 'new';
}

async function saveManualContact() {
  const val = id => (document.getElementById(id) || {}).value || '';
  const name = val('mc-name').trim();
  if (!name) { showToast('Enter a name first.', 'error'); return; }
  const data = await contactApi('/api/contacts', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      name: name, title: val('mc-title').trim(), company: val('mc-company').trim(),
      email: val('mc-email').trim(), phone: val('mc-phone').trim(),
      status: val('mc-status'), source: val('mc-source').trim() || 'manual'
    })
  });
  if (data && data.success) {
    showToast('Contact saved.', 'success');
    clearManualContact();
    loadProspects();
  } else {
    showToast((data && data.message) || 'Could not save that contact.', 'error');
  }
}

// ── Contacts: search, filter, edit, delete, pagination ──
// loadProspects fetches once; renderProspects re-draws from the cached list
// so typing in the search box does not hit the server on every keystroke.

let _ctPage = 1;
const CT_PER_PAGE = 50;

const CATEGORY_LABELS = {technical: 'Technical', hr: 'HR',
  marketing: 'Marketing', career_center: 'Career Center', '': 'Unclassified'};

function catLabel(c) { return CATEGORY_LABELS[c || ''] || c; }

function ctFilters() {
  const v = id => ((document.getElementById(id) || {}).value || '').trim();
  return {q: v('ct-search').toLowerCase(), status: v('ct-status'),
          source: v('ct-source'), category: v('ct-category'),
          sort: v('ct-sort') || 'desc'};
}

function ctFillOptions() {
  const el = document.getElementById('ct-category');
  if (el) {
    const keep = el.value;
    const cats = [...new Set(_prospects.map(p => p.category || ''))]
      .sort((a, b) => catLabel(a).localeCompare(catLabel(b)));
    el.innerHTML = '<option value="">All</option>' + cats.map(c =>
      '<option value="' + escHtml(c || '__none') + '">' + escHtml(catLabel(c)) +
      '</option>').join('');
    if (cats.map(c => c || '__none').indexOf(keep) !== -1) el.value = keep;
  }
  [['ct-status', 'status'], ['ct-source', 'source']].forEach(pair => {
    const el = document.getElementById(pair[0]);
    if (!el) return;
    const keep = el.value;
    const vals = [...new Set(_prospects.map(p => p[pair[1]]).filter(Boolean))].sort();
    el.innerHTML = '<option value="">All</option>' +
      vals.map(v => '<option value="' + escHtml(v) + '">' + escHtml(v) + '</option>').join('');
    if (vals.indexOf(keep) !== -1) el.value = keep;
  });
}

function ctMatching() {
  const f = ctFilters();
  let rows = _prospects.filter(p => {
    if (f.status && p.status !== f.status) return false;
    if (f.source && p.source !== f.source) return false;
    if (f.category) {
      const c = p.category || '__none';
      if (c !== f.category) return false;
    }
    if (!f.q) return true;
    const hay = ((p.first_name || '') + ' ' + (p.last_name || '') + ' ' +
                 (p.company || '') + ' ' + (p.email || '')).toLowerCase();
    return hay.indexOf(f.q) !== -1;
  });
  rows.sort((a, b) => {
    const x = a.created_at || '', y = b.created_at || '';
    return f.sort === 'asc' ? (x < y ? -1 : x > y ? 1 : 0) : (x > y ? -1 : x < y ? 1 : 0);
  });
  return rows;
}

function ctGoto(page) { _ctPage = page; renderProspects(); }

function renderProspects() {
  const el = document.getElementById('prospects-table');
  const pager = document.getElementById('prospects-pager');
  if (!el) return;
  const rows = ctMatching();
  const pages = Math.max(1, Math.ceil(rows.length / CT_PER_PAGE));
  if (_ctPage > pages) _ctPage = pages;
  const slice = rows.slice((_ctPage - 1) * CT_PER_PAGE, _ctPage * CT_PER_PAGE);

  if (!rows.length) {
    el.innerHTML = emptyState('&#9673;', 'No contacts match',
      _prospects.length ? 'Clear the search or filters to see all ' + _prospects.length + '.'
        : 'Import a spreadsheet or add one by hand from the <b>Discover</b> tab.');
    if (pager) pager.innerHTML = '';
    return;
  }

  let html = '<div class="table-card"><table><thead><tr><th>Name</th><th>Title</th>' +
    '<th>Company</th><th>Category</th><th>Email</th><th>Phone</th><th>Status</th><th>Source</th>' +
    '<th>Added</th><th>Actions</th></tr></thead><tbody>';
  slice.forEach(p => {
    const i = _prospects.indexOf(p);
    const emailV = p.email ? (escHtml(p.email) + emailTag(p)) : '';
    const phoneV = p.phone ? (escHtml(p.phone) + (p.phone_verified ? ' <span class="verified">&#10003;</span>' : '')) : '';
    html += '<tr><td>' + escHtml(p.first_name) + ' ' + escHtml(p.last_name) + '</td>' +
      '<td>' + escHtml(p.title) + '</td><td>' + escHtml(p.company) + '</td>' +
      '<td class="muted">' + escHtml(catLabel(p.category)) + '</td>' +
      '<td>' + emailV + '</td><td>' + phoneV + '</td><td>' + badge(p.status) + '</td>' +
      '<td class="muted">' + escHtml(p.source) + '</td>' +
      '<td class="muted">' + formatDate(p.created_at) + '</td>' +
      '<td><button class="btn btn-secondary btn-sm" onclick="openComposer(' + i + ')">Write</button> ' +
      '<button class="btn btn-secondary btn-sm" onclick="editContact(' + i + ')">Edit</button> ' +
      '<button class="btn btn-secondary btn-sm" onclick="fbProspect(' + i + ')">Feedback</button> ' +
      '<button class="btn btn-danger btn-sm" onclick="deleteContact(' + i + ')">Delete</button>' +
      '</td></tr>';
  });
  el.innerHTML = html + '</tbody></table></div>';

  if (pager) {
    const shown = 'Showing ' + (slice.length ? ((_ctPage - 1) * CT_PER_PAGE + 1) : 0) +
      '&ndash;' + ((_ctPage - 1) * CT_PER_PAGE + slice.length) + ' of ' + rows.length +
      (rows.length !== _prospects.length ? ' (filtered from ' + _prospects.length + ')' : '');
    let buttons = '';
    if (pages > 1) {
      buttons = ' <button class="btn btn-secondary btn-sm" onclick="ctGoto(' + (_ctPage - 1) +
        ')"' + (_ctPage <= 1 ? ' disabled' : '') + '>Previous</button>' +
        ' <span class="muted">Page ' + _ctPage + ' of ' + pages + '</span>' +
        ' <button class="btn btn-secondary btn-sm" onclick="ctGoto(' + (_ctPage + 1) +
        ')"' + (_ctPage >= pages ? ' disabled' : '') + '>Next</button>';
    }
    pager.innerHTML = '<p class="lede">' + shown + buttons + '</p>';
  }
}

// ── Edit dialog ──
// Replaces a stack of window.prompt() calls: one form, all fields visible,
// validated before anything is sent.

const CONTACT_STATUSES = ['new', 'contacted', 'replied', 'meeting', 'closed', 'lost'];
let _modalEscHandler = null;

function closeModal() {
  const root = document.getElementById('modal-root');
  if (root) root.innerHTML = '';
  if (_modalEscHandler) {
    document.removeEventListener('keydown', _modalEscHandler);
    _modalEscHandler = null;
  }
}

function modalBackdropClick(e) {
  if (e.target && e.target.classList.contains('modal-backdrop')) closeModal();
}

function editContact(i) {
  const p = _prospects[i];
  const root = document.getElementById('modal-root');
  if (!p || !root) return;
  const v = x => escHtml(x || '');
  const who = ((p.first_name || '') + ' ' + (p.last_name || '')).trim() || p.email || 'this contact';
  const statuses = CONTACT_STATUSES.map(s =>
    '<option value="' + s + '"' + (p.status === s ? ' selected' : '') + '>' + s + '</option>').join('');

  root.innerHTML =
    '<div class="modal-backdrop" onclick="modalBackdropClick(event)">' +
    '<div class="modal" role="dialog" aria-modal="true" aria-label="Edit contact">' +
    '<h2>Edit contact</h2>' +
    '<p class="modal-sub">' + escHtml(who) + '</p>' +
    '<div class="modal-error" id="ec-error"></div>' +
    '<div class="form-row">' +
    '<div class="form-group"><label class="form-label" for="ec-first">First name</label>' +
    '<input class="form-input" id="ec-first" value="' + v(p.first_name) + '" autocomplete="off"></div>' +
    '<div class="form-group"><label class="form-label" for="ec-last">Last name</label>' +
    '<input class="form-input" id="ec-last" value="' + v(p.last_name) + '" autocomplete="off"></div>' +
    '</div>' +
    '<div class="form-group"><label class="form-label" for="ec-title">Title</label>' +
    '<input class="form-input" id="ec-title" value="' + v(p.title) + '" autocomplete="off"></div>' +
    '<div class="form-group"><label class="form-label" for="ec-company">Company</label>' +
    '<input class="form-input" id="ec-company" value="' + v(p.company) + '" autocomplete="off"></div>' +
    '<div class="form-row">' +
    '<div class="form-group"><label class="form-label" for="ec-email">Email</label>' +
    '<input class="form-input" id="ec-email" type="email" value="' + v(p.email) + '" autocomplete="off"></div>' +
    '<div class="form-group"><label class="form-label" for="ec-phone">Phone</label>' +
    '<input class="form-input" id="ec-phone" value="' + v(p.phone) + '" autocomplete="off"></div>' +
    '</div>' +
    '<div class="form-row">' +
    '<div class="form-group"><label class="form-label" for="ec-status">Status</label>' +
    '<select class="form-input" id="ec-status">' + statuses + '</select></div>' +
    '<div class="form-group"><label class="form-label" for="ec-source">Source</label>' +
    '<input class="form-input" id="ec-source" value="' + v(p.source) + '" autocomplete="off"></div>' +
    '</div>' +
    '<div class="btn-group">' +
    '<button class="btn btn-primary" id="ec-save" onclick="submitContactEdit(\'' + p.id + '\')">Save changes</button>' +
    '<button class="btn btn-secondary" onclick="closeModal()">Cancel</button>' +
    '</div></div></div>';

  _modalEscHandler = e => { if (e.key === 'Escape') closeModal(); };
  document.addEventListener('keydown', _modalEscHandler);
  const first = document.getElementById('ec-first');
  if (first) { first.focus(); first.select(); }
}

function modalError(msg) {
  const el = document.getElementById('ec-error');
  if (!el) return;
  el.textContent = msg;
  el.classList.add('show');
}

async function submitContactEdit(id) {
  const val = x => ((document.getElementById(x) || {}).value || '').trim();
  const first = val('ec-first'), last = val('ec-last'), email = val('ec-email');
  if (!first && !last) { modalError('A first or last name is required.'); return; }
  if (email && !/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(email)) {
    modalError('"' + email + '" is not a valid email address.'); return;
  }
  const btn = document.getElementById('ec-save');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving...'; }

  const data = await contactApi('/api/contacts/' + encodeURIComponent(id), {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      first_name: first, last_name: last, title: val('ec-title'),
      company: val('ec-company'), email: email, phone: val('ec-phone'),
      status: val('ec-status'), source: val('ec-source')
    })
  });
  if (data && data.success) {
    closeModal();
    showToast('Contact updated.', 'success');
    loadProspects();
    return;
  }
  if (btn) { btn.disabled = false; btn.textContent = 'Save changes'; }
  modalError((data && data.message) || 'Could not update that contact.');
}

async function deleteContact(i) {
  const p = _prospects[i];
  if (!p) return;
  const who = ((p.first_name || '') + ' ' + (p.last_name || '')).trim() || p.email;
  if (!await askConfirm('Delete ' + who + '? This also cancels any mail '
      + 'still queued for them. It cannot be undone.', 'Delete')) return;
  const data = await contactApi('/api/contacts/' + encodeURIComponent(p.id), {method: 'DELETE'});
  if (data && data.success) { showToast('Contact deleted.', 'success'); loadProspects(); }
  else showToast((data && data.message) || 'Could not delete that contact.', 'error');
}

// ── Editing a draft before approving it ──
// Only pending_review drafts can be edited. The server re-runs the same
// deterministic pre-send gate on the edited text, so a hand edit cannot walk
// an email past the checks Harvey applies to its own writing.

function deskEdit(id) { _desk.editing = id; loadOutbox(); }

// Rewrite the draft in front of you from the template. Stays on the same
// email afterwards: the point is to look at the new wording, and being
// bounced back to the top of a queue of forty-four would defeat that.
// window.confirm is not usable here. Some embedded browsers suppress the
// native dialog and return false without ever showing it, which turns every
// guarded action into a button that silently does nothing: no request, no
// toast, no error. That is indistinguishable from a broken feature, and it
// is how Regenerate, Reject all and Delete all came to "not work".
//
// This is a dialog in the page, so the answer is always the one the person
// actually gave.
var _confirmResolve = null;

function askConfirm(message, confirmLabel) {
  return new Promise(function (resolve) {
    var root = document.getElementById('modal-root');
    if (!root) { resolve(false); return; }
    if (_confirmResolve) _confirmResolve(false);
    _confirmResolve = resolve;
    root.innerHTML =
      '<div class="modal-backdrop" onclick="confirmBackdrop(event)">' +
      '<div class="modal" role="dialog" aria-modal="true" aria-label="Confirm">' +
        '<h2>Just checking</h2>' +
        '<p class="lede">' + escHtml(message) + '</p>' +
        '<div class="btn-group">' +
          '<button class="btn btn-primary" id="confirm-yes" ' +
            'onclick="confirmAnswer(true)">' +
            escHtml(confirmLabel || 'Confirm') + '</button>' +
          '<button class="btn btn-secondary" onclick="confirmAnswer(false)">' +
            'Cancel</button>' +
        '</div>' +
      '</div></div>';
    _modalEscHandler = function (e) {
      if (e.key === 'Escape') confirmAnswer(false);
    };
    document.addEventListener('keydown', _modalEscHandler);
    var yes = document.getElementById('confirm-yes');
    if (yes) yes.focus();
  });
}

function confirmAnswer(answer) {
  var resolve = _confirmResolve;
  _confirmResolve = null;
  closeModal();
  if (resolve) resolve(!!answer);
}

function confirmBackdrop(e) {
  if (e && e.target && e.target.classList &&
      e.target.classList.contains('modal-backdrop')) {
    confirmAnswer(false);
  }
}

// Which composition notes are worth interrupting someone for. A note that
// a configuration field is empty is a standing fact about the setup, not
// news about the draft just written, and it is already shown with the
// readiness warnings before a run starts. Repeating it per click trains
// people to ignore the toast that does matter.
function noteWorthSaying(note) {
  return !/not configured|No founder|No demo URL|No reply address|No organisation/i
    .test(note || '');
}

async function deskRegenerate(id) {
  var go = await askConfirm(
    'Rewrite this email from the template? Any wording you edited by hand '
    + 'will be replaced. Nothing is sent.', 'Rewrite');
  if (!go) return;
  const btn = document.getElementById('desk-regen');
  if (btn) { btn.disabled = true; btn.textContent = 'Rewriting...'; }
  const data = await contactApi(
    '/api/outbox/' + encodeURIComponent(id) + '/regenerate', {method: 'POST'});
  if (data && data.success) {
    // Notes describe configuration, not a failure of this rewrite, so they
    // are reported in the ordinary style. The red box made a working
    // rewrite look broken.
    showToast('Rewritten.', 'success');
    (data.notes || []).filter(noteWorthSaying).forEach(
      n => showToast(n, 'success'));
  } else {
    showToast((data && data.message) || 'Could not regenerate.', 'error');
  }
  const at = _desk.i;
  await loadOutbox();
  if (at < _desk.items.length) deskGo(at);
}
function deskCancelEdit() { _desk.editing = null; loadOutbox(); }

async function deskSaveEdit(id) {
  const subject = (document.getElementById('desk-subject') || {}).value || '';
  const body = (document.getElementById('desk-body') || {}).value || '';
  const data = await contactApi('/api/outbox/' + encodeURIComponent(id), {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({subject: subject, body: body})
  });
  if (data && data.success) {
    _desk.editing = null;
    showToast('Draft saved.', 'success');
    loadOutbox();
  } else {
    showToast((data && data.message) || 'Could not save that draft.', 'error');
  }
}

// ── Write Email ──
// Composes a one-off email straight into the SAME outbox the Writer uses,
// at pending_review. Send to Outbox never sends: Approve & Send in the
// Outbox tab remains the only way anything leaves.

function openSingleComposer(i) {
  const root = document.getElementById('modal-root');
  if (!root) return;
  const p = (typeof i === 'number') ? _prospects[i] : null;
  const v = x => escHtml(x || '');
  const name = p ? ((p.first_name || '') + ' ' + (p.last_name || '')).trim() : '';

  root.innerHTML =
    '<div class="modal-backdrop" onclick="modalBackdropClick(event)">' +
    '<div class="modal" role="dialog" aria-modal="true" aria-label="Write email">' +
    '<h2>Write email</h2>' +
    '<p class="modal-sub">Saved to the Outbox as a pending draft. Nothing is sent until you approve it there.</p>' +
    '<div class="modal-error" id="wc-error"></div>' +
    '<datalist id="wc-contacts"></datalist>' +
    '<div class="form-group"><label class="form-label" for="wc-to">To</label>' +
    '<div id="wc-to-list">' + composerToRow(v(p && p.email), true) + '</div>' +
    '<button type="button" class="btn btn-secondary btn-sm" style="margin-top:6px" ' +
    'onclick="addComposerRecipient()">+ Add recipient</button>' +
    '<p class="lede">Pick a contact or type an address. Each recipient gets ' +
    'their own separate email, never a shared To line.</p></div>' +
    '<div class="form-row">' +
    '<div class="form-group"><label class="form-label" for="wc-name">Recipient name</label>' +
    '<input class="form-input" id="wc-name" value="' + v(name) + '" autocomplete="off"></div>' +
    '<div class="form-group"><label class="form-label" for="wc-company">Company</label>' +
    '<input class="form-input" id="wc-company" value="' + v(p && p.company) + '" autocomplete="off"></div>' +
    '</div>' +
    '<div class="form-group"><label class="form-label" for="wc-subject">Subject</label>' +
    '<input class="form-input" id="wc-subject" placeholder="lowercase, a few words" autocomplete="off"></div>' +
    '<div class="form-group"><label class="form-label" for="wc-body">Email body</label>' +
    '<textarea class="form-input" id="wc-body" rows="12" ' +
    'style="font-family:var(--mono);font-size:12.5px;line-height:1.6" ' +
    'placeholder="Hi ' + (v(p && p.first_name) || 'there') + ',"></textarea></div>' +
    '<div id="wc-preview"></div>' +
    '<div class="btn-group">' +
    '<button class="btn btn-secondary" onclick="previewComposer()">Preview</button>' +
    '<button class="btn btn-secondary" id="wc-draft" onclick="sendToOutbox(\'draft\')">Create draft</button>' +
    '<button class="btn btn-primary" id="wc-save" onclick="sendToOutbox(\'pending_review\')">Move to Outbox</button>' +
    '<button class="btn btn-secondary" onclick="closeModal()">Cancel</button>' +
    '</div></div></div>';

  _modalEscHandler = e => { if (e.key === 'Escape') closeModal(); };
  document.addEventListener('keydown', _modalEscHandler);
  fillComposerContacts();
  const focus = document.getElementById(p ? 'wc-subject' : 'wc-to');
  if (focus) focus.focus();
}

function composerToRow(value, first) {
  return '<div class="wc-to-row">' +
    '<input class="form-input wc-to" ' + (first ? 'id="wc-to" ' : '') +
    'list="wc-contacts" value="' + (value || '') +
    '" placeholder="name@example.edu" autocomplete="off">' +
    (first ? '' : '<button type="button" class="btn btn-secondary btn-sm" ' +
      'onclick="this.parentNode.remove()">Remove</button>') +
    '</div>';
}

function addComposerRecipient() {
  const list = document.getElementById('wc-to-list');
  if (list) list.insertAdjacentHTML('beforeend', composerToRow('', false));
}

// The picker is a datalist rather than a select: you must be able to type an
// address that is not a contact yet, which a select cannot do.
async function fillComposerContacts() {
  const dl = document.getElementById('wc-contacts');
  if (!dl) return;
  let people = _prospects;
  if (!people || !people.length) people = (await api('/api/prospects')) || [];
  dl.innerHTML = people
    .filter(x => x && x.email)
    .map(x => {
      const who = ((x.first_name || '') + ' ' + (x.last_name || '')).trim();
      const label = [who, x.company].filter(Boolean).join(' - ');
      return '<option value="' + escHtml(x.email) + '">' + escHtml(label) + '</option>';
    }).join('');
}

function composerRecipients() {
  return Array.from(document.querySelectorAll('.wc-to'))
    .map(el => (el.value || '').trim())
    .filter(Boolean);
}

function previewComposer() {
  const box = document.getElementById('wc-preview');
  if (!box) return;
  const val = x => ((document.getElementById(x) || {}).value || '').trim();
  const to = composerRecipients(), subject = val('wc-subject'), body = val('wc-body');
  if (!to.length) { box.innerHTML = '<div class="test-result error">Add a recipient first.</div>'; return; }
  box.innerHTML =
    '<div class="subhead">Preview</div>' +
    to.map(addr =>
      '<div class="card" style="margin-bottom:10px">' +
      '<div class="muted" style="font-size:12px">To ' + escHtml(addr) + '</div>' +
      '<div style="font-weight:500;margin:4px 0 8px">' +
      escHtml(subject || '(no subject)') + '</div>' +
      '<div style="white-space:pre-wrap;font-size:13px;line-height:1.6">' +
      escHtml(body || '(empty)') + '</div></div>').join('') +
    (to.length > 1
      ? '<p class="lede">' + to.length + ' separate emails will be created.</p>'
      : '');
}

function composerError(msg) {
  const el = document.getElementById('wc-error');
  if (!el) return;
  el.textContent = msg;
  el.classList.add('show');
}

async function sendToOutbox(status) {
  const val = x => ((document.getElementById(x) || {}).value || '').trim();
  const to = composerRecipients();
  const subject = val('wc-subject'), body = val('wc-body');
  if (!to.length) { composerError('A recipient email is required.'); return; }
  const bad = to.find(a => !/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(a));
  if (bad) { composerError('"' + bad + '" is not a valid email address.'); return; }
  if (!subject) { composerError('A subject is required.'); return; }
  if (!body) { composerError('The email body is empty.'); return; }

  const draft = status === 'draft';
  const btn = document.getElementById(draft ? 'wc-draft' : 'wc-save');
  const label = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = 'Saving...'; }

  const data = await contactApi('/api/outbox/compose', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({to_emails: to, subject: subject, body: body,
                          status: draft ? 'draft' : 'pending_review'})
  });
  if (data && data.success) {
    closeModal();
    const n = data.created || 1;
    showToast(n + (draft
      ? ' draft(s) saved. Move to the Outbox when you are ready.'
      : ' draft(s) on the approval desk. Nothing sent yet.'), 'success');
    const tab = document.querySelector('nav button[onclick*="outbox"]');
    showTab('outbox', tab);
    loadOutbox();
    return;
  }
  if (btn) { btn.disabled = false; btn.textContent = label; }
  composerError((data && data.message) || 'Could not save that draft.');
}

async function outboxPromote(id) {
  const d = await contactApi(
    '/api/outbox/' + encodeURIComponent(id) + '/to-outbox', {method: 'POST'});
  showToast(d && d.success
    ? 'Moved to the approval desk.'
    : ((d && d.message) || 'Could not move that draft.'),
    d && d.success ? 'success' : 'error');
  loadOutbox();
}

// ── Start Discovery, from the Signals tab ──
// Reuses the existing /api/discover/run pipeline rather than a second one:
// results land in Companies, and Scout turns companies into Contacts on its
// next pass. Discovery never sends anything.

let _discPoll = null, _discTick = null, _discStart = null;

function renderStartDiscovery(state) {
  const el = document.getElementById('signals-discovery');
  if (!el) return;
  const running = state && state.running;
  const report = state && state.last_report;
  const provider = (state && (state.selected || state.default)) || '';
  const ready = ((state && state.providers) || [])
    .find(p => p.key === provider) || {};

  let html = '<div class="card" style="margin-top:14px">' +
    '<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">' +
    '<button class="btn btn-primary" id="start-disc" onclick="startDiscovery()"' +
      (running ? ' disabled' : '') + '>' +
      (running ? 'Discovering&hellip;' : 'Start Discovery') + '</button>' +
    (running
      ? '<button class="btn btn-danger btn-sm" onclick="stopDiscovery()">Stop</button>'
      : '') +
    '<span class="muted" style="font-size:12.5px">Source: <b>' +
      escHtml(ready.name || provider || 'none') + '</b>' +
      (ready.ready === false ? ' &middot; needs a key' : '') +
      ' &middot; finds institutions for Companies, never sends email</span>' +
    '</div>';

  if (running) {
    const pr = (state && state.progress) || {};
    const done = pr.done || 0, total = pr.total || 0;
    _discStart = pr.started_at ? pr.started_at * 1000 : (_discStart || Date.now());
    const pct = total ? Math.round(done / total * 100) : 0;
    html += '<div style="margin-top:14px">' +
      '<div style="display:flex;justify-content:space-between;font-size:12.5px;' +
      'color:var(--text-2);margin-bottom:6px">' +
      '<span>Query <b>' + (done + 1) + '</b> of <b>' + (total || '?') + '</b>' +
      (pr.query ? ' &middot; ' + escHtml(pr.query) : '') + '</span>' +
      '<span><span id="disc-elapsed">0:00</span> elapsed' +
      '<span id="disc-left"></span></span></div>' +
      '<div style="height:6px;background:var(--panel-raised);border-radius:3px;' +
      'overflow:hidden"><div id="disc-bar" style="height:100%;width:' + pct +
      '%;background:var(--ink);transition:width .4s"></div></div></div>';
    startDiscTicker(total);
  } else if (report) {
    const found = (report.new_companies || 0) + (report.known_companies || 0);
    html += '<div class="stats-grid" style="margin-top:14px">' +
      ['Institutions found', found,
       'New to Companies', report.new_companies || 0,
       'Already known', report.known_companies || 0,
       'Observations saved', report.observations || 0,
       'Filtered as junk', report.junk || 0,
       'Cost', report.actual_cost ? '$' + report.actual_cost.toFixed(4) : 'free'
      ].reduce((acc, v, i, a) => i % 2 ? acc +
        '<div class="stat-card"><div class="label">' + a[i - 1] +
        '</div><div class="value">' + v + '</div></div>' : acc, '') +
      '</div>';

    // The honest case: a run that worked but could not serve this ICP.
    if (!found && (report.errors || []).length) {
      html += '<div class="test-result error" style="margin-top:12px">' +
        'This source returned nothing for your targets. ' +
        escHtml(report.errors[0]) + '</div>';
    } else if (!found) {
      html += '<div class="test-result error" style="margin-top:12px">' +
        'No institutions matched. Check the cities and ICP on the Discover tab.' +
        '</div>';
    } else {
      html += '<p class="lede" style="margin-top:10px">Saved to <b>Companies</b>. ' +
        'Scout finds the people at each one on its next pass, and they appear in ' +
        '<b>Contacts</b>.</p>';
    }
    if (report.stopped) {
      html += '<div class="test-result error" style="margin-top:10px">Stopped early: ' +
        escHtml(report.stopped) + '</div>';
    }
  }
  el.innerHTML = html + '</div>';
}

async function startDiscovery() {
  const btn = document.getElementById('start-disc');
  if (btn) { btn.disabled = true; btn.textContent = 'Discovering…'; }

  const state = await api('/api/discover/providers');
  const provider = (state && (state.selected || state.default)) || '';
  if (!provider) {
    showToast('No discovery source is configured.', 'error');
    renderStartDiscovery(state);
    return;
  }

  const data = await contactApi('/api/discover/run', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({provider: provider, cities: [], depth: 30,
                          limit: 100, max_spend: 1})
  });
  if (!data || !data.success) {
    showToast((data && data.message) || 'Could not start discovery.', 'error');
    renderStartDiscovery(await api('/api/discover/providers'));
    return;
  }
  showToast('Discovery started: ' + (data.queries || 0) + ' queries.', 'success');
  pollDiscovery();
}

function pollDiscovery() {
  if (_discPoll) clearInterval(_discPoll);
  _discPoll = setInterval(async () => {
    const state = await api('/api/discover/providers');
    renderStartDiscovery(state);
    if (!state || !state.running) {
      clearInterval(_discPoll);
      _discPoll = null;
      stopDiscTicker();
      const r = state && state.last_report;
      if (r) {
        const found = (r.new_companies || 0) + (r.known_companies || 0);
        showToast(found ? 'Discovery finished: ' + found + ' institution(s).'
                        : 'Discovery finished, nothing matched.',
                  found ? 'success' : 'error');
      }
      loadCompanies();
    }
  }, 3000);
}


// ── The running clock ──
// Elapsed ticks client-side every second so the number moves between the
// 3-second polls; the remaining estimate comes from the real rate so far
// (queries done over seconds elapsed), not from a guessed duration.

function fmtClock(ms) {
  const t = Math.max(0, Math.round(ms / 1000));
  return Math.floor(t / 60) + ':' + String(t % 60).padStart(2, '0');
}

function startDiscTicker(total) {
  if (_discTick) clearInterval(_discTick);
  const tick = () => {
    const el = document.getElementById('disc-elapsed');
    if (!el || !_discStart) { clearInterval(_discTick); _discTick = null; return; }
    const ms = Date.now() - _discStart;
    el.textContent = fmtClock(ms);
    const left = document.getElementById('disc-left');
    const bar = document.getElementById('disc-bar');
    const done = bar ? (parseFloat(bar.style.width) || 0) / 100 * (total || 0) : 0;
    if (left && done >= 1 && total) {
      const perQuery = ms / done;
      left.textContent = ' · about ' + fmtClock(perQuery * (total - done)) + ' left';
    }
  };
  tick();
  _discTick = setInterval(tick, 1000);
}

function stopDiscTicker() {
  if (_discTick) { clearInterval(_discTick); _discTick = null; }
  _discStart = null;
}

// ── Write Email: bulk generation ──
// The toolbar button writes a draft for EVERY eligible contact; the per-row
// Write button still opens the single composer. Neither one sends.

let _genPoll = null;

async function openComposer(i) {
  if (typeof i === 'number') return openSingleComposer(i);
  const root = document.getElementById('modal-root');
  if (!root) return;
  root.innerHTML =
    '<div class="modal-backdrop" onclick="modalBackdropClick(event)">' +
    '<div class="modal" role="dialog" aria-modal="true" aria-label="Generate emails">' +
    '<h2>Write emails</h2><p class="modal-sub">Checking your contacts...</p>' +
    '<div id="gen-body"></div></div></div>';
  _modalEscHandler = e => { if (e.key === 'Escape') closeGenerator(); };
  document.addEventListener('keydown', _modalEscHandler);
  const d = await contactApi('/api/contacts/generate-preview' + genScopeQuery());
  const body = document.getElementById('gen-body');
  if (!body) return;
  if (!d || !d.success) {
    body.innerHTML = '<div class="modal-error show">' +
      escHtml((d && d.message) || 'Could not read your contacts.') + '</div>';
    return;
  }
  renderGenPreview(d);
}

function renderGenPreview(d) {
  const body = document.getElementById('gen-body');
  const sub = document.querySelector('.modal .modal-sub');
  if (sub) sub.textContent = 'One War Room beta invitation per contact, written for their role and saved to the Outbox for your approval. Nothing is sent.';
  const s = d.skipped || {};
  const cats = Object.keys(d.by_category || {})
    .map(k => catLabel(k) + ': ' + d.by_category[k]).join(', ');
  const roleOpts = ['<option value="">Every category</option>'].concat(
    (d.roles || []).filter(r => r.count).map(r =>
      '<option value="' + escHtml(r.key) + '"' +
      (_genScope.role === r.key ? ' selected' : '') + '>' +
      escHtml(r.label) + ' (' + r.count + ')</option>')).join('');

  const ready = d.readiness || {};
  const warn = (ready.warnings || []).concat(
    (ready.missing || []).length
      ? ['Not configured yet: ' + (ready.missing || []).join(', ') +
         '. Those sentences are left out of every email rather than guessed.']
      : []);

  body.innerHTML =
    (warn.length
      ? '<div class="test-result error" style="margin-bottom:12px">' +
        '<b>Before you send these</b><ul style="margin:6px 0 0 18px">' +
        warn.map(w => '<li>' + escHtml(w) + '</li>').join('') + '</ul></div>'
      : '') +
    '<div class="form-group"><label class="form-label" for="gen-role">Who to write for</label>' +
    '<select class="form-input" id="gen-role" onchange="setGenRole(this.value)">' +
    roleOpts + '</select></div>' +
    '<label style="display:flex;gap:8px;align-items:center;margin:0 0 12px;font-size:13px">' +
    '<input type="checkbox" ' + (_genScope.regenerate ? 'checked ' : '') +
    'onchange="setGenRegen(this.checked)"> Replace drafts that are still ' +
    'waiting for review</label>' +
    '<div class="stats-grid" style="margin-bottom:12px">' +
    [['Contacts', d.total], ['Will generate', d.to_generate],
     ['No email', s.no_email || 0], ['Invalid email', s.bad_email || 0],
     ['Suppressed', s.suppressed || 0], ['Already queued', s.already_queued || 0],
     ['Approved, left alone', s.approved_locked || 0]]
      .map(x => '<div class="stat-card"><div class="label">' + x[0] +
        '</div><div class="value">' + x[1] + '</div></div>').join('') +
    '</div>' +
    (cats ? '<p class="lede">By segment: ' + escHtml(cats) + '</p>' : '') +
    '<p class="lede">Suppressed means replied, opted out, lost, closed or already booked. Each draft is a separate email to one person, never a CC list.</p>' +
    '<div class="btn-group">' +
    (d.to_generate
      ? '<button class="btn btn-primary" id="gen-start" onclick="startGeneration()">Start email generation (' + d.to_generate + ')</button>'
      : '<button class="btn btn-primary" disabled>Nothing to generate</button>') +
    '<button class="btn btn-secondary" onclick="refreshGenPreview()">Refresh</button>' +
    '<button class="btn btn-secondary" onclick="closeGenerator()">Cancel</button>' +
    '</div>';
}

async function refreshGenPreview() {
  const body = document.getElementById('gen-body');
  if (body) body.innerHTML = '<div class="test-result pending">Re-checking your contacts...</div>';
  const d = await contactApi('/api/contacts/generate-preview' + genScopeQuery());
  if (!d || !d.success) {
    if (body) body.innerHTML = '<div class="modal-error show">' +
      escHtml((d && d.message) || 'Could not read your contacts.') + '</div>';
    return;
  }
  renderGenPreview(d);
}

function closeGenerator() {
  if (_genPoll) { clearInterval(_genPoll); _genPoll = null; }
  closeModal();
}

// What the next generation run is aimed at. Kept outside the modal so a
// re-render of the preview does not silently reset the operator's choice.
var _genScope = {role: '', regenerate: false, ids: []};

function genScopeQuery() {
  var p = [];
  if (_genScope.role) p.push('role=' + encodeURIComponent(_genScope.role));
  if (_genScope.regenerate) p.push('regenerate=true');
  if (_genScope.ids.length) p.push('ids=' + encodeURIComponent(_genScope.ids.join(',')));
  return p.length ? '?' + p.join('&') : '';
}

function setGenRole(v) { _genScope.role = v || ''; refreshGenPreview(); }
function setGenRegen(v) { _genScope.regenerate = !!v; refreshGenPreview(); }

async function startGeneration() {
  const btn = document.getElementById('gen-start');
  if (btn) { btn.disabled = true; btn.textContent = 'Starting...'; }
  const d = await contactApi('/api/contacts/generate', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      role: _genScope.role,
      regenerate: _genScope.regenerate,
      ids: _genScope.ids
    })
  });
  const body = document.getElementById('gen-body');
  if (!d || !d.success) {
    if (body) body.innerHTML = '<div class="modal-error show">' +
      escHtml((d && d.message) || 'Could not start.') + '</div>' +
      '<div class="btn-group"><button class="btn btn-secondary" onclick="closeGenerator()">Close</button></div>';
    return;
  }
  pollGeneration();
}

function pollGeneration() {
  if (_genPoll) clearInterval(_genPoll);
  const draw = async () => {
    const s = await contactApi('/api/contacts/generate-status');
    const body = document.getElementById('gen-body');
    if (!body) { clearInterval(_genPoll); _genPoll = null; return; }
    const p = (s && s.progress) || {};
    const done = p.done || 0, total = p.total || 0;
    const pct = total ? Math.round(done / total * 100) : 0;
    const elapsed = p.started_at ? Date.now() - p.started_at * 1000 : 0;
    let html = '<div style="display:flex;justify-content:space-between;font-size:12.5px;color:var(--text-2);margin-bottom:6px">' +
      '<span>' + done + ' of ' + total +
      (s && s.running && p.contact ? ' - ' + escHtml(p.contact) : '') + '</span>' +
      '<span>' + fmtClock(elapsed) +
      (s && s.running && done ? ' - about ' + fmtClock(elapsed / done * (total - done)) + ' left' : '') +
      '</span></div>' +
      '<div style="height:6px;background:var(--panel-raised);border-radius:3px;overflow:hidden;margin-bottom:12px">' +
      '<div style="height:100%;width:' + pct + '%;background:var(--ink);transition:width .4s"></div></div>' +
      '<div class="stats-grid">' +
      [['Drafted', p.created || 0], ['Failed', p.failed || 0], ['Blocked by gate', p.blocked || 0]]
        .map(x => '<div class="stat-card"><div class="label">' + x[0] +
          '</div><div class="value">' + x[1] + '</div></div>').join('') +
      '</div>';
    if (!s || !s.running) {
      clearInterval(_genPoll); _genPoll = null;
      if ((p.errors || []).length) {
        html += '<div class="modal-error show" style="margin-top:12px">' + escHtml(p.errors[0]) +
          (p.errors.length > 1 ? ' (and ' + (p.errors.length - 1) + ' more)' : '') + '</div>';
      }
      html += '<div class="btn-group">' +
        (p.created ? '<button class="btn btn-primary" onclick="goToOutbox()">Review ' + p.created + ' in Outbox</button>' : '') +
        '<button class="btn btn-secondary" onclick="closeGenerator()">Close</button></div>';
      if (p.created) showToast(p.created + ' draft(s) ready for approval.', 'success');
    } else {
      html += '<div class="btn-group"><button class="btn btn-danger" ' +
        'onclick="stopGeneration()">Stop</button>' +
        '<button class="btn btn-secondary" onclick="closeGenerator()">' +
        'Run in background</button></div>';
    }
    body.innerHTML = html;
  };
  draw();
  _genPoll = setInterval(draw, 2000);
}

function goToOutbox() {
  closeGenerator();
  showTab('outbox', document.querySelector('nav button[onclick*="outbox"]'));
  loadOutbox();
}

// Stopping leaves whatever was already drafted in the outbox; a run is one
// model call per contact, so there has to be a way out of a long one.
async function stopGeneration() {
  const d = await contactApi('/api/contacts/generate-stop', {method: 'POST'});
  if (d && d.success) showToast(d.stopped ? 'Generation stopped.' : 'Nothing was running.', 'success');
  else showToast('Could not stop the run.', 'error');
}

// ── Saved discovery searches ──
// Location and Categories are real query parameters now, so a search is
// worth keeping. Stored server-side in settings, not in this browser.

function splitList(id) {
  const raw = ((document.getElementById(id) || {}).value || '').trim();
  return raw ? raw.split(/[;\n]/).map(x => x.trim()).filter(Boolean) : [];
}

async function loadDiscoverConfigs(selectName) {
  const sel = document.getElementById('disc-config-pick');
  if (!sel) return;
  const d = await contactApi('/api/discover/configs');
  const configs = (d && d.configs) || [];
  sel.innerHTML = '<option value="">None</option>' + configs.map(c =>
    '<option value="' + escHtml(c.name) + '">' + escHtml(c.name) + '</option>').join('');
  if (selectName) sel.value = selectName;
  window._discConfigs = configs;
}

function loadDiscoverConfig() {
  const sel = document.getElementById('disc-config-pick');
  const c = (window._discConfigs || []).find(x => x.name === sel.value);
  if (!c) return;
  document.getElementById('disc-cities').value = (c.cities || []).join('; ');
  document.getElementById('disc-terms').value = (c.terms || []).join('; ');
  document.getElementById('disc-config-name').value = c.name;
  if (c.depth) document.getElementById('disc-depth').value = c.depth;
  if (c.limit) document.getElementById('disc-limit').value = c.limit;
  showToast('Loaded "' + c.name + '".', 'success');
}

async function saveDiscoverConfig() {
  const name = ((document.getElementById('disc-config-name') || {}).value || '').trim();
  if (!name) { showToast('Name the search first.', 'error'); return; }
  const d = await contactApi('/api/discover/configs', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      name: name, cities: splitList('disc-cities'), terms: splitList('disc-terms'),
      depth: Number((document.getElementById('disc-depth') || {}).value) || 30,
      limit: Number((document.getElementById('disc-limit') || {}).value) || 100
    })
  });
  if (d && d.success) { showToast('Saved "' + name + '".', 'success'); loadDiscoverConfigs(name); }
  else showToast((d && d.message) || 'Could not save that search.', 'error');
}

async function deleteDiscoverConfig() {
  const sel = document.getElementById('disc-config-pick');
  const name = sel ? sel.value : '';
  if (!name) { showToast('Pick a saved search first.', 'error'); return; }
  if (!await askConfirm('Delete the saved search "' + name + '"?',
                        'Delete')) return;
  const d = await contactApi('/api/discover/configs', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: name, delete: true})
  });
  if (d && d.success) { showToast('Deleted.', 'success'); loadDiscoverConfigs(); }
  else showToast('Could not delete that search.', 'error');
}

// ── Custom Email ──
// You write the template once; it is filled in per contact. No model call,
// so this works whether or not the Claude CLI is signed in.

const CUSTOM_DEFAULT_SUBJECT = 'a question about {{department}}';
const CUSTOM_DEFAULT_BODY =
  'Hi {{first_name}},\n\n' +
  'I work with colleges on experiential learning and thought of {{company}} ' +
  'given your work in {{department}}.\n\n' +
  'We built a 60 minute AI business simulation. Students take a venture ' +
  'through six stages, make 100+ decisions under time pressure, and get an ' +
  'AI report on how they decided rather than just what they chose.\n\n' +
  'Try it yourself: {{trial_link}}\n\n' +
  'If it raises questions, grab a time here: {{calendly_link}}\n\n' +
  '{{sender_name}}\n{{sender_company}}';

function openCustomEmail() {
  const root = document.getElementById('modal-root');
  if (!root) return;
  const chips = ['first_name', 'name', 'title', 'company', 'department',
                 'category', 'trial_link', 'calendly_link', 'sender_name',
                 'sender_company']
    .map(f => '<code style="cursor:pointer" onclick="insertField(\'' + f +
      '\')">{{' + f + '}}</code>').join(' ');

  root.innerHTML =
    '<div class="modal-backdrop" onclick="modalBackdropClick(event)">' +
    '<div class="modal" style="max-width:720px" role="dialog" aria-modal="true" aria-label="Custom email">' +
    '<h2>Custom email</h2>' +
    '<p class="modal-sub">Write it once. Each contact gets their own copy with their name, college and department filled in. Saved to the Outbox for approval, never sent.</p>' +
    '<div class="modal-error" id="cu-error"></div>' +
    '<div class="form-group"><label class="form-label">Who gets it</label>' +
    '<label class="cu-scope"><input type="radio" name="cu-scope" value="new" checked ' +
    'onchange="previewCustomEmail()"> <span id="cu-label-new">Only contacts without a draft</span></label>' +
    '<label class="cu-scope"><input type="radio" name="cu-scope" value="all" ' +
    'onchange="previewCustomEmail()"> <span id="cu-label-all">Everyone, replacing drafts still awaiting review</span></label>' +
    '</div>' +
    '<div class="form-group"><label class="form-label" for="cu-subject">Subject</label>' +
    '<input class="form-input" id="cu-subject" value="' + escHtml(CUSTOM_DEFAULT_SUBJECT) + '"></div>' +
    '<div class="form-group"><label class="form-label" for="cu-body">Body</label>' +
    '<textarea class="form-input" id="cu-body" rows="14" style="font-family:var(--mono);font-size:12.5px;line-height:1.6">' +
    escHtml(CUSTOM_DEFAULT_BODY) + '</textarea></div>' +
    '<p class="lede">Click to insert: ' + chips + '</p>' +
    '<div id="cu-preview"></div>' +
    '<div class="btn-group">' +
    '<button class="btn btn-secondary" onclick="previewCustomEmail()">Preview</button>' +
    '<button class="btn btn-primary" id="cu-make" onclick="generateCustomEmails()">Create drafts</button>' +
    '<button class="btn btn-secondary" onclick="closeModal()">Cancel</button>' +
    '</div></div></div>';

  _modalEscHandler = e => { if (e.key === 'Escape') closeModal(); };
  document.addEventListener('keydown', _modalEscHandler);
  previewCustomEmail();
}

function insertField(f) {
  const ta = document.getElementById('cu-body');
  if (!ta) return;
  const at = ta.selectionStart || ta.value.length;
  ta.value = ta.value.slice(0, at) + '{{' + f + '}}' + ta.value.slice(at);
  ta.focus();
  ta.selectionStart = ta.selectionEnd = at + f.length + 4;
}

function customScope() {
  const r = document.querySelector('input[name="cu-scope"]:checked');
  return (r && r.value) === 'all' ? 'all' : 'new';
}

function customBody() {
  return {
    subject: (document.getElementById('cu-subject') || {}).value || '',
    body: (document.getElementById('cu-body') || {}).value || '',
    scope: customScope()
  };
}

// Both radio labels carry a live count, so the choice is made with the
// numbers in view rather than by trying one and reading an error.
function cuScopeCounts(scope, eligible, skipped, replacing) {
  const queued = (skipped && skipped.already_queued) || 0;
  let nNew, nAll;
  if (scope === 'all') {
    nAll = eligible;
    nNew = Math.max(0, eligible - (replacing || 0));
  } else {
    nNew = eligible;
    nAll = eligible + queued;
  }
  const a = document.getElementById('cu-label-new');
  const b = document.getElementById('cu-label-all');
  if (a) a.textContent = 'Only contacts without a draft (' + nNew + ')';
  if (b) b.textContent = 'Everyone (' + nAll + ')' +
    (nAll > nNew ? ', replacing ' + (nAll - nNew) + ' draft(s) awaiting review' : '');
  const locked = (skipped && skipped.approved_locked) || 0;
  return locked;
}

async function previewCustomEmail() {
  const box = document.getElementById('cu-preview');
  if (!box) return;
  const make = document.getElementById('cu-make');
  box.innerHTML = '<div class="test-result pending">Rendering...</div>';
  const payload = customBody();
  const d = await contactApi('/api/contacts/custom-preview', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  if (!d || !d.success) {
    if (d && d.skipped) cuScopeCounts(payload.scope, 0, d.skipped, 0);
    if (make) { make.disabled = true; make.textContent = 'Create drafts'; }
    box.innerHTML = '<div class="test-result error">' +
      escHtml((d && d.message) || 'Could not render.') + '</div>';
    return;
  }
  const locked = cuScopeCounts(d.scope, d.eligible, d.skipped, d.replacing);
  if (make) {
    make.disabled = !d.eligible;
    make.textContent = 'Create ' + d.eligible + ' draft' + (d.eligible === 1 ? '' : 's');
  }
  box.innerHTML =
    '<div class="subhead">Preview, as ' + escHtml(d.for) + ' would see it</div>' +
    '<div class="card" style="margin-bottom:10px">' +
    '<div class="muted" style="font-size:12px">To ' + escHtml(d.to) + '</div>' +
    '<div style="font-weight:500;margin:4px 0 8px">' + escHtml(d.subject) + '</div>' +
    '<div style="white-space:pre-wrap;font-size:13px;line-height:1.6">' +
    escHtml(d.body) + '</div></div>' +
    (d.replacing
      ? '<div class="test-result pending">' + d.replacing + ' contact(s) already ' +
        'have a draft awaiting review. Those will be cancelled and replaced ' +
        'by this one. Nothing that you approved is touched.</div>'
      : '') +
    (locked
      ? '<div class="test-result pending">' + locked + ' contact(s) are left out ' +
        'because their draft is already approved. Send or cancel those in the ' +
        'Outbox if you want this template to reach them instead.</div>'
      : '') +
    (d.unknown_fields.length
      ? '<div class="test-result error">Unknown field(s): ' +
        escHtml(d.unknown_fields.join(', ')) + '. Check the spelling.</div>'
      : '') +
    (d.incomplete
      ? '<div class="test-result error">' + d.incomplete + ' contact(s) are ' +
        'missing one of these fields, so their email will read oddly: ' +
        escHtml((d.incomplete_sample || []).join(', ')) +
        (d.incomplete > 4 ? ' and others' : '') + '.</div>'
      : '') +
    (d.gate_ok ? '' : '<div class="test-result error">The gate would block this: ' +
      escHtml(d.gate_reasons.join('; ')) + '</div>');
}

async function generateCustomEmails() {
  const btn = document.getElementById('cu-make');
  const label = btn ? btn.textContent : 'Create drafts';
  if (btn) { btn.disabled = true; btn.textContent = 'Creating...'; }
  const d = await contactApi('/api/contacts/custom-generate', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(customBody())
  });
  if (!d || !d.success) {
    if (btn) { btn.disabled = false; btn.textContent = label; }
    const err = document.getElementById('cu-error');
    if (err) { err.textContent = (d && d.message) || 'Could not create drafts.'; err.classList.add('show'); }
    return;
  }
  closeModal();
  showToast(d.created + ' draft(s) ready for approval' +
    (d.replaced ? ', ' + d.replaced + ' older draft(s) replaced' : '') +
    (d.blocked ? ', ' + d.blocked + ' blocked by the gate' : '') + '.',
    d.created ? 'success' : 'error');
  goToOutbox();
}

// ── Contacts: people and institutions under one tab ──
// Two renderers kept as they were; the toggle just swaps which section is
// showing, so the Companies drill-down still works.

function contactsView(which) {
  const tab = document.querySelector('nav button[onclick*="prospects"]');
  if (which === 'institutions') {
    showTab('companies', tab);
    loadCompanies();
  } else {
    showTab('prospects', tab);
    loadProspects();
  }
}

// ── Gmail connection ──
// Status is read from the server every time, never inferred from whether a
// key is filled in: a saved secret is not a connected mailbox.

let _gmailPoll = null;

async function loadGmailState() {
  const box = document.getElementById('gmail-state');
  if (!box) return;
  const d = await contactApi('/api/gmail/status');
  if (!d || !d.success) {
    box.innerHTML = '<div class="test-result error">' +
      escHtml((d && d.message) || 'Could not read the Gmail status.') + '</div>';
    return;
  }
  const c = d.connecting || {};
  let tone = 'error', text;
  if (c.running) { tone = 'pending'; text = c.message || 'Waiting for Google sign-in...'; }
  else if (!d.configured) text = 'Not set up. Paste the client ID and secret, then Save credentials.';
  else if (!d.authorized) text = 'Credentials saved. Press Connect Gmail to authorise.';
  else if (d.reachable === false) text = 'Authorised, but Gmail rejected the token: ' + (d.detail || 'unknown error');
  else { tone = 'success'; text = 'Connected' + (d.address ? ' as ' + d.address : '') + '.'; }

  box.innerHTML = '<div class="test-result ' + tone + '">' + escHtml(text) + '</div>' +
    '<p class="lede gmail-meta">Redirect URI: <code>' + escHtml(d.redirect_uri) + '</code>' +
    ' &middot; Scope: <code>' + escHtml(d.scope) + '</code></p>';

  const connectBtn = document.getElementById('gmail-connect');
  if (connectBtn) {
    connectBtn.disabled = !!c.running || !d.configured;
    connectBtn.textContent = c.running ? 'Waiting...' :
      (d.authorized ? 'Reconnect Gmail' : 'Connect Gmail');
  }
  const disc = document.getElementById('gmail-disconnect');
  if (disc) disc.disabled = !d.authorized;

  if (c.running && !_gmailPoll) {
    _gmailPoll = setInterval(loadGmailState, 2500);
  } else if (!c.running && _gmailPoll) {
    clearInterval(_gmailPoll); _gmailPoll = null;
    if (c.ok === true) showToast('Gmail connected.', 'success');
    else if (c.ok === false) showToast(c.message || 'Could not connect Gmail.', 'error');
  }
}

async function gmailConnect() {
  const d = await contactApi('/api/gmail/connect', {method: 'POST'});
  if (!d || !d.success) {
    showToast((d && d.message) || 'Could not start the connection.', 'error');
    return;
  }
  // Deployed: Google has to send the browser back to a public URL, so we
  // navigate there. On a laptop the server opens a tab for you instead.
  if (d.mode === 'redirect' && d.url) { window.location = d.url; return; }
  showToast('Approve Harvey in the browser tab that just opened.', 'success');
  loadGmailState();
}

// Coming back from Google: say what happened, then tidy the URL so a
// refresh does not replay a stale result.
(function gmailReturn() {
  const q = new URLSearchParams(window.location.search);
  const outcome = q.get('gmail');
  if (!outcome) return;
  const detail = q.get('detail') || '';
  showToast(outcome === 'connected'
    ? 'Gmail connected.'
    : ('Gmail not connected. ' + detail), outcome === 'connected' ? 'success' : 'error');
  q.delete('gmail'); q.delete('detail');
  const rest = q.toString();
  window.history.replaceState({}, '', window.location.pathname + (rest ? '?' + rest : ''));
})();

// Sign out appears only when a password is actually in force.
(async function signOutControl() {
  let state = null;
  try { state = await (await fetch('/api/auth/state')).json(); } catch (e) { return; }
  if (!state || !state.required) return;
  const host = document.querySelector('.header-controls');
  if (!host) return;
  const btn = document.createElement('button');
  btn.className = 'btn btn-secondary btn-sm';
  btn.textContent = 'Sign out';
  btn.onclick = async () => {
    await fetch('/api/auth/logout', {method: 'POST'});
    window.location = '/login';
  };
  host.appendChild(btn);
})();

async function gmailDisconnect() {
  if (!await askConfirm('Disconnect Gmail? This revokes the token at Google '
      + 'and deletes it here. Approved mail will stop sending until you '
      + 'reconnect.', 'Disconnect')) return;
  const d = await contactApi('/api/gmail/disconnect', {method: 'POST'});
  if (d && d.success) {
    showToast(d.revoked ? 'Disconnected and revoked at Google.'
                        : 'Token removed locally. ' + (d.detail || ''), 'success');
    loadGmailState();
  } else {
    showToast((d && d.message) || 'Could not disconnect.', 'error');
  }
}

async function gmailTest() {
  const to = ((document.getElementById('gmail-test-to') || {}).value || '').trim();
  const box = document.getElementById('gmail-test-result');
  if (!to) { showToast('Enter an address to test with.', 'error'); return; }
  if (box) box.innerHTML = '<div class="test-result pending">Sending...</div>';
  const d = await contactApi('/api/gmail/test', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({to: to})
  });
  if (box) {
    const ok = d && d.success;
    box.innerHTML = '<div class="test-result ' + (ok ? 'success' : 'error') + '">' +
      escHtml((d && d.message) || 'Send failed.') +
      (ok && d.message_id ? ' (id ' + escHtml(d.message_id) + ')' : '') + '</div>';
  }
}
