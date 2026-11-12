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
    if (name === "patches") refreshPatches();
    // The license password never outlives a visit to the License tab.
    if (name === "license") refreshLicense();
    else clearLicensePassword();
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
  document.getElementById("launch-msg").textContent = p.web_state === "webfailed"
    ? "Staging complete, but the web GUI install FAILED — see below. Run pre-flight checks."
    : "Staging complete — next step: Upgrade. Run pre-flight checks.";
}

// Where nf_upgrade runs from (#2): the extracted UPGRADEPKG bundle's
// inc_linux_web_install and linux_packages must be there.
function launchDirText(p) {
  if (!p.launch_dir) return "";
  return "Stage runs from " + p.launch_dir + " (" + p.launch_source + "): extract the " +
    "UPGRADEPKGnetFLEX.tar.gz bundle there.";
}

// nf_upgrader only WARNs when the stage's web GUI install fails and still
// finishes the stage; say so until an upgrade has completed.
function stageWebWarn(p) {
  if (p.web_state !== "webfailed" || (p.phase === "upgrade" && p.status === "done")) return "";
  return "Web GUI install FAILED during staging (webfailed). See the .web trace in " +
    (p.logdir || "the log directory") + ". Fix it and re-stage, or override the web-staged " +
    "check to upgrade without the new web GUI.";
}

var lastProgress = null;
var failureShown = null;   // the failed state already announced in launch-msg

// #3: nf_upgrader's fail_upgrade leaves "<step> failed" and exits; say so (the
// launch message would otherwise keep "upgrade starting"), and offer Resume
// for a failed upgrade.
function showFailure(p) {
  lastProgress = p;
  var box = document.getElementById("run-failed");
  var failed = p.status === "failed" && hostMode !== "install";
  // #4: a stage record left after the stage was removed by hand
  var stale = p.status === "stale" && hostMode !== "install" && !!p.stale;
  box.style.display = failed || stale ? "" : "none";
  document.getElementById("btn-resume").style.display = failed && p.resume ? "" : "none";
  if (stale) {
    document.getElementById("run-failed-msg").textContent = p.stale;
    failureShown = null;
    return;
  }
  if (!failed) { failureShown = null; return; }
  var what = (p.phase === "upgrade" ? "Upgrade" : "Stage") + " FAILED at " + p.label +
    " (" + p.current + "). See the Log tab.";
  var next = p.resume
    ? " Fix the cause, then Resume to re-run from that step."
    : (p.phase === "stage" ? " Clean up the stage and stage again." : "");
  document.getElementById("run-failed-msg").textContent = what + next;
  if (failureShown !== p.current) {
    failureShown = p.current;
    document.getElementById("launch-msg").textContent = what;
  }
}

function confirmResume() {
  var r = (lastProgress || {}).resume;
  if (!r) return;
  var lines = [
    "This re-runs the upgrade on " + thisHost() +
    " from the step that failed, as the upgrade manual describes:",
    "",
    "  - the state file is set from '" + r.failed + "' back to '" + r.state + "'",
    "  - nf_upgrade -u runs again and continues from " + r.state,
    "  - no pre-flight checks and no before snapshot (the box is part-way through the upgrade)",
    "",
    "Fix the cause of the failure first (see the Log tab), or the same step fails again.",
    ""
  ];
  var html = esc(lines.join("\n")) +
    "<label><input type='checkbox' id='resume-boot'" + (r.boot_default ? " checked" : "") +
    "> Boot netFLEX when the upgrade finishes</label> <span class='hint'>(" +
    (r.recorded ? "netFLEX was " + (r.boot_default ? "up" : "down") + " when this upgrade was launched"
                : "not recorded for this upgrade") + "; RESTARTINC=NO still skips the boot)</span>\n" +
    "<label for='resume-reason'>Reason (required, recorded in the audit log):</label>\n" +
    "<input id='resume-reason' type='text' size='50'> <span id='resume-reason-msg' class='issue ERROR'></span>";
  showConfirm({
    title: "Resume the failed upgrade on " + thisHost() + "?",
    html: html,
    ok: "Resume upgrade",
    danger: true,
    focus: "resume-reason",
    validate: function () {
      var ok = document.getElementById("resume-reason").value.trim() !== "";
      document.getElementById("resume-reason-msg").textContent = ok ? "" : "A reason is required.";
      return ok;
    }
  }, function () {
    applyResume(document.getElementById("resume-reason").value.trim(),
                document.getElementById("resume-boot").checked);
  });
}

function applyResume(reason, boot) {
  if (!reason) return;
  var msg = document.getElementById("launch-msg");
  msg.textContent = "Resuming upgrade…";
  postJSON("/api/upgrade/resume", { reason: reason, boot: boot }, function (status, body) {
    msg.textContent = status === 202
      ? "Upgrade resumed from " + body.state + " — follow it on the Log tab."
      : "Not resumed: " + (body.error || status);
    if (status === 202) { failureShown = null; refreshProgress(); }
  });
}

function showLaunchDir(p) {
  var t = hostMode === "install" ? "" : launchDirText(p);
  document.getElementById("launch-dir").textContent = t;
  document.getElementById("cfg-launch-dir").textContent = t;
  var w = document.getElementById("stage-web-warn");
  var wt = hostMode === "install" ? "" : stageWebWarn(p);
  w.textContent = wt;
  w.style.display = wt ? "" : "none";
}

