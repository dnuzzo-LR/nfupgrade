"use strict";

// If anything below throws while the page starts, say so instead of leaving a
// dead page (typically a stale cached index.html next to a newer app.js).
// Built with plain DOM calls so it works whatever index.html is loaded.
window.onerror = function (msg, src, line) {
  var b = document.getElementById("load-error");
  if (!b) {
    b = document.createElement("div");
    b.id = "load-error";
    b.style.cssText = "background:#b3261e;color:#fff;padding:.6rem 1.2rem;font-weight:600";
    document.body.insertBefore(b, document.body.firstChild);
  }
  b.textContent = "The page failed to load (" + msg + (line ? ", app.js:" + line : "") +
    "). Reload with Ctrl+Shift+R; if it persists, the web files on the server are out of date.";
};
var logOffset = 0;
var logFile = "";   // trace file the log pane is showing (server picks the newest)
var hostMode = null;        // "install" or "upgrade", from /api/install/status
var webguiTouched = false;  // the operator changed the web-GUI checkbox by hand
var installStatus = null;   // last /api/install/status response
// This page has shown install mode. A finished install then keeps the install view
// (and the "Install complete" banner) until the operator clicks Continue, instead of
// flipping to upgrade mode as soon as the status poll says netFLEX is installed.
var installSeen = false;
var installWarnLoaded = false;  // the done banner has fetched the trace's WARN lines
var showTab = null;         // set by initTabs: switch to a tab by name

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
    if (name === "bootmenu") bootMenuShown();
  }
  showTab = show;
  tabs.forEach(function (t) { t.onclick = function () { show(t.getAttribute("data-tab")); }; });
  var saved;
  try { saved = localStorage.getItem("nfu_tab"); } catch (e) {}
  var names = [];
  tabs.forEach(function (t) { names.push(t.getAttribute("data-tab")); });
  // A saved tab that no longer exists (e.g. the old "site" tab) falls back to Overview.
  show(saved && names.indexOf(saved) >= 0 ? saved : "overview");
}

var modeTouched = false;   // the user picked Stage/Upgrade by hand; stop auto-switching

// Once staging has finished, the next step is the upgrade: move the dropdown there
// (unless the user chose a mode themselves) and drop the stage pre-flight results.
function suggestUpgradeMode(p) {
  var sel = document.getElementById("mode");
  if (!(p.phase === "stage" && p.status === "done") || modeTouched || sel.value !== "stage") return;
  sel.value = "upgrade";
  resetPreflight();
  document.getElementById("launch-msg").textContent =
    "Staging complete — next step: Upgrade. Run pre-flight checks.";
}

