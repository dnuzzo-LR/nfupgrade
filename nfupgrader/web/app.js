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

function launch(mode) {
  if (!window.confirm("Start the " + mode.toUpperCase() +
      " now? This runs the real upgrade and cannot be paused.")) {
    return;
  }
  postJSON("/api/launch", { mode: mode }, function (status, body) {
    document.getElementById("launch-msg").textContent =
      status === 202 ? ("Launched " + mode + "…") : ("Error: " + (body.error || status));
  });
}

document.getElementById("btn-stage").onclick = function () { launch("stage"); };
document.getElementById("btn-upgrade").onclick = function () { launch("upgrade"); };
document.getElementById("min-sev").onchange = function () {
  logOffset = 0;
  document.getElementById("log").innerHTML = "";
  refreshLog();
};

refreshProgress();
refreshLog();
setInterval(refreshProgress, 2000);
setInterval(refreshLog, 2000);