function refreshProgress() {
  getJSON("/api/progress", function (p) {
    suggestUpgradeMode(p);
    showLaunchDir(p);
    showFailure(p);
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
        : (i.app_up ? "<span class='pill ok'>UP</span>"
           : "<span class='pill down'>" + (i.app_state === "stale" ? "DOWN (stale .CNC_UP)" : "DOWN") + "</span>");
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
    html += row("Application", i.app_up ? "UP"
      : (i.app_state === "stale" ? "DOWN (stale .CNC_UP: no netFLEX processes running)" : "DOWN"));
    html += row("CORE", v.core);
    html += row("PATCH", v.patch);
    html += row("IPATCH", v.ipatch);
    var w = i.web;
    html += row("Web GUI", w ? esc(w.web) + (w.matches === false
      ? " <span class='pill down'>mismatch</span> " + esc(webMismatchText(w)) : "") : null);
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
      html += row("dbcheck errors", i.app_detail.dbcheck_errors === null ? "unknown (dbcheck did not complete)"
                                    : i.app_detail.dbcheck_errors);
    }
    document.getElementById("about-grid").innerHTML = html;
    document.getElementById("links").textContent =
      i.links ? i.links : (i.app_up ? "(no links reported)" : "application down — unavailable");
    var badge = document.getElementById("app-badge");
    badge.textContent = i.app_up ? "APP UP" : (i.app_state === "stale" ? "APP DOWN (stale .CNC_UP)" : "APP DOWN");
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
    // EPEL packages never get a Fix button (customer approval needed); say so.
    if (r.source === "epel") detail = "<span class='pill epel' title='Extra Packages for " +
      "Enterprise Linux: outside Red Hat-supported content'>EPEL</span> " + detail;
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

// Pre-flight runs only on request (it is slow: dbcheck and dozens of checks);
// until then say so rather than leave a blank under the Phase picker.
function preflightPlaceholder() {
  var sel = document.getElementById("mode");
  var label = sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].text : sel.value;
  document.getElementById("preflight").innerHTML = "<p class='muted'>Not run yet for " +
    esc(label) + " &mdash; press <b>Run pre-flight checks</b>.</p>";
}

function resetPreflight() {
  preflight = null;
  preflightPlaceholder();
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
        else if (body.continued) {
          msg.textContent = "Continuing the interrupted upgrade from " + body.continued +
            " (no new before snapshot; the after snapshot pairs with the original).";
        }
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
    var el = document.getElementById("postflight");
    if (data.pending) el.innerHTML = "<p class='muted'>" + esc(data.pending) + "</p>";
    else if (data.results.length) renderChecks(el, data);
    else el.textContent = "-";
  });
}

