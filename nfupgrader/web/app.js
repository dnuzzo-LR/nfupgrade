"use strict";
var logOffset = 0;
var logFile = "";   // trace file the log pane is showing (server picks the newest)

function getJSON(url, cb, onError) {
  var x = new XMLHttpRequest();
  x.open("GET", url, true);
  x.onreadystatechange = function () {
    if (x.readyState !== 4) return;
    var data = null;
    if (x.status === 200) {
      try { data = JSON.parse(x.responseText); } catch (e) { data = null; }
    }
    if (data !== null) cb(data);
    else if (onError) onError(x.status);
  };
  x.send();
}

function postJSON(url, obj, cb) {
  var x = new XMLHttpRequest();
  x.open("POST", url, true);
  x.setRequestHeader("Content-Type", "application/json");
  x.onreadystatechange = function () {
    if (x.readyState !== 4) return;
    var body = {};
    // A proxy error page or a dropped connection is not JSON: never leave the
    // caller waiting on an exception.
    try {
      if (x.responseText) body = JSON.parse(x.responseText);
    } catch (e) {
      body = { error: "HTTP " + x.status };
    }
    if (!body || typeof body !== "object") body = { error: "HTTP " + x.status };
    cb(x.status, body);
  };
  x.send(JSON.stringify(obj));
}

function spin(id, on) {
  var el = document.getElementById(id);
  if (el) el.style.display = on ? "inline-block" : "none";
}

// Panels that have loaded at least once — the spinner shows only on the first
// load; background re-polls refresh silently (stale data stays on screen).
var loaded = {};

function loadingHTML(text) {
  return "<div class='loading'><span class='spinner'></span> " + text + "</div>";
}

function initTabs() {
  var tabs = document.querySelectorAll(".tab");
  var panels = document.querySelectorAll(".panel");
  function show(name) {
    tabs.forEach(function (t) { t.classList.toggle("active", t.getAttribute("data-tab") === name); });
    panels.forEach(function (p) { p.classList.toggle("active", p.id === "tab-" + name); });
    try { localStorage.setItem("nfu_tab", name); } catch (e) {}
  }
  tabs.forEach(function (t) { t.onclick = function () { show(t.getAttribute("data-tab")); }; });
  var saved;
  try { saved = localStorage.getItem("nfu_tab"); } catch (e) {}
  var names = [];
  tabs.forEach(function (t) { names.push(t.getAttribute("data-tab")); });
  // A saved tab that no longer exists (e.g. the old "site" tab) falls back to Overview.
  show(saved && names.indexOf(saved) >= 0 ? saved : "overview");
}

function refreshProgress() {
  getJSON("/api/progress", function (p) {
    document.getElementById("phase").textContent = p.phase;
    document.getElementById("status").textContent = p.status;
    document.getElementById("current").textContent =
      (p.current || "-") + (p.label ? " — " + p.label : "");
    document.getElementById("counter").textContent = p.completed + " / " + p.total;
    var pct = p.total ? Math.round((p.completed / p.total) * 100) : 0;
    document.getElementById("bar-fill").style.width = pct + "%";
  });
}

function refreshLog() {
  var min = document.getElementById("min-sev").value;
  getJSON("/api/log?offset=" + logOffset + "&file=" + encodeURIComponent(logFile) +
          (min ? "&min=" + min : ""), function (d) {
    logOffset = d.next_offset;
    var pre = document.getElementById("log");
    if (d.reset) pre.innerHTML = "";   // a new trace file (new run or phase switch)
    logFile = d.file || "";
    d.entries.forEach(function (e) {
      var line = document.createElement("div");
      line.className = "sev-" + e.severity;
      line.textContent = (e.ts ? e.ts + " " : "") + e.severity + "  " + e.msg;
      pre.appendChild(line);
    });
    pre.scrollTop = pre.scrollHeight;
  });
}

function cell(v) { return "<td>" + (v === null || v === undefined || v === "" ? "-" : esc(v)) + "</td>"; }

var lastInfo = null;   // last /api/info response; used when /api/site lacks this host
var localOpen = true;
try { localOpen = localStorage.getItem("nfu_local_open") !== "0"; } catch (e) {}

