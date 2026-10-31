// Live run monitor (increment 5): ticks the clock and swaps in the server-rendered #monitor fragment about once a
// second. It inserts only same-origin HTML that Jinja autoescaped, and builds nothing from run data itself.
(function () {
  "use strict";
  var POLL_MS = 1000, MAX_BACKOFF_MS = 8000;
  var delay = POLL_MS;

  function el() { return document.getElementById("monitor"); }

  function tick() {
    var m = el(), c = m && m.querySelector("[data-clock]");
    var start = m && Number(m.dataset.start);
    if (!c || !start) return;
    var s = Math.max(0, Math.floor((Date.now() - start) / 1000));
    c.textContent = String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
  }

  function swap(html) {
    var cur = el();
    var doc = new DOMParser().parseFromString(html, "text/html");
    var next = doc.getElementById("monitor");
    if (!cur || !next) return null;
    if (next.dataset.state !== cur.dataset.state) return "reload";  // finished, or evaluation started/ended
    var open = Array.prototype.map.call(cur.querySelectorAll("details[open][data-step]"),
                                        function (d) { return d.dataset.step; });
    var wf = cur.querySelector(".waterfall"), scroll = wf ? wf.scrollLeft : 0;
    cur.replaceWith(document.importNode(next, true));
    open.forEach(function (step) {
      var d = el().querySelector('details[data-step="' + CSS.escape(step) + '"]');
      if (d) d.open = true;
    });
    var nwf = el().querySelector(".waterfall");
    if (nwf) nwf.scrollLeft = scroll;
    tick();
    return el().dataset.live === "1" ? "again" : "reload";
  }

  function poll() {
    var m = el();
    if (!m || m.dataset.live !== "1") return;
    if (document.hidden) { setTimeout(poll, POLL_MS); return; }
    var url = "/runs/" + encodeURIComponent(m.dataset.run) + "/live?seen=" + encodeURIComponent(m.dataset.seen);
    fetch(url, { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.text(); })
      .then(function (html) {
        delay = POLL_MS;
        var what = swap(html);
        if (what === "reload") { location.reload(); return; }
        setTimeout(poll, delay);
      })
      .catch(function () {
        var c = el() && el().querySelector("[data-conn]");
        if (c) c.textContent = "connection lost, retrying…";
        delay = Math.min(delay * 2, MAX_BACKOFF_MS);
        setTimeout(poll, delay);
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    tick();
    setInterval(tick, 1000);
    setTimeout(poll, POLL_MS);
  });
})();