function esc(s) {
  return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// ---- Patches tab: install a netFLEX-CORE-PATCH RPM from the depot (nf_patch).
var patchPoll = null;      // timer while a patch install is running
var patchData = null;      // last /api/patches response
var patchMsg = "";         // transient message under the table

var PATCH_STOP_MSG = "This patch requires netFLEX to be stopped — use the Boot menu tab";
var patchWebUnticked = {}; // web tar path -> true once the user unticked it (survives re-render)

function patchStatusText(p, installed) {
  switch (p.status) {
    case "installable": return "► next";
    case "installed": return "installed";
    case "out_of_sequence": return p.reason || "out of sequence";
    case "wrong_release": return p.reason || "wrong release";
    case "wrong_arch": return p.reason || ("wrong arch (box is " + (installed.core_arch || "?") + ")");
    case "unsupported": return p.reason || "not supported";
    case "unreadable": return p.reason || "unreadable";
    default: return p.reason || p.status || "";
  }
}

function refreshPatches() {
  getJSON("/api/patches", function (d) {
    patchData = d;
    var tab = document.getElementById("tab-btn-patches");
    tab.disabled = !d.available;
    tab.classList.toggle("greyed", !d.available);
    tab.title = d.available ? "" : d.reason || "";
    if (!d.available && document.getElementById("tab-patches").classList.contains("active") && showTab) {
      showTab("overview");
      return;
    }
    renderPatches(d);
    if (d.running && !patchPoll) {
      patchPoll = setInterval(refreshPatches, 3000);
    } else if (!d.running && patchPoll) {
      // finished: one last refresh already happened with this response
      clearInterval(patchPoll);
      patchPoll = null;
    }
  });
}

// A patch RPM does not carry the web GUI (a separate LINUXWEB64 tar): say so
// when the web GUI's build is not the installed CORE/PATCH load. "" otherwise.
function webMismatchText(w) {
  if (!w || w.matches !== false) return "";
  return "Web GUI is " + w.web + " but netFLEX is at " + w.expected +
    " \u2014 install the matching LINUXWEB64 tar (lwebsetup).";
}

function renderPatches(d) {
  var inst = d.installed || {};
  var webMsg = webMismatchText(d.web);
  var webEl = document.getElementById("patch-web");
  webEl.style.display = webMsg ? "block" : "none";
  webEl.textContent = webMsg;
  document.getElementById("patch-installed").textContent =
    "Installed: CORE " + (inst.core || "—") + (inst.core_arch ? " (" + inst.core_arch + ")" : "") +
    "   PATCH " + (inst.patch || "—") +
    (inst.current_load != null ? "   (current load " + inst.current_load + ")" : "");
  var ban = document.getElementById("patch-banner");
  var blocked = d.host_block || (d.available ? "" : d.reason || "");
  ban.style.display = blocked ? "block" : "none";
  ban.innerHTML = esc(blocked);
  var rows = d.patches || [];
  var html = "";
  if (!rows.length) {
    html = "<p class='hint'>No patch files found in the depot directories.</p>";
  } else {
    html = "<table class='patch-table'><tr><th>File</th><th>Version</th><th>Status</th></tr>";
    rows.forEach(function (p, i) {
      html += "<tr><td>" + esc(p.name) + "</td><td>" + esc(p.version) + "</td><td class='st-" +
        esc(p.status) + "'>" + esc(patchStatusText(p, inst));
      if (p.status === "installable") {
        var off = d.running || p.needs_stop || !!blocked;
        html += " <button type='button' class='primary' data-patch='" + i + "'" +
          (off ? " disabled" : "") + ">Install</button>";
        if (p.needs_stop) html += "<div class='hint'>" + esc(PATCH_STOP_MSG) + "</div>";
        if (p.web_tar !== undefined) html += patchWebOption(p, off);
      }
      html += "</td></tr>";
    });
    html += "</table>";
  }
  var box = document.getElementById("patch-table");
  box.innerHTML = html;
  var cb = document.getElementById("patch-web-cb");
  if (cb) cb.onchange = function () {
    if (cb.value) patchWebUnticked[cb.value] = !cb.checked;
  };
  var btns = box.querySelectorAll("button[data-patch]");
  for (var i = 0; i < btns.length; i++) {
    btns[i].onclick = (function (p) { return function () { installPatch(p); }; })(rows[btns[i].getAttribute("data-patch")]);
  }
  var msg = document.getElementById("patch-msg");
  if (d.running) {
    msg.innerHTML = "Installing… see <a href='#' id='patch-log-link'>Log tab</a>";
    document.getElementById("patch-log-link").onclick = function (e) { e.preventDefault(); showTab("log"); };
  } else {
    msg.textContent = patchMsg;
  }
  var last = d.last || {};
  var lh = "";
  if (!d.running && last.result) {
    var bad = /RESULT: (refused|failed)/.test(last.result);
    // nf_patch exit 3: the patch is installed, only the web GUI step failed
    var warn = !bad && /web GUI not updated/.test(last.result);
    lh = "Last patch: <span class='" + (bad ? "bad" : warn ? "warn" : "") + "'>" +
      esc(last.result.replace(/^.*RESULT:\s*/, "")) + "</span>";
    if (last.trace) {
      lh += "<div class='hint'>Trace: " + esc(last.trace) +
        (bad || warn ? " — send this file to support" : "") + "</div>";
    }
  }
  document.getElementById("patch-last").innerHTML = lh;
}

// The "Also install the web GUI" checkbox under the installable row: ticked
// by default when the scan found the matching LINUXWEB64 tar beside the RPM,
// greyed out (never installable) with the reason otherwise.
function patchWebOption(p, off) {
  var name = p.web_tar ? p.web_tar.replace(/^.*\//, "") : "";
  var checked = p.web_tar && !patchWebUnticked[p.web_tar];
  var h = "<div class='patch-web-opt" + (p.web_tar ? "" : " greyed") + "'><label>" +
    "<input type='checkbox' id='patch-web-cb' value='" + esc(p.web_tar || "") + "'" +
    (!p.web_tar || off ? " disabled" : "") + (checked ? " checked" : "") +
    "> Also install the web GUI</label>";
  h += "<div class='hint'>" + esc(p.web_tar ? name + " (web GUI " + p.version + ")"
    : p.web_tar_note || "no web GUI tar found") + "</div></div>";
  return h;
}

function installPatch(p) {
  var cb = document.getElementById("patch-web-cb");
  var web = !!(p.web_tar && cb && cb.checked);
  var text = "netFLEX stays up, but its processes will be restarted. " +
    "Installing " + p.name + " cannot be undone from this tool.";
  if (web) {
    text += " Then the web GUI is installed from " + p.web_tar.replace(/^.*\//, "") + ". " +
      "Web users are disconnected while the web GUI is reinstalled (lwebsetup restarts the web processes). " +
      "The web step can take several minutes.";
  }
  showConfirm({
    title: "Install patch " + p.version + (web ? " and the web GUI" : "") + " on " + thisHost() + "?",
    ok: "Install patch",
    body: text
  }, function () {
    patchMsg = "";
    var body = { path: p.path };
    if (web) body.web = p.web_tar;
    postJSON("/api/patches/install", body, function (status, body) {
      if (status === 202) patchInstalled(body);
      else {
        patchMsg = "Not installed: " + (body.error || "HTTP " + status);
        refreshPatches();
      }
    });
  });
}

function patchInstalled(body) {
  patchMsg = "";
  var msg = document.getElementById("patch-msg");
  msg.innerHTML = "Installing… see <a href='#' id='patch-log-link'>Log tab</a>";
  document.getElementById("patch-log-link").onclick = function (e) { e.preventDefault(); showTab("log"); };
  refreshPatches();
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
    else if (!reportsJob && d.waiting) {
      // #7: the upgrade finished; the after snapshot waits for netFLEX to be up
      msg.textContent = d.waiting;
      document.getElementById("reports-job").textContent = d.waiting;
    }
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
  inReplay: false,   // the scrollback replay has begun on bm.ws and its status not yet come
  replayDrops: 0,    // sockets in a row that opened and then dropped during the replay
  showAttach: false, // the tab was just shown: the next poll may attach to an ended session
  loadFailed: false, // the terminal could not be created in this browser
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

// xterm.js did not load (or does not parse) in this browser.
function xtermMissing() {
  return typeof Terminal === "undefined" || typeof FitAddon === "undefined";
}

// Create the terminal once. False (and the load-error notice) if xterm.js did not
// load or does not run in this browser.
function ensureBootMenuTerm() {
  if (bm.term) return true;
  var err = document.getElementById("bootmenu-loaderr");
  if (xtermMissing()) {
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
    bm.loadFailed = true;
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
  bm.inReplay = false;
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
    } else if (bm.inReplay) {
      // Dropped while replaying the scrollback (a slow link can't keep up). Retry
      // by itself twice; after that, wait for a click instead of looping.
      bm.replayDrops++;
      if (bm.replayDrops > 2) {
        bm.autoFailed = true;
        setBootMenuMsg("The connection keeps dropping while the boot menu output is " +
          "replayed (slow link?). Click Reconnect to try again.");
      } else if (!bm.detached && bm.state === "running") {
        setBootMenuMsg("Disconnected from the boot menu.");
      }
    } else if (!bm.detached && bm.state === "running") {
      setBootMenuMsg("Disconnected from the boot menu.");
    }
    bm.inReplay = false;
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
    // The replay is the whole scrollback, in one or more frames: start clean.
    if (m.replay_start) {
      bm.term.reset();
      bm.inReplay = true;
    }
    bm.term.write(String(m.data == null ? "" : m.data));
  } else if (m.type === "status") {
    bm.state = m.state;
    bm.inReplay = false;   // the status follows the replay: it arrived whole
    bm.replayDrops = 0;
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
  bm.replayDrops = 0;
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
  // No terminal in this browser: say so as soon as the tab is shown, not on a click.
  var noTerm = !bm.term && (bm.loadFailed || xtermMissing());
  document.getElementById("bootmenu-loaderr").style.display = noTerm ? "block" : "none";
  open.disabled = noTerm || (!running && !(bm.status && d.available));
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
    // The session another tab took over has ended (or gone): nothing to reconnect
    // to, so Open is plain Open again (it starts a new session).
    if (bm.detached && bm.state !== "running") bm.detached = false;
    // Attach without starting: to a running session on any poll, to an ended one
    // only when the tab has just been shown (so its output stays viewable after a
    // reload without two tabs taking an ended session from each other forever).
    var attach = bm.state === "running" || (bm.state === "ended" && bm.showAttach);
    if (bootMenuActive()) bm.showAttach = false;
    if (attach && !bm.ws && !bm.detached && !bm.autoFailed &&
        bootMenuActive() && ensureBootMenuTerm()) {
      bm.wantStart = false;
      connectBootMenu();   // the replay arrives first, then the status
    }
    renderBootMenu();
  });
}

function bootMenuShown() {
  bm.showAttach = true;
  fitBootMenu();
  refreshBootMenu();
}

// What End session really does (bootmenu.BootMenuSession.end): SIGKILL only the
// menu's own processes, never what the menu started.
var BOOTMENU_END_SCOPE = "Only the menu's own processes (su, new_boot_nms, boot_nms) are " +
  "killed. Anything the menu already started, such as boot or shutdown scripts, " +
  "is not killed and may keep running.";

function endBootMenu() {
  // Fresh status, so the warning reflects what the menu is doing now.
  getJSON("/api/bootmenu/status", function (d) {
    bm.status = d;
    showConfirm({
      title: "End the boot menu session on " + thisHost() + "?",
      body: d.busy
        ? "A boot or shutdown appears to be in progress. Ending it now can leave netFLEX half up or half down." +
          "\n\n" + BOOTMENU_END_SCOPE + "\n\nEnd the session anyway?"
        : "This ends the boot menu on " + thisHost() + ".\n\n" + BOOTMENU_END_SCOPE,
      ok: "End session",
      danger: true
    }, function () {
      postJSON("/api/bootmenu/end", {}, function (status, body) {
        if (status !== 200) setBootMenuMsg("Not ended: " + (body.error || status));
        refreshBootMenu();
      });
    });
  }, function (status) {
    setBootMenuMsg("Could not check the boot menu (HTTP " + status + "); not ended.");
    renderBootMenu();
  });
}

function endBootMenuLeftover() {
  var pids = ((bm.status || {}).other_pids || []).join(", ");
  showConfirm({
    title: "End the leftover boot menu on " + thisHost() + "?",
    body: "A boot menu started by an earlier run of this tool is still running" +
      (pids ? " (pid " + pids + ")" : "") + ". Ending it kills only those boot menu " +
      "processes (new_boot_nms, boot_nms). Anything the menu already started, such as " +
      "boot or shutdown scripts, is not killed and may keep running.",
    ok: "End it",
    danger: true
  }, function () {
    postJSON("/api/bootmenu/end-leftover", {}, function (status, body) {
      setBootMenuMsg(status === 200 ? "" : "Not ended: " + (body.error || status));
      refreshBootMenu();
    });
  });
}

// ---- License tab
// Reads the license on show (GET /api/license). Unlock checks the password and
// loads every NE type; Review asks the server for the before -> after list
// (nothing is written); Save asks for confirmation, then applies it. The
// password lives only in its input: never stored, never in a URL, cleared after
// Save and when the tab is left.
var lic = null;            // last GET /api/license body
var licTypes = null;       // NE types from Unlock; null until unlocked
var licInitial = {};       // field key -> value as first shown (to spot real edits)
var licReview = null;      // last error-free review with changes; Save needs it
var licBusy = false;       // a license POST is in flight
var licSeq = 0;            // drops stale GET answers
// Main fields have their own inputs; every other field is rendered under Advanced.
var LIC_MAIN_IDS = { NumNE: "lic-numne", NumUsers: "lic-numusers", Trial: "lic-trial" };

function licFieldEl(key) {
  return document.getElementById(LIC_MAIN_IDS[key] || "lic-f-" + key);
}

function licFieldValue(f) {
  var el = licFieldEl(f.key);
  if (!el) return null;
  return f.kind === "bool" ? el.checked : el.value;
}

function clearLicensePassword() {
  document.getElementById("lic-password").value = "";
  document.getElementById("lic-repair-password").value = "";
}

function licEditable() {
  return !!(lic && lic.state === "ok" && lic.editable);
}

function applyLicenseEnabled() {
  var on = licEditable() && !licBusy;
  Array.prototype.forEach.call(
    document.querySelectorAll("#lic-form input, #lic-form button"), function (el) {
      if (el.id === "lic-type-filter") return;
      // Before Unlock the type list is a read-only view of the enabled types.
      el.disabled = !on || el.getAttribute("data-ro") === "1";
    });
  document.getElementById("btn-lic-save").disabled = !on || !licReview;
  var canAct = !!(lic && lic.editable) && !licBusy;
  document.getElementById("btn-lic-create").disabled = !canAct;
  document.getElementById("btn-lic-migrate").disabled = !canAct;
  document.getElementById("btn-lic-repair").disabled = !canAct;
}

function setLicenseBusy(on) {
  licBusy = on;
  applyLicenseEnabled();
}

function invalidateLicenseReview() {
  licReview = null;
  document.getElementById("btn-lic-save").disabled = true;
  document.getElementById("lic-review").innerHTML = "";
}

function licenseStatusText(d) {
  var t;
  if (d.state === "unavailable") {
    t = "The license tools are not on this host (/usr/cnc/mbin/lice) — is netFLEX installed?";
  } else if (d.state === "missing") {
    t = "No license on this box.";
  } else if (d.state === "old_format") {
    t = "The license is in an older format; convert it before editing." +
        (d.error ? " (" + d.error + ")" : "");
  } else if (d.state === "needs_repair") {
    t = d.error || "The license needs repair: set its expiry date.";
  } else if (d.state === "unreadable") {
    t = "The license could not be read: " + (d.error || "unknown error");
  } else {
    t = d.editable ? "netFLEX is down — edits allowed."
                   : "Read-only: " + (d.reason || "editing is not allowed right now");
  }
  if ((d.state === "missing" || d.state === "old_format" || d.state === "needs_repair") &&
      !d.editable && d.reason) {
    t += " Not possible right now: " + d.reason + ".";
  }
  return t;
}

function renderLicenseFields(d) {
  var L = d.license || {};
  var adv = "";
  licInitial = {};
  (d.fields || []).forEach(function (f) {
    if (!f.main) {
      var id = "lic-f-" + f.key;
      var input = f.kind === "bool"
        ? "<input type='checkbox' id='" + esc(id) + "'>"
        : "<input type='text' id='" + esc(id) + "' autocomplete='off'" +
          (f.max_len ? " maxlength='" + esc(f.max_len) + "'" : "") + ">";
      adv += "<div class='cfg-row'><label for='" + esc(id) + "'>" + esc(f.label) +
        "</label>" + input + "<span class='hint'>" + esc(f.key) +
        (f.key in L ? "" : " (not in this license)") + "</span></div>";
    }
  });
  document.getElementById("lic-adv-fields").innerHTML = adv;
  (d.fields || []).forEach(function (f) {
    var el = licFieldEl(f.key);
    if (!el) return;
    var v = L[f.key];
    if (f.kind === "bool") el.checked = v === true;
    else el.value = v === null || v === undefined ? "" : String(v);
    licInitial[f.key] = licFieldValue(f);
  });
  document.getElementById("lic-expiry").value = d.expiry || "";

  // Values lice reports that this tab does not edit (watch bits, NE groups, ...).
  var skip = { EnabledNeTypes: 1, MaintExpireDate: 1 };
  (d.fields || []).forEach(function (f) { skip[f.key] = 1; });
  var other = "";
  Object.keys(L).sort().forEach(function (k) {
    if (skip[k]) return;
    other += row(esc(k), esc(licPlain(L[k])));
  });
  document.getElementById("lic-adv-other").innerHTML = other || row("-", "");
}

// A read-only lice -j value as text: a list of records reads "2 MD Reader, 3 ...".
function licPlain(v) {
  if (v === null || typeof v !== "object") return v;
  if (Array.isArray(v)) {
    return v.map(function (e) {
      if (e === null || typeof e !== "object") return String(e);
      return Object.keys(e).map(function (k) { return licPlain(e[k]); }).join(" ");
    }).join(", ");
  }
  return JSON.stringify(v);
}

// Before Unlock: the enabled types from lice -j, read-only. After: every type.
function renderLicenseTypes() {
  var rows, note;
  if (licTypes) {
    rows = licTypes;
    note = "Ticked = enabled. " + licTypes.length + " types.";
  } else {
    rows = ((lic && lic.license && lic.license.EnabledNeTypes) || []).map(function (e) {
      return { type: e.type, name: e.name, enabled: true };
    });
    note = "Enabled types shown. Enter the license password and Unlock to see and change all types.";
  }
  var html = "";
  rows.forEach(function (r) {
    html += "<label class='lic-type' data-text='" + esc((r.type + " " + (r.name || "")).toLowerCase()) +
      "'><input type='checkbox' data-type='" + esc(r.type) + "'" +
      (r.enabled ? " checked" : "") + (licTypes ? "" : " data-ro='1'") + "> " +
      esc(r.type) + " " + esc(r.name || "") + "</label>";
  });
  document.getElementById("lic-type-list").innerHTML =
    html || "<span class='muted'>No NE types enabled.</span>";
  document.getElementById("lic-types-note").textContent = note;
  applyLicenseTypeFilter();
}

function applyLicenseTypeFilter() {
  var q = document.getElementById("lic-type-filter").value.toLowerCase().trim();
  Array.prototype.forEach.call(document.querySelectorAll("#lic-type-list label.lic-type"),
    function (l) {
      l.style.display = !q || l.getAttribute("data-text").indexOf(q) >= 0 ? "" : "none";
    });
}

function renderLicense(d) {
  lic = d;
  licTypes = null;
  document.getElementById("lic-status").textContent = licenseStatusText(d);
  document.getElementById("lic-status").className =
    "lic-status " + (d.state === "ok" && d.editable ? "ok" : "warn");
  document.getElementById("btn-lic-create").style.display = d.state === "missing" ? "" : "none";
  document.getElementById("btn-lic-migrate").style.display = d.state === "old_format" ? "" : "none";
  document.getElementById("lic-repair").style.display = d.state === "needs_repair" ? "" : "none";
  document.getElementById("lic-form").style.display = d.state === "ok" ? "" : "none";
  invalidateLicenseReview();
  if (d.state === "ok") {
    renderLicenseFields(d);
    renderLicenseTypes();
  }
  applyLicenseEnabled();
}

function refreshLicense() {
  var seq = ++licSeq;
  if (!loaded.license) spin("lic-spin", true);
  getJSON("/api/license", function (d) {
    if (seq !== licSeq) return;
    spin("lic-spin", false);
    loaded.license = true;
    renderLicense(d);
  }, function (status) {
    if (seq !== licSeq) return;
    spin("lic-spin", false);
    document.getElementById("lic-status").textContent =
      "Could not read the license (HTTP " + status + ").";
  });
}

// A read that came back not editable is re-read while the tab is shown, so the
// buttons come back on their own once a transient block (a launch grace window,
// a running install, an open boot menu) ends. Inputs are disabled while not
// editable, so re-rendering cannot drop any typing.
function pollLicense() {
  var panel = document.getElementById("tab-license");
  if (!panel || !panel.classList.contains("active")) return;
  if (!lic || lic.editable || licBusy) return;
  refreshLicense();
}

function licNumber(kind, s) {
  var t = String(s).trim();
  if (kind === "int" && /^[0-9]{1,10}$/.test(t)) return parseInt(t, 10);
  if (kind === "float" && /^[0-9]+(\.[0-9]+)?$/.test(t)) return parseFloat(t);
  return t;   // not a number: the server names the field in its error
}

// The review/save body. Only fields the user changed from the value first shown
// are sent: an untouched field the license lacks would be added, and an
// untouched odd value (an empty Text, which lice cannot set; a number the
// server's checks reject) would block the review of an unrelated change.
function licenseRequest() {
  var values = {};
  (lic.fields || []).forEach(function (f) {
    var v = licFieldValue(f);
    if (v === null) return;
    if (v === licInitial[f.key]) return;
    values[f.key] = (f.kind === "int" || f.kind === "float") ? licNumber(f.kind, v) : v;
  });
  var req = {
    password: document.getElementById("lic-password").value,
    values: values,
    expiry: document.getElementById("lic-expiry").value.trim()
  };
  // NE types only once Unlock has loaded the full list; otherwise unchanged.
  var neOn = licTypes ? licCheckedTypes() : null;
  if (neOn) req.ne_on = neOn;
  return req;
}

function licCheckedTypes() {
  var on = [];
  Array.prototype.forEach.call(document.querySelectorAll("#lic-type-list input[type=checkbox]"),
    function (c) { if (c.checked) on.push(parseInt(c.getAttribute("data-type"), 10)); });
  return on;
}

function licTypeName(n) {
  var name = "";
  (licTypes || []).forEach(function (t) { if (t.type === n) name = t.name; });
  return name;
}

function licShow(v) {
  if (v === null || v === undefined || v === "") return "(not set)";
  if (v === true) return "yes";
  if (v === false) return "no";
  return String(v);
}

function licChangeCount(d) {
  return (d.changes || []).length + (d.ne_toggle || []).length + (d.expiry ? 1 : 0);
}

// d: a 200 review body, or null with errors (400 [{field,message}]).
function renderLicenseReview(d, errors) {
  var html = "";
  if (errors && errors.length) {
    html += "<div class='lic-errors'><b>Fix these first:</b><ul>";
    errors.forEach(function (e) {
      html += "<li>" + esc(e.field) + ": " + esc(e.message) + "</li>";
    });
    document.getElementById("lic-review").innerHTML = html + "</ul></div>";
    return;
  }
  (d.warnings || []).forEach(function (w) {
    html += "<div class='lic-warn'>⚠️ " + esc(w) + "</div>";
  });
  if (!licChangeCount(d)) {
    document.getElementById("lic-review").innerHTML =
      html + "<p class='muted'>No changes — the license already has these values.</p>";
    return;
  }
  html += "<table class='checks lic-changes'><thead><tr><th>Field</th><th>Before</th>" +
          "<th>After</th></tr></thead><tbody>";
  (d.changes || []).forEach(function (c) {
    html += "<tr><td>" + esc(c.label) + " <span class='muted'>(" + esc(c.key) + ")</span></td>" +
      "<td>" + esc(licShow(c.before)) + "</td><td>" + esc(licShow(c.after)) + "</td></tr>";
  });
  var enable = d.ne_enable || [];
  (d.ne_toggle || []).forEach(function (n) {
    var on = enable.indexOf(n) >= 0;
    html += "<tr><td>NE type " + esc(n) + " " + esc(licTypeName(n)) + "</td><td>" +
      (on ? "disabled" : "enabled") + "</td><td>" + (on ? "enabled" : "disabled") + "</td></tr>";
  });
  if (d.expiry) {
    html += "<tr><td>Expiry date</td><td>" + esc(licShow(lic && lic.expiry)) + "</td><td>" +
      esc(d.expiry) + "</td></tr>";
  }
  document.getElementById("lic-review").innerHTML = html + "</tbody></table>";
}

function unlockLicense() {
  var msg = document.getElementById("lic-msg");
  msg.textContent = "Unlocking…";
  setLicenseBusy(true);
  postJSON("/api/license/unlock", { password: document.getElementById("lic-password").value },
    function (status, d) {
      setLicenseBusy(false);
      if (status === 200 && d.ok) {
        licTypes = d.ne_types || [];
        invalidateLicenseReview();
        renderLicenseTypes();
        applyLicenseEnabled();
        msg.textContent = "Unlocked — all NE types listed.";
      } else {
        msg.textContent = "Not unlocked: " + (d.error || ("HTTP " + status));
      }
    });
}

function reviewLicense() {
  var msg = document.getElementById("lic-msg");
  invalidateLicenseReview();
  document.getElementById("lic-result").innerHTML = "";
  msg.textContent = "Reviewing…";
  setLicenseBusy(true);
  postJSON("/api/license/review", licenseRequest(), function (status, d) {
    setLicenseBusy(false);
    if (status === 200) {
      renderLicenseReview(d);
      licReview = licChangeCount(d) ? d : null;
      msg.textContent = licReview ? "Review the changes, then Save." : "";
    } else if (status === 400) {
      renderLicenseReview(null, d.errors || [{ field: "", message: d.error || "invalid" }]);
      msg.textContent = "";
    } else {
      msg.textContent = "Not reviewed: " + (d.error || ("HTTP " + status));
    }
    applyLicenseEnabled();
  });
}

function saveLicense() {
  if (!licReview) return;
  var n = licChangeCount(licReview);
  showConfirm({
    title: "Save the license on " + thisHost() + "?",
    body: "Apply " + n + " change" + (n === 1 ? "" : "s") + " to /usr/cnc/data/lic.ucs.\n" +
          "A backup copy (lic.ucs.nfupgrader.<time>) is taken first." +
          ((licReview.warnings || []).length
            ? "\n\nWarnings:\n  - " + licReview.warnings.join("\n  - ") : ""),
    ok: "Save license",
    danger: true
  }, function () {
    var req = licenseRequest();
    document.getElementById("lic-msg").textContent = "Saving…";
    setLicenseBusy(true);
    postJSON("/api/license/save", req, licenseSaved);
  });
}

function licenseSaved(status, d) {
  clearLicensePassword();
  setLicenseBusy(false);
  document.getElementById("lic-msg").textContent = "";
  var html = "";
  if (status === 200) {
    html += d.ok ? "<p class='lic-ok'>✅ Saved.</p>"
      : (d.applied || []).length ? "<p class='lic-bad'>❌ Not everything was saved.</p>"
      : "<p class='lic-bad'>❌ Nothing was saved.</p>";
    if ((d.applied || []).length) html += "<p>Applied: " + esc(d.applied.join(", ")) + "</p>";
    if ((d.not_applied || []).length) {
      html += "<p class='lic-bad'>Not applied: " + esc(d.not_applied.join(", ")) + "</p>";
    }
    if (d.backup) html += "<p>Backup: <code>" + esc(d.backup) + "</code></p>";
    if (d.ok && !(d.applied || []).length) html += "<p class='muted'>Nothing needed changing.</p>";
  } else if (status === 400) {
    html += "<p class='lic-bad'>❌ Not saved:</p><ul>";
    (d.errors || []).forEach(function (e) {
      html += "<li>" + esc(e.field) + ": " + esc(e.message) + "</li>";
    });
    html += "</ul>";
  } else {
    html += "<p class='lic-bad'>❌ Not saved (HTTP " + esc(status) + ").</p>";
  }
  if (d.error) html += "<pre class='lic-error'>" + esc(d.error) + "</pre>";
  document.getElementById("lic-result").innerHTML = html;
  refreshLicense();
}

function licenseAction(path, title, body, okLabel, done) {
  showConfirm({ title: title, body: body, ok: okLabel }, function () {
    var msg = document.getElementById("lic-action-msg");
    msg.textContent = "Working…";
    setLicenseBusy(true);
    postJSON(path, {}, function (status, d) {
      setLicenseBusy(false);
      msg.textContent = status === 200 ? done(d) : "Failed: " + (d.error || ("HTTP " + status));
      refreshLicense();
    });
  });
}

function createLicense() {
  licenseAction("/api/license/create", "Create a license from the template?",
    "Copies the netFLEX default license template to /usr/cnc/data/lic.ucs. " +
    "Then set the values from the purchase email and Save.", "Create license",
    function (d) {
      if (d.migrate && !d.migrate.ok) {
        return "Created, but converting it failed (license-migrate exit " + d.migrate.rc + ").";
      }
      return "Created from the template.";
    });
}

function migrateLicense() {
  licenseAction("/api/license/migrate", "Convert the license to the current format?",
    "Runs license-migrate on /usr/cnc/data/lic.ucs.", "Convert",
    function (d) {
      return d.ok ? "Converted." : "license-migrate failed (exit " + d.rc + ").";
    });
}
// Mirrors license.expiry_error: catches a date that does not exist (04/31)
// before the confirm, instead of after a round trip.
function expiryError(s) {
  var m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(String(s).trim());
  if (!m) return "use MM/DD/YYYY";
  var mo = +m[1], d = +m[2], y = +m[3];
  var dt = new Date(y, mo - 1, d);
  if (mo < 1 || mo > 12 || dt.getMonth() !== mo - 1 || dt.getDate() !== d) {
    return (mo < 10 ? "0" : "") + mo + "/" + (d < 10 ? "0" : "") + d + "/" + m[3] +
           " is not a real date";
  }
  if (y < 1970) return "the year must be 1970 or later";
  return "";
}

function repairLicense() {
  var exp = document.getElementById("lic-repair-expiry").value;
  var msg = document.getElementById("lic-repair-msg");
  var bad = expiryError(exp);
  if (bad) { msg.textContent = "Not repaired: " + bad; return; }
  msg.textContent = "";
  showConfirm({ title: "Repair the license?", ok: "Repair",
                body: "Backs up /usr/cnc/data/lic.ucs, then sets its expiry date to " + exp +
                      " with license -M." }, function () {
    msg.textContent = "Working…";
    setLicenseBusy(true);
    postJSON("/api/license/repair",
             { password: document.getElementById("lic-repair-password").value, expiry: exp },
             function (status, d) {
      setLicenseBusy(false);
      clearLicensePassword();
      if (status === 200 && d.ok) {
        msg.textContent = "Repaired. Backup: " + (d.backup || "-");
      } else if (d.errors && d.errors.length) {
        msg.textContent = "Not repaired: " + d.errors.map(function (e) { return e.message; }).join("; ");
      } else {
        msg.textContent = "Not repaired: " + (d.error || ("HTTP " + status)) +
                          (d.backup ? " (backup: " + d.backup + ")" : "");
      }
      refreshLicense();
    });
  });
}
// ---- end License tab

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
document.getElementById("btn-resume").onclick = confirmResume;
document.getElementById("btn-install-continue").onclick = continueAfterInstall;
document.getElementById("not-installed-cfg").onclick = function (ev) {
  ev.preventDefault();
  if (showTab) showTab("config");
};
document.getElementById("btn-launch").onclick = launch;
document.getElementById("cfg-license").onclick = function () {
  if (showTab) showTab("license");
};
document.getElementById("btn-lic-create").onclick = createLicense;
document.getElementById("btn-lic-migrate").onclick = migrateLicense;
document.getElementById("btn-lic-repair").onclick = repairLicense;
document.getElementById("btn-lic-unlock").onclick = unlockLicense;
document.getElementById("btn-lic-review").onclick = reviewLicense;
document.getElementById("btn-lic-save").onclick = saveLicense;
document.getElementById("lic-type-filter").oninput = applyLicenseTypeFilter;
// Any edit (not the password or the type filter) makes the last review stale.
function onLicenseEdit(ev) {
  var id = ev.target.id;
  if (id === "lic-password" || id === "lic-type-filter") return;
  if (licReview || document.getElementById("lic-review").innerHTML) invalidateLicenseReview();
}
document.getElementById("lic-form").addEventListener("input", onLicenseEdit);
document.getElementById("lic-form").addEventListener("change", onLicenseEdit);
document.getElementById("btn-patch-refresh").onclick = refreshPatches;
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
// Deferred stop (httpd.StopControl): show it on every tab.
function refreshStopping() {
  getJSON("/api/tool/status", function (t) {
    var b = document.getElementById("stopping-banner");
    b.style.display = t.stopping ? "" : "none";
    document.getElementById("stopping-work").textContent =
      t.stopping && t.work && t.work.length ? "(" + t.work.join(", ") + ")" : "";
  }, function () {});
}

preflightPlaceholder();
refreshStopping();
refreshProgress();
refreshLog();
refreshInfo();
refreshSite();
refreshPostflight();
refreshReports();
refreshReportsJob();
refreshBootMenu();
refreshPatches();
setInterval(refreshStopping, 2000);
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
setInterval(pollLicense, 5000);