function applyLocalOpen() {
  document.getElementById("local-detail").style.display = localOpen ? "" : "none";
  var t = document.getElementById("local-toggle");
  if (t) {
    t.textContent = localOpen ? "▾" : "▸";
    t.setAttribute("aria-expanded", localOpen ? "true" : "false");
  }
}

function toggleLocal() {
  localOpen = !localOpen;
  try { localStorage.setItem("nfu_local_open", localOpen ? "1" : "0"); } catch (e) {}
  applyLocalOpen();
}

function localFromInfo(i) {
  return { name: i.host, role: (i.topology || {}).role, reachable: true, is_local: true, info: i };
}

function refreshSite() {
  if (!loaded.site) spin("site-spin", true);
  getJSON("/api/site", function (s) {
    spin("site-spin", false);
    loaded.site = true;
    var tb = document.querySelector("#site-table tbody");
    var detail = document.getElementById("local-detail");
    // Drop every row except the persistent detail row, which is moved, not rebuilt,
    // so the host overview survives the 15 s site poll.
    Array.prototype.slice.call(tb.rows).forEach(function (r) {
      if (r !== detail) tb.removeChild(r);
    });
    var hosts = s.hosts.slice();
    var hasLocal = hosts.some(function (h) { return h.is_local; });
    if (!hasLocal && lastInfo) hosts.unshift(localFromInfo(lastInfo));
    hosts.forEach(function (h) {
      var i = h.info || {};
      var v = i.versions || {}, l = i.loads || {}, g = i.gr || {};
      var tr = document.createElement("tr");
      if (h.is_local) tr.className = "local";
      var reach = h.reachable
        ? "<span class='pill ok'>ok</span>"
        : "<span class='pill down'>unreachable</span>";
      var app = !h.reachable ? "<span class='pill unknown'>?</span>"
        : (i.app_up ? "<span class='pill ok'>UP</span>" : "<span class='pill down'>DOWN</span>");
      var tog = h.is_local
        ? "<td class='tog'><button id='local-toggle' class='toggle' type='button' " +
          "aria-label='Show host details'></button></td>"
        : "<td class='tog'></td>";
      tr.innerHTML = tog +
        "<td class='hostcell'>" + esc(h.name + (h.is_local ? " (this)" : "")) + "</td>" +
        cell(h.role) + "<td>" + reach + "</td><td>" + app + "</td>" +
        cell(v.core) + cell(l.installed) + cell(l.staged) + cell(g.transfer);
      tb.appendChild(tr);
      if (h.is_local) tb.appendChild(detail);
    });
    applyLocalOpen();
  });
}

function row(label, value) {
  return "<div class='k'>" + label + "</div><div class='v'>" +
         (value === null || value === undefined || value === "" ? "-" : value) + "</div>";
}

function refreshInfo() {
  getJSON("/api/info", function (i) {
    lastInfo = i;
    loaded.info = true;
    var t = i.topology, v = i.versions, l = i.loads, g = i.gr;
    var html = "";
    html += row("Host", i.host);
    html += row("Topology", t.kind + (t.role ? " (" + t.role + ")" : "") +
                (t.host_count ? " — " + t.host_count + " hosts" : ""));
    html += row("Mate", t.mate);
    html += row("Application", i.app_up ? "UP" : "DOWN");
    html += row("CORE", v.core);
    html += row("PATCH", v.patch);
    html += row("IPATCH", v.ipatch);
    html += row("Installed load", l.installed);
    html += row("Staged load", l.staged);
    html += row("Saved load", l.saved);
    html += row("GR transfer", g.transfer);
    html += row("GR restore", g.restore ? "in progress" : "none");
    (i.disk || []).forEach(function (d) {
      html += row("Disk " + d.mount, d.use + " used");
    });
    if (i.app_detail) {
      var inc = i.app_detail.incinfo;
      if (inc && typeof inc === "object") {
        Object.keys(inc).forEach(function (k) {
          var val = inc[k];
          if (val !== null && typeof val === "object") val = JSON.stringify(val);
          html += row("incinfo · " + k, val);
        });
      } else {
        html += row("incinfo", inc);
      }
      html += row("rdb", i.app_detail.rdb_version);
      html += row("dbcheck errors", i.app_detail.dbcheck_errors);
    }
    document.getElementById("about-grid").innerHTML = html;
    document.getElementById("links").textContent =
      i.links ? i.links : (i.app_up ? "(no links reported)" : "application down — unavailable");
    var badge = document.getElementById("app-badge");
    badge.textContent = i.app_up ? "APP UP" : "APP DOWN";
    badge.className = "badge " + (i.app_up ? "up" : "down");
  });
}