function refreshProgress() {
  getJSON("/api/progress", function (p) {
    suggestUpgradeMode(p);
    document.getElementById("phase").textContent = p.phase;
    document.getElementById("status").textContent = p.status;
    document.getElementById("current").textContent =
      (p.current || "-") + (p.label ? " — " + p.label : "");
    document.getElementById("counter").textContent =
      "Step " + (p.step ? p.step : "–") + " / " + p.total;
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
    document.getElementById("log-file").textContent =
      "Following: " + (d.path || "-") + (d.path && !d.exists ? " (not created yet)" : "");
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
    // Where the upgrade scripts log (from the active config); flag it when it
    // differs from where nfupgrader keeps its own files.
    html += row("Upgrade logs", esc(i.upgrade_logdir) +
                (i.nfupgrader_logdir && i.nfupgrader_logdir !== i.upgrade_logdir
                  ? " <span class='muted'>(nfupgrader files: " + esc(i.nfupgrader_logdir) + ")</span>"
                  : ""));
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
  var html = "<table class='checks'><tr><th></th><th>Category</th><th>Check</th><th>Detail</th>" +
             (overridable ? "<th>Override</th><th>Fix</th>" : "") + "</tr>";
  data.results.forEach(function (r, i) {
    var mark = r.passed ? "✅" : (r.severity === "ERROR" ? "❌" : "⚠️");
    var extra = "";
    if (overridable) {
      // A hard check (e.g. "netFLEX already installed") is a stop, never overridable;
      // a resume skips it, exactly as the server does.
      var ovr = r.hard
        ? (r.passed ? "" : "<span class='hard'>" +
           (hardSkipped() ? "skipped on resume" : "cannot override") + "</span>")
        : (r.blocking ? "<input type='checkbox' class='ovr' data-name='" + esc(r.name) + "'>" : "");
      extra = "<td>" + ovr + "</td>" +
        "<td>" + (r.fix
        ? "<button type='button' class='fix' data-name='" + esc(r.name) + "' data-fix='" +
          esc(r.fix) + "' title='" + esc(r.fix) + "'>Fix</button>" : "") + "</td>";
    }
    var detail = esc(r.detail || "");
    if (r.output) {
      detail += " <a href='#' class='out-toggle' data-i='" + i + "'>output</a>";
    }
    html += "<tr class='" + (r.passed ? "pass" : "fail-" + esc(r.severity)) + "'>" +
            "<td>" + mark + "</td><td class='cat'>" + esc(r.category || "") + "</td>" +
            "<td>" + esc(r.name) + " — " + esc(r.description) + "</td>" +
            "<td>" + detail + "</td>" + extra + "</tr>";
    if (r.output) {
      html += "<tr class='check-out' id='" + el.id + "-out-" + i + "' style='display:none'>" +
              "<td></td><td colspan='7'><pre>" + esc(r.output) + "</pre></td></tr>";
    }
  });
  html += "</table>";
  el.innerHTML = html;
  el.onclick = function (ev) {
    var b = ev.target.closest ? ev.target.closest("button.fix") : null;
    if (b) { if (!b.disabled) runFix(b); return; }
    var a = ev.target.closest ? ev.target.closest("a.out-toggle") : null;
    if (!a) return;
    ev.preventDefault();
    var row = document.getElementById(el.id + "-out-" + a.getAttribute("data-i"));
    if (row) row.style.display = row.style.display === "none" ? "" : "none";
  };
}

var preflight = null;  // last pre-flight /api/checks response for the selected mode
var fixing = false;    // a check fix is running: Launch and Run checks stay disabled

function overriddenNames() {
  var boxes = document.querySelectorAll("#preflight input.ovr:checked");
  return Array.prototype.map.call(boxes, function (b) { return b.getAttribute("data-name"); });
}

// Resuming an unfinished install skips the hard (fresh-install-only) checks.
function hardSkipped() {
  return hostMode === "install" && !!(installStatus && installStatus.unfinished);
}

// Failed hard checks that stop a launch outright (no override possible).
function hardFailures() {
  if (!preflight || hardSkipped()) return [];
  return preflight.results.filter(function (r) { return r.hard && !r.passed; });
}

// Blocking failures the operator can override (every blocking one but the hard ones).
function overridableBlocking(data) {
  return data.blocking.filter(function (r) { return !r.hard; });
}

function updateLaunchState() {
  var btn = document.getElementById("btn-launch");
  var why = document.getElementById("override-msg");
  if (!preflight) { btn.disabled = true; why.textContent = ""; return; }
  var hard = hardFailures();
  var total = overridableBlocking(preflight).length;
  var missing = total - overriddenNames().length;
  var reason = document.getElementById("reason").value.trim();
  var msg = "";
  if (hard.length) {
    msg = "cannot launch: " + hard.map(function (r) { return r.name; }).join(", ") +
          " failed (cannot be overridden)";
  }
  else if (missing > 0) msg = missing + " of " + total + " blocking checks not overridden";
  else if (total > 0 && !reason) msg = "reason required to override";
  why.textContent = msg;
  btn.disabled = fixing || installRunning() || msg !== "";
}

// after (optional) runs once the new results are rendered (not on an error).
function runPreflight(after) {
  var mode = document.getElementById("mode").value;
  getJSON("/api/checks?when=preflight&phase=" + mode, function (data) {
    preflight = data;
    renderChecks(document.getElementById("preflight"), data, true);
    updateLaunchState();
    // The box's message line also carries the hard-stop message; the override
    // help and reason only show when something can be overridden.
    var canOverride = overridableBlocking(data).length > 0;
    document.getElementById("override-box").style.display =
      canOverride || hardFailures().length ? "block" : "none";
    document.getElementById("override-help").style.display = canOverride ? "" : "none";
    document.getElementById("reason").style.display = canOverride ? "" : "none";
    if (typeof after === "function") after();
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

var modalOnOk = null;        // callback for the open confirmation dialog
var modalValidate = null;    // OK is refused (dialog stays open) while this returns false
var modalReturnFocus = null; // element to refocus when it closes

// In-page replacement for the browser's built-in confirm. opts: {title, body (text; newlines
// kept), html (body markup instead of body; the caller escapes it), ok (button label),
// danger (red OK button), focus (id to focus instead of Cancel), validate (fn; false keeps
// the dialog open)}. onOk runs only on confirm.
function showConfirm(opts, onOk) {
  document.getElementById("modal-title").textContent = opts.title;
  if (opts.html) document.getElementById("modal-body").innerHTML = opts.html;
  else document.getElementById("modal-body").textContent = opts.body || "";
  var ok = document.getElementById("modal-ok");
  ok.textContent = opts.ok || "OK";
  ok.className = opts.danger ? "danger" : "primary";
  modalOnOk = onOk;
  modalValidate = opts.validate || null;
  modalReturnFocus = document.activeElement;
  document.getElementById("modal").style.display = "flex";
  // Default focus on Cancel so a stray Enter never launches or fixes. (A typed-reason
  // dialog focuses its input; Enter there does nothing.)
  if (opts.focus) document.getElementById(opts.focus).focus();
  else document.getElementById("modal-cancel").focus();
}

function closeModal(confirmed) {
  if (confirmed && modalValidate && !modalValidate()) return;
  var cb = modalOnOk;
  modalOnOk = null;
  modalValidate = null;
  document.getElementById("modal").style.display = "none";
  if (modalReturnFocus && modalReturnFocus.focus) modalReturnFocus.focus();
  if (confirmed && cb) cb();
}

function modalOpen() {
  return document.getElementById("modal").style.display !== "none";
}

function runFix(btn) {
  var host = lastInfo && lastInfo.host ? lastInfo.host : "this host";
  showConfirm({
    title: "Run fix as root on " + host + "?",
    body: btn.getAttribute("data-fix"),
    ok: "Run fix",
    danger: true
  }, function () { applyFix(btn); });
}

function applyFix(btn) {
  var name = btn.getAttribute("data-name");
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

function thisHost() {
  return lastInfo && lastInfo.host ? lastInfo.host : "this host";
}

// True when a launch in install mode resumes an unfinished install (the server
// decides the same way).
function resuming(mode) {
  return mode === "install" && !!(installStatus && installStatus.unfinished);
}

function installConfirmBase() {
  var s = installStatus || {};
  if (s.unfinished) {
    var rpm = (s.info || {}).DEPOTFILE;
    return "Resume the netFLEX install on " + thisHost() + " from the failed step: " +
      (s.label || s.state || "unknown") + (s.state ? " (" + s.state + ")" : "") + "." +
      (rpm ? "\nRPM: " + rpm : "") +
      "\nSteps that already finished are not repeated. This runs the real install " +
      "and cannot be paused.";
  }
  var dep = document.querySelector("[name='DEPOTFILE']");
  return "Install netFLEX on " + thisHost() + " from " +
    (dep && dep.value ? dep.value : "the CORE RPM chosen on the Config tab") +
    " now? This runs the real install and cannot be paused.";
}

function launchConfirmText(mode) {
  var base = mode === "install" ? installConfirmBase()
           : "Start the " + mode.toUpperCase() +
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
  lines.push((mode === "install" ? "Installing" : "Upgrading") +
             " with unresolved pre-flight failures may leave the system " +
             "in an unsupported state.", "", base);
  return lines.join("\n");
}

function launch() {
  var mode = document.getElementById("mode").value;
  if (!preflight) return;
  var unresolved = launchConfirmText(mode).indexOf("WARNING:") === 0;
  var resume = resuming(mode);
  showConfirm({
    title: unresolved ? "Launch with unresolved pre-flight failures?"
         : (resume ? "Resume the install now?" : "Start the " + mode + " now?"),
    body: launchConfirmText(mode),
    ok: resume ? "Resume install" : "Start " + mode,
    danger: true
  }, function () { startLaunch(mode); });
}

function startLaunch(mode) {
  var reason = document.getElementById("reason").value;
  postJSON("/api/launch", { mode: mode, overrides: overriddenNames(), reason: reason },
    function (status, body) {
      var msg = document.getElementById("launch-msg");
      if (status === 202) {
        if (body.snapshot) { msg.textContent = "Taking before snapshot…"; refreshReportsJob(); }
        else if (body.action === "resume") { msg.textContent = "Resuming install…"; }
        else { msg.textContent = "Launched " + mode + "…"; }
      }
      // A failed hard check is a stop, not something to override.
      else if (status === 409 && body.hard) {
        document.getElementById("btn-launch").disabled = true;
        msg.textContent = "Not launched: " + (body.error || status) + " — " +
          body.hard.map(function (r) { return r.name; }).join(", ") + ". Checks re-run.";
        runPreflight();
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
    // The phase list changes with the host mode; drop an answer for the old one.
    if (document.getElementById("mode").value !== mode) return;
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
    var fixMsg = document.getElementById("fix-msg");
    if (d.interrupted_fix && !fixMsg.textContent) {
      fixMsg.textContent = "⚠️ A fix for " + d.interrupted_fix.name +
        " was interrupted (service restarted) — re-run pre-flight to see its state.";
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

// Install-only fields: the install block below the depot picker renders these
// itself, so the generic form must not also render them.
var INSTALL_OWN_FIELDS = ["BOXTYPE", "MATEHOST", "MASTERHOST", "INSTALLWEBGUI"];

function buildForm() {
  getJSON("/api/config/fields?mode=" + hostMode, function (d) {
    cfgFields = d.fields;
    document.getElementById("cfg-thishost").value = d.hostname || "";
    document.getElementById("cfg-master-me-name").textContent = d.hostname || "this host";
    var f = document.getElementById("cfg-form");
    f.innerHTML = "";
    cfgFields.forEach(function (fld) {
      if (fld.hidden) return;
      if (hostMode === "install" && INSTALL_OWN_FIELDS.indexOf(fld.name) >= 0) return;
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
    if (hostMode === "install") {
      applyBoxType();
      previewInstall();
    }
  });
}

function formValues() {
  var v = {};
  cfgFields.forEach(function (fld) {
    var el = document.querySelector("[name='" + fld.name + "']");
    if (el) v[fld.name] = el.value;
  });
  if (hostMode === "install") {
    v.BOXTYPE = document.getElementById("cfg-boxtype").value;
    v.MATEHOST = document.getElementById("cfg-mate").value.trim();
    v.MASTERHOST = document.getElementById("cfg-master-mate").checked
      ? v.MATEHOST
      : document.getElementById("cfg-thishost").value;
    v.INSTALLWEBGUI = document.getElementById("cfg-webgui").checked ? "YES" : "NO";
  }
  return v;
}

// The mate is always the other box type: a FEP's mate is a BEP, and vice versa.
function applyMateLabel() {
  var boxtype = document.getElementById("cfg-boxtype").value;
  document.getElementById("cfg-mate-label").textContent =
    boxtype === "BEP" ? "Mate (FEP) host" : "Mate (BEP) host";
}

function applyBoxType() {
  var boxtype = document.getElementById("cfg-boxtype").value;
  var multi = boxtype !== "SINGLE";
  document.getElementById("cfg-mate-row").style.display = multi ? "" : "none";
  document.getElementById("cfg-master-row").style.display = multi ? "" : "none";
  applyMateLabel();
  // Default ticked for Singlebox/FEP, unticked for BEP — until the operator
  // overrides it by hand, after which their choice sticks across box types.
  if (!webguiTouched) document.getElementById("cfg-webgui").checked = boxtype !== "BEP";
  previewInstall();
}

function updateMasterMateName() {
  var mate = document.getElementById("cfg-mate").value.trim();
  document.getElementById("cfg-master-mate-name").textContent = mate || "mate";
}

// Live cnc.cnfg preview + mate-not-in-/etc/hosts warning, re-run on every
// install-field input/change.
function previewInstall() {
  if (hostMode !== "install") return;
  postJSON("/api/config/preview", { values: formValues(), mode: hostMode }, function (status, d) {
    document.getElementById("cfg-cnc-preview").textContent = d.cnc_cnfg || "";
    var warn = (d.issues || []).filter(function (i) {
      return i.field === "MATEHOST" && i.level === "WARNING";
    });
    document.getElementById("cfg-mate-warn").textContent = warn.length ? warn[0].message : "";
  });
}

function loadHosts() {
  getJSON("/api/install/hosts", function (d) {
    document.getElementById("cfg-hosts").innerHTML = d.hosts.map(function (h) {
      return "<option value='" + esc(h.name) + "'>" + esc(h.ip) + "</option>";
    }).join("");
  });
}

// In install mode the Upgrade tab is the Install tab: one "Install" phase. Leaving
// it puts Stage/Upgrade back (an upgrade-mode page keeps the operator's pick).
function setModeOptions(install) {
  var sel = document.getElementById("mode");
  if (install) sel.innerHTML = "<option value='install'>Install</option>";
  else if (sel.value === "install" || !sel.options.length) {
    sel.innerHTML = "<option value='stage'>Stage</option><option value='upgrade'>Upgrade</option>";
  }
}

function applyHostMode(s) {
  hostMode = s.mode;
  var install = hostMode === "install";
  if (install) installSeen = true;
  document.getElementById("cfg-title").textContent =
    install ? "Install configuration" : "Upgrade configuration";
  document.getElementById("install-cfg").style.display = install ? "block" : "none";
  document.getElementById("tab-btn-upgrade").textContent = install ? "Install" : "Upgrade";
  setModeOptions(install);
  resetPreflight();
  document.getElementById("postflight").textContent = "-";
  refreshPostflight();
  if (install) loadHosts();
  buildForm();
  renderInstall(s);
}

// A finished install seen by this page: keep showing it until Continue.
function installDone(s) {
  return installSeen && s.state === "isdone" && !!s.installed;
}

// Set an element's text only when it differs: the install poll re-renders every
// 2 s, and rewriting unchanged text would clear the operator's selection (e.g.
// of the trace path they are copying).
function setTextIfChanged(id, text) {
  var el = document.getElementById(id);
  if (el.textContent !== text) el.textContent = text;
}

// Install-mode panels that follow every status poll: overview notice, failure
// panel, done banner, and the Launch button's label.
function renderInstall(s) {
  var install = hostMode === "install";
  var done = install && installDone(s);
  var failed = install && !!s.unfinished && !s.running;
  document.getElementById("not-installed").style.display = install && !done ? "block" : "none";
  document.getElementById("site-table").style.display = install && !done ? "none" : "";
  document.getElementById("install-failed").style.display = failed ? "block" : "none";
  if (failed) {
    setTextIfChanged("install-failed-step",
      (s.label || s.state || "unknown") + (s.state ? " (" + s.state + ")" : ""));
    setTextIfChanged("install-fatal", (s.fatal || []).length
      ? s.fatal.join("\n") : "(no ERROR or FATAL lines in the trace file)");
    setTextIfChanged("install-trace", "Send this file to support: " + (s.trace || "-"));
  }
  document.getElementById("install-done").style.display = done ? "block" : "none";
  if (done && !installWarnLoaded) loadInstallWarnings();
  setTextIfChanged("btn-launch", !install ? "Launch"
    : s.running ? "Install running\u2026"
    : s.unfinished ? "Resume install" : "Launch");
  updateLaunchState();
}

// An install is running right now (a running install is also "unfinished", so
// this is what keeps it from being offered as a Resume).
function installRunning() {
  return hostMode === "install" && !!(installStatus && installStatus.running);
}

// The step in progress, live: run_logged only copies a step's output into the
// trace when the step ends, so a long one (linux_packages) would otherwise look
// stalled in the Log tab.
function refreshLive() {
  getJSON("/api/log/live", function (d) {
    var show = !!(d.running && d.lines && d.lines.length);
    document.getElementById("log-live-box").style.display = show ? "" : "none";
    if (show) setTextIfChanged("log-live", d.lines.join("\n"));
  });
}

// WARN-and-worse lines from the finished install's trace (web GUI, license
// migration…), shown in the done banner. The active phase is still "install", so
// /api/log reads the install trace.
function loadInstallWarnings() {
  installWarnLoaded = true;
  getJSON("/api/log?offset=0&min=WARN", function (d) {
    var pre = document.getElementById("install-warn");
    var lines = (d.entries || []).map(function (e) {
      return (e.ts ? e.ts + " " : "") + e.severity + "  " + e.msg;
    });
    pre.textContent = lines.length ? "Warnings from the install:\n" + lines.join("\n") : "";
    pre.style.display = lines.length ? "block" : "none";
  }, function () { installWarnLoaded = false; });
}

// Preview mode (NFU_PREVIEW_INSTALL): a banner with a scenario picker. The
// server serves canned install data and refuses anything that changes the box.
function renderPreview(s) {
  var banner = document.getElementById("preview-banner");
  banner.style.display = s.preview ? "" : "none";
  var sel = document.getElementById("preview-scenario");
  if (s.preview && document.activeElement !== sel) sel.value = s.preview;
}

function choosePreview() {
  var scenario = document.getElementById("preview-scenario").value;
  postJSON("/api/preview", { scenario: scenario }, function () {
    logOffset = 0;
    refreshInstall();
    refreshProgress();
    refreshLog();
  });
}

function refreshInstall() {
  getJSON("/api/install/status", function (s) {
    installStatus = s;
    renderPreview(s);
    // A just-finished install reads as upgrade mode; hold the install view (done
    // banner) until the operator continues.
    var hold = hostMode === "install" && installSeen && installDone(s);
    if (s.mode !== hostMode && !hold) applyHostMode(s);
    else renderInstall(s);
  });
}

// Resume = the normal pre-flight -> confirm -> launch flow (the server turns an
// install launch into a resume while the install is unfinished).
function resumeInstall() {
  if (showTab) showTab("upgrade");
  var msg = document.getElementById("launch-msg");
  msg.textContent = "Re-running pre-flight checks before resuming…";
  runPreflight(function () {
    if (!document.getElementById("btn-launch").disabled) { msg.textContent = ""; launch(); }
    else msg.textContent = "Resolve or override the pre-flight failures below, then click Resume install.";
  });
}

function confirmReset() {
  var info = (installStatus || {}).info || {};
  var dir = info.LOADDIR || "";
  var lines = [
    "This undoes the unfinished install on " + thisHost() + " so it can start over. " +
    "It removes only what this install created:",
    "",
    "  - the netFLEX-CORE RPM, if it is installed",
    "  - the /usr/cnc and /usr/cnc_stage links, only if they point at " +
      (dir || "the install's load directory"),
    dir && info.LOADDIR_CREATED === "yes"
      ? "  - the load directory " + dir + " and everything in it (this install created it)"
      : "  - NOT the load directory" + (dir ? " " + dir : "") +
        ": it is removed only if this install created it, and it did not, so it is left in place",
    "",
    "The install state file is archived and the trace files are kept. If anything does " +
    "not match (a link pointing elsewhere, no recorded load directory), the reset stops " +
    "without removing anything.",
    ""
  ];
  var html = esc(lines.join("\n")) +
    "<label for='reset-reason'>Reason (required, recorded in the audit log):</label>\n" +
    "<input id='reset-reason' type='text' size='50'> <span id='reset-reason-msg' class='issue ERROR'></span>";
  showConfirm({
    title: "Reset the unfinished install on " + thisHost() + "?",
    html: html,
    ok: "Reset install",
    danger: true,
    focus: "reset-reason",
    validate: function () {
      var ok = document.getElementById("reset-reason").value.trim() !== "";
      document.getElementById("reset-reason-msg").textContent = ok ? "" : "A reason is required.";
      return ok;
    }
  }, function () { applyReset(document.getElementById("reset-reason").value.trim()); });
}

function applyReset(reason) {
  if (!reason) return;   // never post an empty reason (the server 400s anyway)
  var msg = document.getElementById("install-reset-msg");
  msg.textContent = "Starting reset…";
  postJSON("/api/install/reset", { reason: reason }, function (status, body) {
    var text = status === 202 ? "Reset started — follow it on the Log tab."
                              : "Not reset: " + (body.error || status);
    msg.textContent = text;
    document.getElementById("launch-msg").textContent = text;
    if (status === 202) refreshInstall();
  });
}

function continueAfterInstall() {
  installSeen = false;
  installWarnLoaded = false;
  var s = installStatus || {};
  s.mode = "upgrade";
  applyHostMode(s);
  refreshInfo();
  refreshSite();
}

function renderIssues(issues) {
  document.getElementById("cfg-issues").innerHTML = issues.map(function (i) {
    return "<div class='issue " + esc(i.level) + "'>" + esc(i.level) + ": " + esc(i.field) +
      " — " + esc(i.message) + "</div>";
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

// ---- Boot menu tab: netFLEX's boot menu in an xterm.js terminal over /ws/bootmenu.
// The server owns the session (one per tool process, one attached browser); this
// page only attaches to it, starts it on request, and shows what the server says.
var bm = {
  term: null,        // xterm.js Terminal, created on first open
  fit: null,         // its FitAddon
  ws: null,          // the open WebSocket, or null
  wsOpened: false,   // bm.ws has connected (a socket that never opens was refused)
  status: null,      // last /api/bootmenu/status response
  state: "none",     // session state: from the socket while connected, else the poll
  exitCode: null,    // exit code of the ended session
  wantStart: false,  // the user clicked Open: send start once the server says none/ended
  detached: false,   // another tab took the session over: do not reconnect by ourselves
  autoFailed: false, // an automatic reattach was refused: wait for a click
  msg: "",           // error from the server (or a dropped connection) for the status line
  msgState: ""       // bm.state when msg was set: a later state change clears msg
};

function setBootMenuMsg(text) {
  bm.msg = text;
  bm.msgState = bm.state;
}

function bootMenuActive() {
  return document.getElementById("tab-bootmenu").classList.contains("active");
}

function bootMenuConnected() {
  return !!(bm.ws && bm.wsOpened && bm.ws.readyState === 1);
}

function bootMenuSend(frame) {
  if (bootMenuConnected()) bm.ws.send(JSON.stringify(frame));
}

function cssVar(name, dflt) {
  var v = window.getComputedStyle(document.documentElement).getPropertyValue(name);
  v = v ? v.trim() : "";
  return v || dflt;
}

// Create the terminal once. False (and the load-error notice) if xterm.js did not
// load or does not run in this browser.
function ensureBootMenuTerm() {
  if (bm.term) return true;
  var err = document.getElementById("bootmenu-loaderr");
  if (typeof Terminal === "undefined" || typeof FitAddon === "undefined") {
    err.style.display = "block";
    return false;
  }
  var wrap = document.getElementById("bootmenu-wrap");
  wrap.style.display = "block";   // xterm measures its host: it must be visible
  try {
    var term = new Terminal({
      cursorBlink: true,
      fontFamily: cssVar("--mono", "monospace"),
      fontSize: 13,
      scrollback: 5000,
      theme: {
        background: cssVar("--term-bg", "#0f1720"),
        foreground: cssVar("--term-fg", "#d7e0ea"),
        cursor: cssVar("--term-cursor", "#e9c46a"),
        cursorAccent: cssVar("--term-bg", "#0f1720"),
        selectionBackground: cssVar("--term-sel", "rgba(185, 196, 207, .35)")
      }
    });
    var fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(document.getElementById("bootmenu-term"));
  } catch (e) {
    wrap.style.display = "none";
    err.style.display = "block";
    return false;
  }
  err.style.display = "none";
  bm.term = term;
  bm.fit = fit;
  // Keystrokes go to the menu only while the session runs (the server refuses
  // input otherwise); a fit that changes the size tells the PTY.
  term.onData(function (data) {
    if (bm.state === "running") bootMenuSend({ type: "input", data: data });
  });
  term.onResize(function (size) {
    if (bm.state === "running") bootMenuSend({ type: "resize", cols: size.cols, rows: size.rows });
  });
  window.addEventListener("resize", fitBootMenu);
  fitBootMenu();
  return true;
}

function fitBootMenu() {
  if (!bm.fit || !bootMenuActive()) return;
  try { bm.fit.fit(); } catch (e) {}
}

// Open (or reattach to) the session over a new socket. The server answers with the
// scrollback replay and the status, or status "none"; start is sent from there.
function connectBootMenu() {
  var proto = location.protocol === "https:" ? "wss://" : "ws://";
  var ws;
  try {
    ws = new WebSocket(proto + location.host + "/ws/bootmenu");
  } catch (e) {
    setBootMenuMsg("Could not connect: " + e.message);
    renderBootMenu();
    return;
  }
  bm.ws = ws;
  bm.wsOpened = false;
  ws.onopen = function () {
    if (bm.ws !== ws) return;
    bm.wsOpened = true;
    bm.autoFailed = false;
  };
  ws.onmessage = function (ev) {
    if (bm.ws !== ws) return;
    var m;
    try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (m && typeof m === "object") bootMenuFrame(m);
  };
  ws.onclose = function () {
    if (bm.ws !== ws) return;
    bm.ws = null;
    bm.wantStart = false;
    if (!bm.wsOpened) {
      bm.autoFailed = true;
      if (!bm.detached) setBootMenuMsg("Could not connect to the boot menu (reload the page if this persists).");
    } else if (!bm.detached && bm.state === "running") {
      setBootMenuMsg("Disconnected from the boot menu.");
    }
    renderBootMenu();
  };
  renderBootMenu();
}

function bootMenuStart() {
  bm.wantStart = false;
  bm.term.reset();
  fitBootMenu();
  bootMenuSend({ type: "start", cols: bm.term.cols, rows: bm.term.rows });
}

function bootMenuFrame(m) {
  if (m.type === "output") {
    if (m.replay) bm.term.reset();   // the replay is the whole scrollback
    bm.term.write(String(m.data == null ? "" : m.data));
  } else if (m.type === "status") {
    bm.state = m.state;
    if (m.state === "running") {
      setBootMenuMsg("");
      // Size the PTY to this browser (another tab may have had another size).
      fitBootMenu();
      bootMenuSend({ type: "resize", cols: bm.term.cols, rows: bm.term.rows });
      bm.term.focus();
    } else {
      if (m.state === "ended") bm.exitCode = m.exit_code;
      if (bm.wantStart) bootMenuStart();
    }
    renderBootMenu();
    refreshBootMenu();
  } else if (m.type === "exit") {
    bm.state = "ended";
    bm.exitCode = m.code;
    renderBootMenu();
    refreshBootMenu();
  } else if (m.type === "detached") {
    // Opened in another tab: the server closes this socket. Stay off until the
    // user clicks Reconnect, or the two tabs would keep taking it from each other.
    bm.detached = true;
    setBootMenuMsg("");
    renderBootMenu();
  } else if (m.type === "error") {
    bm.wantStart = false;
    setBootMenuMsg("Error: " + m.message);
    renderBootMenu();
  }
}

// Open boot menu / Reconnect.
function openBootMenu() {
  setBootMenuMsg("");
  bm.detached = false;
  bm.autoFailed = false;
  if (!ensureBootMenuTerm()) return;
  var running = bm.state === "running";
  if (bootMenuConnected()) {
    if (!running) bootMenuStart();   // the old session ended; start a new one
    return;
  }
  bm.wantStart = !running;
  if (!bm.ws) connectBootMenu();
}

function bootMenuStatusText() {
  var d = bm.status || {};
  if (bm.detached) return "Opened in another tab";
  if (bm.msg) return bm.msg;
  if (bm.state === "running") {
    return "Boot menu running" + (bootMenuConnected() ? "" : " (not connected)") +
      (d.busy ? " — a boot or shutdown appears to be in progress" : "");
  }
  if (bm.state === "ended") {
    return "Session ended" + (bm.exitCode === null || bm.exitCode === undefined
      ? "" : " (exit " + bm.exitCode + ")");
  }
  if (!bm.status) return "Checking…";
  if (!d.available) {
    return "Not available: " + (d.reason || "unknown") +
      (d.other === "foreign" && (d.other_pids || []).length
        ? " (pid " + d.other_pids.join(", ") + ")" : "");
  }
  return "Not running. Open boot menu starts netFLEX's boot menu on this host.";
}

function renderBootMenu() {
  var d = bm.status || {};
  var running = bm.state === "running";
  var connected = bootMenuConnected();
  setTextIfChanged("bootmenu-status", bootMenuStatusText());
  var open = document.getElementById("btn-bootmenu-open");
  open.style.display = connected && running ? "none" : "";
  setTextIfChanged("btn-bootmenu-open",
    running || bm.detached ? "Reconnect" : "Open boot menu");
  open.disabled = !running && !(bm.status && d.available);
  document.getElementById("btn-bootmenu-end").style.display = running ? "" : "none";
  var leftover = !running && d.other === "leftover";
  document.getElementById("bootmenu-other").style.display = leftover ? "block" : "none";
  if (leftover) {
    setTextIfChanged("bootmenu-other-text", "A boot menu from an earlier run of this tool " +
      "is still running on this host" +
      ((d.other_pids || []).length ? " (pid " + d.other_pids.join(", ") + ")" : "") + ".");
  }
  var wrap = document.getElementById("bootmenu-wrap");
  wrap.style.display = bm.term ? "block" : "none";
  wrap.classList.toggle("inactive", !(connected && running));
}

function refreshBootMenu() {
  getJSON("/api/bootmenu/status", function (d) {
    bm.status = d;
    var tab = document.getElementById("tab-btn-bootmenu");
    // Greyed only when netFLEX is not installed or in preview mode; any other
    // reason is shown in the panel (the tab stays clickable).
    tab.disabled = !!d.greyed;
    tab.classList.toggle("greyed", !!d.greyed);
    tab.title = d.greyed ? d.reason || "" : "";
    if (d.greyed && bootMenuActive() && showTab) showTab("overview");
    var session = d.session || {};
    if (!bm.ws) {
      bm.state = session.state || "none";
      if (bm.state === "ended") bm.exitCode = session.exit_code;
    }
    if (bm.msg && bm.state !== bm.msgState) bm.msg = "";
    if (bm.state === "running" && !bm.ws && !bm.detached && !bm.autoFailed &&
        bootMenuActive() && ensureBootMenuTerm()) {
      bm.wantStart = false;
      connectBootMenu();   // reattach; the replay arrives first
    }
    renderBootMenu();
  });
}

function bootMenuShown() {
  fitBootMenu();
  refreshBootMenu();
}

function endBootMenu() {
  // Fresh status, so the warning reflects what the menu is doing now.
  getJSON("/api/bootmenu/status", function (d) {
    bm.status = d;
    showConfirm({
      title: "End the boot menu session on " + thisHost() + "?",
      body: d.busy
        ? "A boot or shutdown appears to be in progress. Ending it now can leave netFLEX half up or half down." +
          "\n\nEnd the session anyway?"
        : "This ends the boot menu and anything it is running on " + thisHost() + ".",
      ok: "End session",
      danger: true
    }, function () {
      postJSON("/api/bootmenu/end", {}, function (status, body) {
        if (status !== 200) setBootMenuMsg("Not ended: " + (body.error || status));
        refreshBootMenu();
      });
    });
  });
}

function endBootMenuLeftover() {
  var pids = ((bm.status || {}).other_pids || []).join(", ");
  showConfirm({
    title: "End the leftover boot menu on " + thisHost() + "?",
    body: "A boot menu started by an earlier run of this tool is still running" +
      (pids ? " (pid " + pids + ")" : "") + ". Ending it kills those processes and " +
      "anything the menu is running.",
    ok: "End it",
    danger: true
  }, function () {
    postJSON("/api/bootmenu/end-leftover", {}, function (status, body) {
      setBootMenuMsg(status === 200 ? "" : "Not ended: " + (body.error || status));
      refreshBootMenu();
    });
  });
}

// Tabs first, so a failure in a later binding can't leave the user stuck on one tab.
initTabs();

document.getElementById("preview-scenario").onchange = choosePreview;
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
  postJSON("/api/config/preview", { values: formValues(), mode: hostMode }, function (status, d) {
    document.getElementById("cfg-rendered").textContent = d.rendered || "";
    renderIssues(d.issues || []);
  });
};

document.getElementById("cfg-save").onclick = function () {
  postJSON("/api/config/save", { values: formValues(), mode: hostMode }, function (status, d) {
    var msg = document.getElementById("cfg-msg");
    if (status === 200) { msg.textContent = "Saved — this config will be used on launch."; renderIssues([]); }
    else { msg.textContent = "Not saved — fix the errors."; renderIssues(d.issues || []); }
  });
};

document.getElementById("cfg-boxtype").onchange = applyBoxType;
document.getElementById("cfg-mate").oninput = function () { updateMasterMateName(); previewInstall(); };
document.getElementById("cfg-master-me").onchange = previewInstall;
document.getElementById("cfg-master-mate").onchange = previewInstall;
document.getElementById("cfg-webgui").onchange = function () { webguiTouched = true; previewInstall(); };

refreshInstall();
loadDepots();

document.getElementById("btn-check").onclick = function () { runPreflight(); };
document.getElementById("btn-install-resume").onclick = resumeInstall;
document.getElementById("btn-install-reset").onclick = confirmReset;
document.getElementById("btn-install-continue").onclick = continueAfterInstall;
document.getElementById("not-installed-cfg").onclick = function (ev) {
  ev.preventDefault();
  if (showTab) showTab("config");
};
document.getElementById("btn-launch").onclick = launch;
document.getElementById("btn-bootmenu-open").onclick = openBootMenu;
document.getElementById("btn-bootmenu-end").onclick = endBootMenu;
document.getElementById("btn-bootmenu-end-leftover").onclick = endBootMenuLeftover;
document.getElementById("modal-ok").onclick = function () { closeModal(true); };
document.getElementById("modal-cancel").onclick = function () { closeModal(false); };
document.getElementById("modal").onclick = function (ev) {
  if (ev.target === this) closeModal(false);   // click on the dimmed backdrop
};
document.addEventListener("keydown", function (ev) {
  if (!modalOpen()) return;
  if (ev.key === "Escape" || ev.key === "Esc") { ev.preventDefault(); closeModal(false); return; }
  if (ev.key === "Tab") {   // keep focus inside the dialog (inputs, Cancel, OK)
    var f = Array.prototype.slice.call(
      document.querySelectorAll("#modal-body input, #modal-cancel, #modal-ok"));
    var i = f.indexOf(document.activeElement);
    var next = ev.shiftKey ? (i <= 0 ? f.length - 1 : i - 1) : (i + 1) % f.length;
    ev.preventDefault();
    f[next].focus();
  }
});
document.getElementById("preflight").onchange = updateLaunchState;
// #preflight/#postflight click handling (fix buttons + output toggles) is wired
// by renderChecks, since it depends on the rendered rows.
document.getElementById("reason").oninput = updateLaunchState;
document.getElementById("mode").onchange = function () {
  modeTouched = true;
  resetPreflight();
};

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
refreshBootMenu();
setInterval(refreshProgress, 2000);
setInterval(refreshInstall, 2000);
setInterval(refreshLive, 2000);
setInterval(refreshLog, 2000);
setInterval(refreshInfo, 15000);
setInterval(refreshSite, 15000);
setInterval(refreshPostflight, 15000);
setInterval(refreshReportsJob, 3000);
setInterval(refreshReports, 30000);
setInterval(refreshBootMenu, 3000);
