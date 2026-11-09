/* Apply a saved theme before paint, then let the header button switch it. */
(function () {
  var key = "rt-theme";
  var root = document.documentElement;
  try {
    var stored = localStorage.getItem(key);
    if (stored === "light" || stored === "dark") root.setAttribute("data-theme", stored);
  } catch (error) {
    /* Storage can be blocked. The system preference still applies. */
  }

  function bind() {
    var button = document.getElementById("theme-toggle");
    if (!button) return;
    button.addEventListener("click", function () {
      var current = root.getAttribute("data-theme");
      if (current !== "light" && current !== "dark") {
        current = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
      }
      var next = current === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem(key, next); } catch (error) {}
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
