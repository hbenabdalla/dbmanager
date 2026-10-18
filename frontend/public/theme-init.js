// Applied before first paint to avoid a light/dark flash. Kept external for a strict CSP.
try {
  var t = localStorage.getItem("dbm.theme");
  var dark = t ? t === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  if (dark) document.documentElement.classList.add("dark");
} catch (e) {
  /* storage unavailable */
}