function renderChecks(el, data, overridable) {
  var html = "<table class='checks'><tr><th></th><th>Check</th><th>Detail</th>" +
             (overridable ? "<th>Override</th><th>Fix</th>" : "") + "</tr>";
  data.results.forEach(function (r) {
    var mark = r.passed ? "✅" : (r.severity === "ERROR" ? "❌" : "⚠️");
    var extra = "";
    if (overridable) {
      extra = "<td>" + (r.blocking
        ? "<input type='checkbox' class='ovr' data-name='" + esc(r.name) + "'>" : "") + "</td>" +
        "<td>" + (r.fix
        ? "<button type='button' class='fix' data-name='" + esc(r.name) + "' data-fix='" +
          esc(r.fix) + "' title='" + esc(r.fix) + "'>Fix</button>" : "") + "</td>";
    }
    html += "<tr class='" + (r.passed ? "pass" : "fail-" + esc(r.severity)) + "'>" +
            "<td>" + mark + "</td><td>" + esc(r.name) + " — " + esc(r.description) + "</td>" +
            "<td>" + esc(r.detail || "") + "</td>" + extra + "</tr>";
  });
  html += "</table>";
  el.innerHTML = html;
}

var preflight = null;  // last pre-flight /api/checks response for the selected mode
var fixing = false;    // a check fix is running: Launch and Run checks stay disabled

function overriddenNames() {
  var boxes = document.querySelectorAll("#preflight input.ovr:checked");
  return Array.prototype.map.call(boxes, function (b) { return b.getAttribute("data-name"); });
}

function updateLaunchState() {
  var btn = document.getElementById("btn-launch");
  var why = document.getElementById("override-msg");
  if (!preflight) { btn.disabled = true; why.textContent = ""; return; }
  var total = preflight.blocking.length;
  var missing = total - overriddenNames().length;
  var reason = document.getElementById("reason").value.trim();
  var msg = "";
  if (missing > 0) msg = missing + " of " + total + " blocking checks not overridden";
  else if (total > 0 && !reason) msg = "reason required to override";
  why.textContent = msg;
  btn.disabled = fixing || msg !== "";
}

function runPreflight() {
  var mode = document.getElementById("mode").value;
  getJSON("/api/checks?when=preflight&phase=" + mode, function (data) {
    preflight = data;
    renderChecks(document.getElementById("preflight"), data, true);
    document.getElementById("override-box").style.display =
      data.blocking.length > 0 ? "block" : "none";
    updateLaunchState();
  }, function (status) {
    // Keep the old table but make it usable again (a fix may have disabled it).
    Array.prototype.forEach.call(document.querySelectorAll("#preflight button.fix"),
                                 function (b) { b.disabled = false; b.textContent = "Fix"; });
    document.getElementById("btn-check").disabled = false;
    var msg = document.getElementById("fix-msg");
    var why = "could not re-run checks (HTTP " + status + ")";
    msg.textContent = msg.textContent ? msg.textContent + " — " + why : why;
    updateLaunchState();
  });
}

function runFix(btn) {
  var name = btn.getAttribute("data-name");
  var host = lastInfo && lastInfo.host ? lastInfo.host : "this host";
  if (!window.confirm("Run as root on " + host + ":\n\n" + btn.getAttribute("data-fix") +
                      "\n\nContinue?")) return;
  Array.prototype.forEach.call(document.querySelectorAll("#preflight button.fix"),
                               function (b) { b.disabled = true; });
  // No launch or re-check while a root fix is changing the system.
  fixing = true;
  document.getElementById("btn-launch").disabled = true;
  document.getElementById("btn-check").disabled = true;
  btn.textContent = "Fixing…";
  var msg = document.getElementById("fix-msg");
  msg.textContent = "Fixing " + name + "…";
  postJSON("/api/checks/fix", { name: name, phase: document.getElementById("mode").value },
    function (status, d) {
      fixing = false;
      document.getElementById("btn-check").disabled = false;
      updateLaunchState();
      if (status === 200 && d.fix) {
        var detail = d.fix.detail || "";
        if (!d.fix.ok) msg.textContent = "❌ " + name + ": " + detail;
        else if (d.fix.ok && d.result && d.result.passed) {
          msg.textContent = (detail.indexOf("WARNING") === 0 ? "⚠️ " : "✅ ") + name + ": " + detail;
        }
        else {
          msg.textContent = "⚠️ " + name + ": fix applied but the check still fails — " +
            ((d.result && d.result.detail) || detail);
        }
      }
      else if (status === 200) msg.textContent = "✅ " + name + ": already passing";
      else msg.textContent = "❌ " + name + ": " + (d.error || ("HTTP " + status));
      runPreflight();
    });
}

