"use strict";
var logOffset = 0;

function getJSON(url, cb) {
  var x = new XMLHttpRequest();
  x.open("GET", url, true);
  x.onreadystatechange = function () {
    if (x.readyState === 4 && x.status === 200) cb(JSON.parse(x.responseText));
  };
  x.send();
}

function postJSON(url, obj, cb) {
  var x = new XMLHttpRequest();
  x.open("POST", url, true);
  x.setRequestHeader("Content-Type", "application/json");
  x.onreadystatechange = function () {
    if (x.readyState === 4) cb(x.status, x.responseText ? JSON.parse(x.responseText) : {});
  };
  x.send(JSON.stringify(obj));
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
  getJSON("/api/log?offset=" + logOffset + (min ? "&min=" + min : ""), function (d) {
    logOffset = d.next_offset;
    var pre = document.getElementById("log");
    d.entries.forEach(function (e) {
      var line = document.createElement("div");
      line.className = "sev-" + e.severity;
      line.textContent = (e.ts ? e.ts + " " : "") + e.severity + "  " + e.msg;
      pre.appendChild(line);
    });
    pre.scrollTop = pre.scrollHeight;
  });
}

function cell(v) { return "<td>" + (v === null || v === undefined || v === "" ? "-" : v) + "</td>"; }

function refreshSite() {
  getJSON("/api/site", function (s) {
    var tb = document.querySelector("#site-table tbody");
    tb.innerHTML = "";
    s.hosts.forEach(function (h) {
      var i = h.info || {};
      var v = i.versions || {}, l = i.loads || {}, g = i.gr || {};
      var tr = document.createElement("tr");
      if (h.is_local) tr.className = "local";
      var reach = h.reachable ? "ok" : "unreachable";
      var app = !h.reachable ? "?" : (i.app_up ? "UP" : "DOWN");
      tr.innerHTML =
        cell(h.name + (h.is_local ? " (this)" : "")) + cell(h.role) + cell(reach) +
        cell(app) + cell(v.core) + cell(l.installed) + cell(l.staged) +
        cell(g.transfer);
      tb.appendChild(tr);
    });
  });
}

function row(label, value) {
  return "<div class='k'>" + label + "</div><div class='v'>" +
         (value === null || value === undefined || value === "" ? "-" : value) + "</div>";
}

function refreshInfo() {
  getJSON("/api/info", function (i) {
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
      html += row("incinfo", i.app_detail.incinfo);
      html += row("rdb", i.app_detail.rdb_version);
      html += row("dbcheck errors", i.app_detail.dbcheck_errors);
    }
    document.getElementById("about-grid").innerHTML = html;
    document.getElementById("links").textContent =
      i.links ? i.links : (i.app_up ? "(no links reported)" : "application down — unavailable");
  });
}

function renderChecks(el, data) {
  var html = "<table class='checks'><tr><th></th><th>Check</th><th>Detail</th></tr>";
  data.results.forEach(function (r) {
    var mark = r.passed ? "✅" : (r.severity === "ERROR" ? "❌" : "⚠️");
    html += "<tr class='" + (r.passed ? "pass" : "fail-" + r.severity) + "'>" +
            "<td>" + mark + "</td><td>" + r.name + " — " + r.description + "</td>" +
            "<td>" + (r.detail || "") + "</td></tr>";
  });
  html += "</table>";
  el.innerHTML = html;
}

function runPreflight() {
  var mode = document.getElementById("mode").value;
  getJSON("/api/checks?when=preflight&phase=" + mode, function (data) {
    renderChecks(document.getElementById("preflight"), data);
    var blocked = data.blocking.length > 0;
    document.getElementById("override-box").style.display = blocked ? "block" : "none";
    document.getElementById("btn-launch").disabled = false;  // enabled; server re-checks
  });
}

function launch() {
  var mode = document.getElementById("mode").value;
  var override = document.getElementById("override").checked;
  var reason = document.getElementById("reason").value;
  if (!window.confirm("Start the " + mode.toUpperCase() +
      " now? This runs the real upgrade and cannot be paused.")) return;
  postJSON("/api/launch", { mode: mode, override: override, reason: reason },
    function (status, body) {
      var msg = document.getElementById("launch-msg");
      if (status === 202) { msg.textContent = "Launched " + mode + "…"; }
      else if (status === 409) { msg.textContent = "Blocked by pre-flight checks — override to proceed."; }
      else { msg.textContent = "Error: " + (body.error || status); }
    });
}

function refreshPostflight() {
  var mode = document.getElementById("mode").value;
  getJSON("/api/checks?when=postflight&phase=" + mode, function (data) {
    if (data.results.length) renderChecks(document.getElementById("postflight"), data);
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

document.getElementById("cfg-load-depots").onclick = function () {
  getJSON("/api/config/depots", function (d) {
    var sel = document.getElementById("cfg-depot");
    sel.innerHTML = "<option value=''>— choose a CORE RPM —</option>" +
      d.depots.map(function (x) {
        return "<option value='" + x.path + "' data-g='" + x.generic + "' data-l='" + x.load + "'>" +
               x.name + "</option>";
      }).join("");
  });
};

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

document.getElementById("btn-check").onclick = runPreflight;
document.getElementById("btn-launch").onclick = launch;
document.getElementById("min-sev").onchange = function () {
  logOffset = 0;
  document.getElementById("log").innerHTML = "";
  refreshLog();
};

refreshProgress();
refreshLog();
refreshInfo();
refreshSite();
refreshPostflight();
setInterval(refreshProgress, 2000);
setInterval(refreshLog, 2000);
setInterval(refreshInfo, 15000);
setInterval(refreshSite, 15000);
setInterval(refreshPostflight, 15000);
