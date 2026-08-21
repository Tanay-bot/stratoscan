"""Self-contained admin dashboard for Stratoscan.

A single HTML file (inline CSS + vanilla JS, zero external dependencies)
served at ``GET /admin``. It is a thin client over the admin-gated API:

  - ``POST /auth/login``       → stores the JWT in localStorage
  - ``GET  /admin/overview``   → health cards + universe/user/job stats
  - ``GET  /admin/users``      → searchable, paginated user management
  - ``PATCH /admin/users/{id}``→ change plan / admin role / verification
  - ``GET  /admin/jobs``       → job-monitor panel

The HTML page itself is public (so the login form can render); every data
endpoint it calls is behind ``require_admin``. The dashboard sends the JWT
as ``Authorization: Bearer <token>``.

No build step: paste-safe, CSP-friendly inline assets only.
"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stratoscan — Admin</title>
<style>
  :root {
    --bg: #0b0f1a;
    --panel: #131a2b;
    --panel-2: #1a2338;
    --border: #24304c;
    --text: #e6ecf7;
    --muted: #8b9ac2;
    --accent: #4f46e5;
    --accent-2: #7c6cff;
    --green: #22c55e;
    --red: #ef4444;
    --amber: #f59e0b;
    --cyan: #38bdf8;
    --radius: 14px;
    --shadow: 0 10px 30px rgba(0,0,0,.35);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    background:
      radial-gradient(1200px 600px at 80% -10%, rgba(79,70,229,.18), transparent 60%),
      radial-gradient(900px 500px at -10% 110%, rgba(56,189,248,.10), transparent 55%),
      var(--bg);
    color: var(--text);
    min-height: 100vh;
    line-height: 1.5;
  }
  a { color: var(--accent-2); text-decoration: none; }

  /* ── Login screen ─────────────────────────────────────────── */
  #login-screen {
    min-height: 100vh; display: grid; place-items: center; padding: 24px;
  }
  .login-card {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 20px; padding: 40px; width: 100%; max-width: 400px;
    box-shadow: var(--shadow);
  }
  .login-card h1 { font-size: 24px; display: flex; align-items: center; gap: 10px; }
  .login-card .logo {
    width: 38px; height: 38px; border-radius: 10px;
    background: linear-gradient(135deg, var(--accent), var(--accent-2));
    display: grid; place-items: center; font-weight: 800; font-size: 18px;
  }
  .login-card p.sub { color: var(--muted); margin: 8px 0 24px; font-size: 14px; }
  .field { margin-bottom: 14px; }
  .field label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 6px; letter-spacing: .4px; text-transform: uppercase; }
  .field input {
    width: 100%; padding: 12px 14px; border-radius: 10px;
    border: 1px solid var(--border); background: var(--panel-2); color: var(--text);
    font-size: 15px; outline: none; transition: border-color .15s, box-shadow .15s;
  }
  .field input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(79,70,229,.25); }
  .login-error { color: var(--red); font-size: 13px; min-height: 18px; margin: 4px 0 10px; }
  .btn-primary {
    width: 100%; padding: 13px; border: none; border-radius: 10px; cursor: pointer;
    background: linear-gradient(135deg, var(--accent), var(--accent-2));
    color: #fff; font-size: 15px; font-weight: 600; letter-spacing: .3px;
    transition: transform .12s, box-shadow .12s, filter .12s;
  }
  .btn-primary:hover { filter: brightness(1.08); box-shadow: 0 6px 20px rgba(79,70,229,.4); transform: translateY(-1px); }
  .btn-primary:disabled { opacity: .55; cursor: wait; transform: none; }

  /* ── Shell ────────────────────────────────────────────────── */
  #app { display: none; }
  header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 16px 28px; border-bottom: 1px solid var(--border);
    background: rgba(11,15,26,.75); backdrop-filter: blur(8px);
    position: sticky; top: 0; z-index: 10;
  }
  header .brand { display: flex; align-items: center; gap: 12px; font-weight: 700; font-size: 17px; }
  header .brand .logo { width: 30px; height: 30px; border-radius: 8px; font-size: 14px;
    background: linear-gradient(135deg, var(--accent), var(--accent-2)); display: grid; place-items: center; }
  header .whoami { display: flex; align-items: center; gap: 14px; color: var(--muted); font-size: 13px; }
  .pill {
    padding: 4px 10px; border-radius: 999px; font-size: 11px; font-weight: 600;
    letter-spacing: .4px; text-transform: uppercase; border: 1px solid var(--border);
  }
  .pill.admin { color: var(--cyan); border-color: rgba(56,189,248,.4); background: rgba(56,189,248,.08); }
  .btn-ghost {
    padding: 8px 14px; border-radius: 9px; border: 1px solid var(--border);
    background: transparent; color: var(--muted); cursor: pointer; font-size: 13px;
    transition: color .15s, border-color .15s, background .15s;
  }
  .btn-ghost:hover { color: var(--text); border-color: var(--accent); background: var(--panel-2); }

  nav {
    display: flex; gap: 6px; padding: 14px 28px 0; flex-wrap: wrap;
  }
  nav button {
    padding: 9px 18px; border-radius: 10px 10px 0 0; border: 1px solid transparent;
    background: transparent; color: var(--muted); cursor: pointer; font-size: 14px; font-weight: 500;
    border-bottom: 2px solid transparent; transition: color .15s, background .15s;
  }
  nav button:hover { color: var(--text); background: var(--panel-2); }
  nav button.active { color: var(--text); border-bottom-color: var(--accent); background: var(--panel); }

  main { padding: 20px 28px 60px; max-width: 1400px; margin: 0 auto; }
  .panel { display: none; }
  .panel.active { display: block; animation: fadein .25s ease; }
  @keyframes fadein { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }

  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 14px; margin-bottom: 18px; }
  .card {
    background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 18px 20px; transition: transform .15s, border-color .15s;
  }
  .card:hover { transform: translateY(-2px); border-color: var(--accent-2); }
  .card .k { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; }
  .card .v { font-size: 26px; font-weight: 700; margin-top: 6px; }
  .card .d { font-size: 12px; color: var(--muted); margin-top: 4px; }
  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 7px; vertical-align: 1px; }
  .dot.green { background: var(--green); box-shadow: 0 0 8px rgba(34,197,94,.7); }
  .dot.red { background: var(--red); box-shadow: 0 0 8px rgba(239,68,68,.7); }
  .dot.amber { background: var(--amber); box-shadow: 0 0 8px rgba(245,158,11,.7); }
  .dot.gray { background: #475569; }

  .section { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; margin-bottom: 18px; }
  .section h2 { font-size: 15px; margin-bottom: 14px; display: flex; align-items: center; justify-content: space-between; }
  .section h2 .muted { font-size: 12px; color: var(--muted); font-weight: 400; }

  table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  th {
    text-align: left; color: var(--muted); font-size: 11.5px; text-transform: uppercase;
    letter-spacing: .5px; padding: 8px 10px; border-bottom: 1px solid var(--border);
  }
  td { padding: 10px; border-bottom: 1px solid rgba(36,48,76,.5); vertical-align: middle; }
  tr:hover td { background: rgba(79,70,229,.05); }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }

  .badge { display: inline-block; padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 600; }
  .badge.green { background: rgba(34,197,94,.12); color: var(--green); }
  .badge.red { background: rgba(239,68,68,.12); color: var(--red); }
  .badge.amber { background: rgba(245,158,11,.12); color: var(--amber); }
  .badge.gray { background: rgba(148,163,184,.12); color: #a8b6d8; }
  .badge.cyan { background: rgba(56,189,248,.12); color: var(--cyan); }

  .row-actions { display: flex; gap: 6px; justify-content: flex-end; flex-wrap: wrap; }
  .mini-btn {
    padding: 5px 10px; border-radius: 7px; border: 1px solid var(--border); cursor: pointer;
    background: var(--panel-2); color: var(--text); font-size: 12px; transition: all .12s;
  }
  .mini-btn:hover { border-color: var(--accent); color: var(--accent-2); }
  .mini-btn.warn:hover { border-color: var(--amber); color: var(--amber); }
  .mini-btn.danger:hover { border-color: var(--red); color: var(--red); }
  .mini-btn:disabled { opacity: .45; cursor: wait; }

  .toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 14px; flex-wrap: wrap; }
  .toolbar input[type=search] {
    flex: 1; min-width: 200px; padding: 10px 14px; border-radius: 10px;
    border: 1px solid var(--border); background: var(--panel-2); color: var(--text); font-size: 14px; outline: none;
  }
  .toolbar input[type=search]:focus { border-color: var(--accent); }
  .toolbar .page-info { color: var(--muted); font-size: 13px; white-space: nowrap; }
  .warn-list { list-style: none; }
  .warn-list li { padding: 10px 14px; border-radius: 10px; background: rgba(245,158,11,.06);
    border: 1px solid rgba(245,158,11,.25); color: #fcd9a8; margin-bottom: 8px; font-size: 13.5px; }
  .ok-note { color: var(--green); font-size: 13.5px; }

  .spark { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; }
  .grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 18px; }
  .muted { color: var(--muted); }
  .right { text-align: right; }
  .freshness-bar { height: 6px; border-radius: 999px; background: var(--panel-2); overflow: hidden; margin-top: 8px; }
  .freshness-bar > i { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, var(--green), var(--cyan)); transition: width .4s ease; }

  #toast {
    position: fixed; bottom: 24px; right: 24px; z-index: 50;
    background: var(--panel-2); border: 1px solid var(--border); border-radius: 12px;
    padding: 12px 18px; font-size: 13.5px; box-shadow: var(--shadow);
    transform: translateY(80px); opacity: 0; transition: transform .25s, opacity .25s;
  }
  #toast.show { transform: none; opacity: 1; }
  #toast.err { border-color: rgba(239,68,68,.5); color: #fca5a5; }
  .loading { color: var(--muted); font-size: 13.5px; padding: 18px 0; }

  @media (max-width: 640px) {
    header { padding: 12px 16px; }
    main { padding: 14px 16px 50px; }
    .cards { grid-template-columns: 1fr 1fr; }
  }
</style>
</head>
<body>

<!-- ── Login ─────────────────────────────────────────────── -->
<div id="login-screen">
  <div class="login-card">
    <h1><span class="logo">S</span> Stratoscan Admin</h1>
    <p class="sub">Sign in with an account that has the admin role.</p>
    <form id="login-form" autocomplete="on">
      <div class="field">
        <label for="email">Email</label>
        <input id="email" type="email" required placeholder="you@stratoscan.com" autocomplete="email">
      </div>
      <div class="field">
        <label for="password">Password</label>
        <input id="password" type="password" required placeholder="••••••••" autocomplete="current-password">
      </div>
      <div class="login-error" id="login-error"></div>
      <button class="btn-primary" id="login-btn" type="submit">Sign in</button>
    </form>
    <p class="sub" style="margin-top:18px;font-size:12px;">
      Admin role is granted via <span class="mono">ADMIN_EMAILS</span> in .env
      (granted automatically at login), or by another admin on the Users tab.
    </p>
  </div>
</div>

<!-- ── App shell ─────────────────────────────────────────── -->
<div id="app">
  <header>
    <div class="brand"><span class="logo">S</span> Stratoscan Admin</div>
    <div class="whoami">
      <span id="who-email"></span>
      <span class="pill admin">admin</span>
      <button class="btn-ghost" id="logout-btn">Sign out</button>
    </div>
  </header>

  <nav>
    <button data-tab="overview" class="active">Overview</button>
    <button data-tab="users">Users</button>
    <button data-tab="jobs">Jobs</button>
  </nav>

  <main>
    <!-- Overview -->
    <section id="tab-overview" class="panel active">
      <div class="cards" id="ov-cards">
        <div class="card"><div class="k">API Status</div><div class="v" id="ov-status">—</div><div class="d" id="ov-uptime"></div></div>
        <div class="card"><div class="k">Database</div><div class="v" id="ov-db">—</div><div class="d" id="ov-db-latency"></div></div>
        <div class="card"><div class="k">Stocks in universe</div><div class="v" id="ov-stocks">—</div><div class="d" id="ov-freshness"></div></div>
        <div class="card"><div class="k">Accounts</div><div class="v" id="ov-users">—</div><div class="d" id="ov-users-detail"></div></div>
        <div class="card"><div class="k">Admins</div><div class="v" id="ov-admins">—</div><div class="d">with admin role</div></div>
        <div class="card"><div class="k">Scheduler</div><div class="v" id="ov-sched">—</div><div class="d" id="ov-sched-detail"></div></div>
      </div>

      <div class="grid2">
        <div class="section">
          <h2>Configuration warnings <span class="muted" id="ov-warn-count"></span></h2>
          <ul class="warn-list" id="ov-warnings"></ul>
          <div class="ok-note" id="ov-ok" style="display:none;">✅ All configuration validated.</div>
        </div>
        <div class="section">
          <h2>Services <span class="muted">configured</span></h2>
          <table>
            <tbody id="ov-services"></tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- Users -->
    <section id="tab-users" class="panel">
      <div class="section">
        <h2>Accounts</h2>
        <div class="toolbar">
          <input type="search" id="user-search" placeholder="Search by email…">
          <span class="page-info" id="user-page-info"></span>
        </div>
        <div style="overflow-x:auto;">
          <table>
            <thead><tr>
              <th>ID</th><th>Email</th><th>Plan</th><th>Verified</th><th>Role</th><th>Created</th><th class="right">Actions</th>
            </tr></thead>
            <tbody id="user-rows"></tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- Jobs -->
    <section id="tab-jobs" class="panel">
      <div class="section">
        <h2>Background jobs <span class="muted" id="job-refresh-hint"></span></h2>
        <div style="overflow-x:auto;">
          <table>
            <thead><tr>
              <th>Job</th><th>Schedule</th><th>Last run</th><th>Status</th><th>Records</th><th>Duration</th><th>Staleness</th>
            </tr></thead>
            <tbody id="job-rows"></tbody>
          </table>
        </div>
      </div>
    </section>
  </main>
</div>

<div id="toast"></div>

<script>
"use strict";
const TOKEN_KEY = "stratoscan_admin_token";
const api = {
  async req(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    const tok = localStorage.getItem(TOKEN_KEY);
    if (tok) headers["Authorization"] = "Bearer " + tok;
    if (opts.body) headers["Content-Type"] = "application/json";
    const res = await fetch(path, Object.assign({}, opts, { headers }));
    if (res.status === 401 || res.status === 403) {
      localStorage.removeItem(TOKEN_KEY);
      showLogin();
      throw new Error("Session expired — sign in again");
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok) {
      const detail = (data && data.detail) ? data.detail : ("HTTP " + res.status);
      throw new Error(detail);
    }
    return data;
  },
  overview: () => api.req("/admin/overview"),
  users: (q, off) => api.req("/admin/users?limit=50&offset=" + off + (q ? "&search=" + encodeURIComponent(q) : "")),
  updateUser: (id, body) => api.req("/admin/users/" + id, { method: "PATCH", body: JSON.stringify(body) }),
  jobs: () => api.req("/admin/jobs"),
  login: (email, password) => api.req("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
};

let state = { users: [], total: 0, offset: 0, search: "" };
let refreshTimer = null;

// ── Auth ──────────────────────────────────────────────────
function showLogin() {
  document.getElementById("login-screen").style.display = "grid";
  document.getElementById("app").style.display = "none";
}
function showApp(email) {
  document.getElementById("login-screen").style.display = "none";
  document.getElementById("app").style.display = "block";
  document.getElementById("who-email").textContent = email || "";
  setIntervalSafe();
}
function setIntervalSafe() {
  clearInterval(refreshTimer);
  refreshTimer = setInterval(() => {
    const tab = document.querySelector("nav button.active");
    if (tab) switchTab(tab.dataset.tab, true);
  }, 15000);
}

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = document.getElementById("login-btn");
  const err = document.getElementById("login-error");
  btn.disabled = true; err.textContent = "";
  try {
    const email = document.getElementById("email").value.trim().toLowerCase();
    const res = await api.login(email, document.getElementById("password").value);
    localStorage.setItem(TOKEN_KEY, res.access_token);
    toast("Signed in");
    showApp(email);
    switchTab("overview");
  } catch (ex) {
    err.textContent = ex.message;
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("logout-btn").addEventListener("click", () => {
  localStorage.removeItem(TOKEN_KEY);
  showLogin();
});

// ── Tabs ──────────────────────────────────────────────────
document.querySelectorAll("nav button").forEach((b) => {
  b.addEventListener("click", () => switchTab(b.dataset.tab));
});
function switchTab(name, silent) {
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + name));
  if (name === "overview") loadOverview();
  if (name === "users") loadUsers();
  if (name === "jobs") loadJobs();
  if (!silent) toast("Viewing " + name);
}

// ── Overview ──────────────────────────────────────────────
async function loadOverview() {
  const set = (id, v) => { document.getElementById(id).textContent = v; };
  try {
    const o = await api.overview();
    set("ov-status", o.status === "ok" ? "● Online" : "● Degraded");
    const up = o.uptime_seconds || 0;
    set("ov-uptime", "up " + fmtDuration(up));

    const db = o.database || {};
    set("ov-db", db.configured ? (db.alive === false ? "Down" : "Healthy") : "Not configured");
    set("ov-db-latency", db.alive ? (db.latency_ms != null ? db.latency_ms + " ms" : "") : (db.error ? db.error : ""));

    const st = o.stocks || {};
    set("ov-stocks", st.count != null ? st.count.toLocaleString() : "—");
    const fr = (st.freshness && st.freshness.total_rows) ? st.freshness : null;
    if (fr) {
      const pct = Math.max(0, Math.min(100, 100 - ((fr.stale_count || 0) / Math.max(1, fr.total_rows)) * 100));
      set("ov-freshness", (fr.stale_count || 0) + " stale of " + fr.total_rows.toLocaleString());
      const bar = document.createElement("div");
    } else {
      set("ov-freshness", st.count != null ? "freshness unknown" : "");
    }

    const us = o.users || {};
    set("ov-users", us.total != null ? us.total.toLocaleString() : "—");
    set("ov-users-detail", us.total != null ? (us.verified || 0) + " verified · " + planMix(us.plans) : "");
    set("ov-admins", us.admins != null ? us.admins : "—");

    const sched = (o.jobs && o.jobs.scheduler) || {};
    set("ov-sched", sched.alive ? "● Alive" : sched.last_heartbeat_at ? "● Down" : "—");
    set("ov-sched-detail", sched.alive ? "pid " + (sched.pid || "?") : (sched.last_heartbeat_at ? "last beat " + fmtAge(sched.heartbeat_age_seconds) : "no heartbeat yet"));

    const warns = o.warnings || [];
    const list = document.getElementById("ov-warnings");
    list.innerHTML = "";
    set("ov-warn-count", "(" + warns.length + ")");
    document.getElementById("ov-ok").style.display = warns.length ? "none" : "block";
    warns.forEach((w) => { const li = document.createElement("li"); li.textContent = w; list.appendChild(li); });

    const svc = document.getElementById("ov-services");
    svc.innerHTML = "";
    Object.entries(o.config || {}).forEach(([k, v]) => {
      const tr = document.createElement("tr");
      tr.innerHTML = "<td>" + esc(k) + "</td><td class='right'><span class='badge " + (v ? "green" : "gray") + "'>" + (v ? "yes" : "no") + "</span></td>";
      svc.appendChild(tr);
    });
  } catch (ex) {
    toast(ex.message, true);
  }
}

// ── Users ─────────────────────────────────────────────────
document.getElementById("user-search").addEventListener("input", debounce(() => {
  state.search = document.getElementById("user-search").value.trim();
  state.offset = 0;
  loadUsers();
}, 300));

async function loadUsers(dir) {
  if (dir === 1) state.offset += 50;
  if (dir === -1) state.offset = Math.max(0, state.offset - 50);
  const rows = document.getElementById("user-rows");
  rows.innerHTML = '<tr><td colspan="7"><div class="loading">Loading accounts…</div></td></tr>';
  try {
    const res = await api.users(state.search, state.offset);
    state.users = res.users; state.total = res.total;
    document.getElementById("user-page-info").textContent =
      "Showing " + (res.total ? state.offset + 1 : 0) + "–" + Math.min(state.offset + res.users.length, res.total) + " of " + res.total;
    rows.innerHTML = "";
    if (!res.users.length) {
      rows.innerHTML = '<tr><td colspan="7"><div class="loading">No accounts found.</div></td></tr>';
      return;
    }
    res.users.forEach((u) => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td class='mono'>" + u.id + "</td>" +
        "<td>" + esc(u.email) + "</td>" +
        "<td><span class='badge " + (u.plan === "premium" ? "cyan" : "gray") + "'>" + esc(u.plan) + "</span></td>" +
        "<td><span class='badge " + (u.email_verified ? "green" : "amber") + "'>" + (u.email_verified ? "verified" : "pending") + "</span></td>" +
        "<td><span class='badge " + (u.is_admin ? "cyan" : "gray") + "'>" + (u.is_admin ? "admin" : "user") + "</span></td>" +
        "<td class='muted'>" + (u.created_at ? new Date(u.created_at).toLocaleDateString() : "—") + "</td>" +
        "<td><div class='row-actions'>" +
          (u.is_admin ? "<button class='mini-btn danger' data-act='demote' data-id='" + u.id + "'>Revoke admin</button>"
                      : "<button class='mini-btn' data-act='promote' data-id='" + u.id + "'>Make admin</button>") +
          "<button class='mini-btn' data-act='premium' data-id='" + u.id + "' " + (u.plan === "premium" ? "disabled" : "") + ">Set premium</button>" +
          (u.email_verified ? "" : "<button class='mini-btn warn' data-act='verify' data-id='" + u.id + "'>Mark verified</button>") +
        "</div></td>";
      rows.appendChild(tr);
    });
  } catch (ex) {
    rows.innerHTML = '<tr><td colspan="7"><div class="loading">' + esc(ex.message) + "</div></td></tr>";
  }
}

document.getElementById("user-rows").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  btn.disabled = true;
  const id = +btn.dataset.id;
  const act = btn.dataset.act;
  const body = {};
  if (act === "promote") body.is_admin = true;
  if (act === "demote") body.is_admin = false;
  if (act === "premium") body.plan = "premium";
  if (act === "verify") body.email_verified = true;
  try {
    const u = await api.updateUser(id, body);
    toast(u.email + " updated (" + act + ")");
    loadUsers();
  } catch (ex) {
    toast(ex.message, true);
    btn.disabled = false;
  }
});

// ── Jobs ──────────────────────────────────────────────────
async function loadJobs() {
  const rows = document.getElementById("job-rows");
  try {
    const j = await api.jobs();
    rows.innerHTML = "";
    (j.jobs || []).forEach((job) => {
      const tr = document.createElement("tr");
      const status = job.last_status || "never";
      const statusBadge = status === "success" ? "green" : (status === "failed" ? "red" : (status === "running" ? "cyan" : "gray"));
      const staleBadge = job.stale ? "red" : "gray";
      tr.innerHTML =
        "<td><b>" + esc(job.label) + "</b><br><span class='muted mono'>" + esc(job.job_name) + "</span></td>" +
        "<td class='muted'>" + esc(job.schedule || "—") + "</td>" +
        "<td class='muted'>" + (job.last_finished_at ? new Date(job.last_finished_at).toLocaleString() : "—") + "</td>" +
        "<td><span class='badge " + statusBadge + "'>" + status + "</span>" +
          ((job.consecutive_failures || 0) > 1 ? " <span class='badge red'>×" + job.consecutive_failures + " fails</span>" : "") + "</td>" +
        "<td class='mono'>" + (job.last_records != null ? job.last_records.toLocaleString() : "—") + "</td>" +
        "<td class='mono'>" + (job.last_duration_ms != null ? Math.round(job.last_duration_ms) + " ms" : "—") + "</td>" +
        "<td><span class='badge " + staleBadge + "'>" + (job.stale ? esc(job.stale_reason || "stale") : "fresh") + "</span></td>";
      rows.appendChild(tr);
    });
    const sched = j.scheduler || {};
    document.getElementById("job-refresh-hint").textContent =
      "scheduler " + (sched.alive ? "alive" : "down") + " · heartbeat " + (sched.last_heartbeat_at ? new Date(sched.last_heartbeat_at).toLocaleString() : "never");
  } catch (ex) {
    rows.innerHTML = '<tr><td colspan="7"><div class="loading">' + esc(ex.message) + "</div></td></tr>";
  }
}

// ── Helpers ───────────────────────────────────────────────
function toast(msg, isErr) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = isErr ? "err show" : "show";
  clearTimeout(t._h);
  t._h = setTimeout(() => { t.className = ""; }, 2600);
}
function debounce(fn, ms) {
  let h; return function () { clearTimeout(h); h = setTimeout(fn, ms); };
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtDuration(sec) {
  if (sec < 60) return Math.round(sec) + "s";
  if (sec < 3600) return Math.round(sec / 60) + "m";
  return Math.round(sec / 3600) + "h";
}
function fmtAge(sec) {
  if (sec == null) return "—";
  if (sec < 60) return Math.round(sec) + "s ago";
  if (sec < 3600) return Math.round(sec / 60) + "m ago";
  return Math.round(sec / 3600) + "h ago";
}
function planMix(plans) {
  if (!plans) return "";
  return Object.entries(plans).map(([k, v]) => k + ": " + v).join(" · ");
}

// ── Boot ──────────────────────────────────────────────────
(function () {
  const tok = localStorage.getItem(TOKEN_KEY);
  if (tok) {
    // Try /auth/me to confirm the token is still valid + surface the email.
    api.req("/auth/me").then((me) => {
      showApp(me.email);
      loadOverview();
    }).catch(() => { /* showLogin handled in req */ });
  } else {
    showLogin();
  }
})();
</script>
</body>
</html>
"""