function resetPreflight() {
  preflight = null;
  document.getElementById("preflight").innerHTML = "";
  document.getElementById("override-box").style.display = "none";
  document.getElementById("fix-msg").textContent = "";
  updateLaunchState();
}

function launchConfirmText(mode) {
  var base = "Start the " + mode.toUpperCase() +
             " now? This runs the real upgrade and cannot be paused.";
  var ticked = overriddenNames();
  var over = preflight.results.filter(function (r) {
    return r.blocking && ticked.indexOf(r.name) >= 0;
  });
  var warn = preflight.results.filter(function (r) {
    return !r.passed && r.severity !== "ERROR";
  });
  if (!over.length && !warn.length) return base;
  var lines = ["WARNING: launching with unresolved pre-flight failures.", ""];
  if (over.length) {
    lines.push("Overridden:");
    over.forEach(function (r) { lines.push("  - " + r.name + " — " + (r.detail || "")); });
    lines.push("");
  }
  if (warn.length) {
    lines.push("Warnings:");
    warn.forEach(function (r) { lines.push("  - " + r.name + " — " + (r.detail || "")); });
    lines.push("");
  }
  lines.push("Upgrading with unresolved pre-flight failures may leave the system " +
             "in an unsupported state.", "", base);
  return lines.join("\n");
}

function launch() {
  var mode = document.getElementById("mode").value;
  var reason = document.getElementById("reason").value;
  if (!preflight) return;
  if (!window.confirm(launchConfirmText(mode))) return;
  postJSON("/api/launch", { mode: mode, overrides: overriddenNames(), reason: reason },
    function (status, body) {
      var msg = document.getElementById("launch-msg");
      if (status === 202) {
        if (body.snapshot) { msg.textContent = "Taking before snapshot…"; refreshReportsJob(); }
        else { msg.textContent = "Launched " + mode + "…"; }
      }
      else if (status === 409 && body.not_overridden) {
        document.getElementById("btn-launch").disabled = true;  // until re-run lands
        msg.textContent = "Blocked — not overridden: " +
          body.not_overridden.join(", ") + ". Checks re-run.";
        runPreflight();
      }
      // Any other 409 (snapshot in progress, upgrade already running) is not a
      // pre-flight verdict: leave the button and checks alone.
      else if (status === 409) { msg.textContent = "Not launched: " + (body.error || status); }
      else { msg.textContent = "Error: " + (body.error || status); }
    });
}

function refreshPostflight() {
  var mode = document.getElementById("mode").value;
  getJSON("/api/checks?when=postflight&phase=" + mode, function (data) {
    if (data.results.length) renderChecks(document.getElementById("postflight"), data);
  });
}

function esc(s) {
  return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

var snapshots = [];
var reportsJob = null;
var cmpTouched = false;   // user picked A/B by hand; stop auto-selecting the latest pair

function jobText(j) {
  return "Taking " + j.label + " snapshot " + j.index + "/" + j.total +
         (j.current ? " — " + j.current : "") + "…";
}

function refreshReportsJob() {
  getJSON("/api/reports/job", function (d) {
    var was = reportsJob;
    reportsJob = d.job;
    document.getElementById("reports-job").textContent = reportsJob ? jobText(reportsJob) : "";
    document.getElementById("btn-reports-run").disabled = !!reportsJob;
    var msg = document.getElementById("launch-msg");
    if (reportsJob && reportsJob.label === "before") msg.textContent = jobText(reportsJob);
    else if (was && was.label === "before") msg.textContent = "Before snapshot done — upgrade starting.";
    if (was && !reportsJob) refreshReports();
    if (d.launch_error) msg.textContent = "Launch failed: " + d.launch_error;
    else if (!reportsJob && d.notice) {
      msg.textContent = d.notice;
      document.getElementById("reports-job").textContent = d.notice;
    }
  });
}

// The pair to compare by default (snapshots is newest first). Anchor on the
// newest before: its own after if taken, else whatever is newest since it (the
// upgrade may still be running). Only ids from snapshots are returned.
function defaultPair() {
  var bi = -1, i, j;
  for (i = 0; i < snapshots.length; i++) {
    if (snapshots[i].label === "before") { bi = i; break; }
  }
  if (bi >= 0) {
    var before = snapshots[bi];
    for (j = 0; j < bi; j++) {
      if (snapshots[j].label === "after" && snapshots[j].pair_of === before.id) {
        return [before.id, snapshots[j].id];
      }
    }
    if (bi > 0) return [before.id, snapshots[0].id];
  }
  if (snapshots.length >= 2) return [snapshots[1].id, snapshots[0].id];
  var only = snapshots.length ? snapshots[0].id : "";
  return [only, only];
}

function fillSelect(id, chosen) {
  document.getElementById(id).innerHTML = snapshots.map(function (s) {
    return "<option value='" + esc(s.id) + "'" + (s.id === chosen ? " selected" : "") + ">" +
           esc(s.id + " (" + s.label + ", " + s.status + ")") + "</option>";
  }).join("");
}

function refreshReports() {
  getJSON("/api/reports", function (d) {
    var keepA = document.getElementById("cmp-a").value;
    var keepB = document.getElementById("cmp-b").value;
    snapshots = d.snapshots;
    document.querySelector("#reports-list tbody").innerHTML = snapshots.length
      ? snapshots.map(function (s) {
          return "<tr><td>" + esc(s.id) + "</td><td>" + esc(s.label) + "</td><td>" +
                 esc(s.started) + "</td><td>" + esc(s.status) + "</td><td>" +
                 esc(s.note || "") + "</td></tr>";
        }).join("")
      : "<tr><td colspan='5'>No snapshots yet.</td></tr>";
    var pair = defaultPair();
    fillSelect("cmp-a", cmpTouched && keepA ? keepA : pair[0]);
    fillSelect("cmp-b", cmpTouched && keepB ? keepB : pair[1]);
    if (!cmpTouched) {
      var curA = document.getElementById("cmp-a").value;
      var curB = document.getElementById("cmp-b").value;
      if (curA && curB && (curA + "|" + curB) !== lastCompared) runCompare();
    }
  });
}

var BADGE = { unchanged: "✅", changed: "●", failed: "❌", missing: "⚠️" };
var lastCompared = "";   // "a|b" of the pair Compare last ran for

function runCompare() {
  var a = document.getElementById("cmp-a").value;
  var b = document.getElementById("cmp-b").value;
  var body = document.querySelector("#cmp-table tbody");
  if (!a || !b) { body.innerHTML = "<tr><td>Select two snapshots.</td></tr>"; return; }
  lastCompared = a + "|" + b;
  body.innerHTML = "<tr><td>" + loadingHTML("Comparing…") + "</td></tr>";
  getJSON("/api/reports/compare?a=" + encodeURIComponent(a) + "&b=" + encodeURIComponent(b),
    function (d) {
      body.innerHTML = "<tr><th></th><th>Report</th><th>Result</th></tr>" +
        d.reports.map(function (r, i) {
          var res = r.status + ((r.added || r.removed) ? " [+" + r.added + " −" + r.removed + "]" : "");
          return "<tr class='cmp-row cmp-" + esc(r.status) + "' data-i='" + i + "' data-report='" +
                 esc(r.name) + "'><td>" + BADGE[r.status] + "</td><td>" + esc(r.name) +
                 "</td><td>" + esc(res) + "</td></tr>" +
                 "<tr class='cmp-diff' id='cmp-diff-" + i + "' style='display:none'>" +
                 "<td colspan='3'></td></tr>";
        }).join("");
    });
}

function unifiedHTML(rows) {
  if (!rows.length) return "<p>No differences.</p>";
  return "<pre class='diff'>" + rows.map(function (r) {
    var cls = r.op === "+" ? "add" : (r.op === "-" ? "del" : (r.op === "@" ? "hunk" : "ctx"));
    return "<span class='" + cls + "'>" + esc(r.op === "@" ? r.text : r.op + r.text) + "</span>";
  }).join("") + "</pre>";
}

function sideHTML(rows) {
  if (!rows.length) return "<p>No differences.</p>";
  if (rows.length === 1 && rows[0].op === "skip") {
    return "<p>No differences (" + rows[0].count + " identical lines).</p>";
  }
  return "<table class='diff-side'>" + rows.map(function (r) {
    if (r.op === "skip") {
      return "<tr class='skip'><td colspan='2'>… " + r.count + " unchanged lines …</td></tr>";
    }
    return "<tr class='" + esc(r.op) + "'><td>" + esc(r.left) + "</td><td>" + esc(r.right) + "</td></tr>";
  }).join("") + "</table>";
}

function loadDiff(name, cell) {
  var a = document.getElementById("cmp-a").value;
  var b = document.getElementById("cmp-b").value;
  var style = document.getElementById("cmp-style").value;
  var rep = "&report=" + encodeURIComponent(name);
  cell.innerHTML = loadingHTML("Loading diff…");
  getJSON("/api/reports/diff?a=" + encodeURIComponent(a) + "&b=" + encodeURIComponent(b) +
          rep + "&style=" + style, function (d) {
    var links = "<div class='diff-links'>" +
      "<a target='_blank' href='" + esc("/api/reports/raw?id=" + encodeURIComponent(a) + rep) +
      "'>raw A</a> · " +
      "<a target='_blank' href='" + esc("/api/reports/raw?id=" + encodeURIComponent(b) + rep) +
      "'>raw B</a>" +
      (d.truncated ? " · <b>diff truncated — see raw</b>" : "") + "</div>";
    cell.innerHTML = links + (style === "unified" ? unifiedHTML(d.rows) : sideHTML(d.rows));
  });
}

var cfgFields = [];

function buildForm() {
  getJSON("/api/config/fields", function (d) {
    cfgFields = d.fields;
    var f = document.getElementById("cfg-form");
    f.innerHTML = "";
    cfgFields.forEach(function (fld) {
      var wrap = document.createElement("div");
      wrap.className = "cfg-row";
      var label = fld.name + (fld.required ? " *" : "");
      var input;
      if (fld.kind === "enum") {
        input = "<select name='" + fld.name + "'" + (fld.fixed ? " disabled" : "") + ">" +
          fld.values.map(function (v) { return "<option>" + v + "</option>"; }).join("") + "</select>";
      } else {
        input = "<input name='" + fld.name + "' value='" + (fld.default || "") + "'>";
      }
      wrap.innerHTML = "<label>" + label + "</label>" + input +
        "<span class='hint'>" + (fld.help || "") + "</span>";
      f.appendChild(wrap);
    });
  });
}

function formValues() {
  var v = {};
  cfgFields.forEach(function (fld) {
    var el = document.querySelector("[name='" + fld.name + "']");
    if (el) v[fld.name] = el.value;
  });
  return v;
}

function renderIssues(issues) {
  document.getElementById("cfg-issues").innerHTML = issues.map(function (i) {
    return "<div class='issue " + i.level + "'>" + i.level + ": " + i.field + " — " + i.message + "</div>";
  }).join("");
}

// Built automatically at startup; the button re-scans (e.g. after copying an RPM in).
function loadDepots() {
  var msg = document.getElementById("cfg-depot-msg");
  msg.textContent = "Scanning depots…";
  getJSON("/api/config/depots", function (d) {
    var sel = document.getElementById("cfg-depot");
    var keep = sel.value;
    sel.innerHTML = "<option value=''>— choose a CORE RPM —</option>" +
      d.depots.map(function (x) {
        return "<option value='" + esc(x.path) + "' data-g='" + esc(x.generic) +
               "' data-l='" + esc(x.load) + "'" + (x.path === keep ? " selected" : "") + ">" +
               esc(x.name) + "</option>";
      }).join("");
    msg.textContent = d.depots.length
      ? d.depots.length + (d.depots.length === 1 ? " RPM" : " RPMs") + " found"
      : "no netFLEX-CORE RPMs in " + (d.dirs || []).join(", ");
  }, function (status) {
    msg.textContent = "could not list depots (HTTP " + status + ")";
  });
}

document.getElementById("cfg-load-depots").onclick = loadDepots;

document.getElementById("cfg-depot").onchange = function () {
  var opt = this.options[this.selectedIndex];
  if (!opt.value) return;
  var set = function (n, val) { var e = document.querySelector("[name='" + n + "']"); if (e) e.value = val; };
  set("DEPOTFILE", opt.value);
  set("NEW_GENERIC", opt.getAttribute("data-g"));
  set("NEW_LOAD", opt.getAttribute("data-l"));
};

document.getElementById("cfg-preview").onclick = function () {
  postJSON("/api/config/preview", { values: formValues() }, function (status, d) {
    document.getElementById("cfg-rendered").textContent = d.rendered || "";
    renderIssues(d.issues || []);
  });
};

document.getElementById("cfg-save").onclick = function () {
  postJSON("/api/config/save", { values: formValues() }, function (status, d) {
    var msg = document.getElementById("cfg-msg");
    if (status === 200) { msg.textContent = "Saved — this config will be used on launch."; renderIssues([]); }
    else { msg.textContent = "Not saved — fix the errors."; renderIssues(d.issues || []); }
  });
};

buildForm();
loadDepots();

document.getElementById("btn-check").onclick = runPreflight;
document.getElementById("btn-launch").onclick = launch;
document.getElementById("preflight").onchange = updateLaunchState;
document.getElementById("preflight").onclick = function (ev) {
  var b = ev.target.closest ? ev.target.closest("button.fix") : null;
  if (b && !b.disabled) runFix(b);
};
document.getElementById("reason").oninput = updateLaunchState;
document.getElementById("mode").onchange = resetPreflight;

document.getElementById("btn-reports-run").onclick = function () {
  postJSON("/api/reports/run", {}, function (status, d) {
    var el = document.getElementById("reports-job");
    if (status === 202) { el.textContent = "Starting…"; refreshReportsJob(); }
    else { el.textContent = "Error: " + (d.error || status); }
  });
};
document.getElementById("btn-compare").onclick = runCompare;
document.getElementById("cmp-style").onchange = function () {
  var rows = document.querySelectorAll("tr.cmp-diff");
  Array.prototype.forEach.call(rows, function (row) {
    if (row.style.display === "none") return;
    var prevRow = row.previousElementSibling;
    if (!prevRow) return;
    loadDiff(prevRow.getAttribute("data-report"), row.firstChild);
  });
};
function onCmpSelectionChanged() {
  cmpTouched = true;
  lastCompared = "";
  document.querySelector("#cmp-table tbody").innerHTML =
    "<tr><td>Selection changed — click Compare.</td></tr>";
}
document.getElementById("cmp-a").onchange = onCmpSelectionChanged;
document.getElementById("cmp-b").onchange = onCmpSelectionChanged;
document.getElementById("site-table").onclick = function (ev) {
  var t = ev.target;
  if (!t.closest) return;
  if (t.closest("#local-toggle") || t.closest("tr.local td.hostcell")) toggleLocal();
};
document.getElementById("cmp-table").onclick = function (ev) {
  var tr = ev.target.closest ? ev.target.closest("tr.cmp-row") : null;
  if (!tr) return;
  var row = document.getElementById("cmp-diff-" + tr.getAttribute("data-i"));
  if (row.style.display !== "none") { row.style.display = "none"; return; }
  row.style.display = "";
  loadDiff(tr.getAttribute("data-report"), row.firstChild);
};

document.getElementById("min-sev").onchange = function () {
  logOffset = 0;
  document.getElementById("log").innerHTML = "";
  refreshLog();
};

initTabs();
document.getElementById("about-grid").innerHTML = loadingHTML("Loading host info…");
document.getElementById("links").textContent = "Loading…";
(function () {
  var tb = document.querySelector("#site-table tbody");
  var tr = tb.insertRow(0);
  tr.innerHTML = "<td colspan='9' class='loading'><span class='spinner'></span> Loading site…</td>";
})();
applyLocalOpen();
refreshProgress();
refreshLog();
refreshInfo();
refreshSite();
refreshPostflight();
refreshReports();
refreshReportsJob();
setInterval(refreshProgress, 2000);
setInterval(refreshLog, 2000);
setInterval(refreshInfo, 15000);
setInterval(refreshSite, 15000);
setInterval(refreshPostflight, 15000);
setInterval(refreshReportsJob, 3000);
setInterval(refreshReports, 30000);
