(() => {
  "use strict";
  const id = Number(document.currentScript?.dataset.metricaId);
  // Local previews and the technical container URL must not pollute production statistics.
  if (!Number.isSafeInteger(id) || id <= 0 || location.hostname !== "pacersreg.ru") return;

  function cleanUrl(value) {
    if (!value) return "";
    try {
      const url = new URL(value, location.origin);
      if (!["http:", "https:"].includes(url.protocol)) return "";
      return url.origin + (url.origin === location.origin ? url.pathname : "/");
    } catch { return ""; }
  }

  window.ym = window.ym || function () { (window.ym.a = window.ym.a || []).push(arguments); };
  window.ym.l = Date.now();
  const script = document.createElement("script");
  script.async = true;
  script.src = `https://mc.yandex.ru/metrika/tag.js?id=${id}`;
  script.referrerPolicy = "origin";
  document.head.append(script);

  // Do not send participant URLs from /add?result_url=… or names from page titles.
  window.ym(id, "init", {
    url: cleanUrl(location.href), referrer: cleanUrl(document.referrer),
    sendTitle: false, webvisor: false, clickmap: false, trackLinks: false,
    ecommerce: false, disableYtm: true, accurateTrackBounce: true,
  });
  const goals = new Set(["result_open", "registry_filter", "result_parse", "result_preview", "result_saved"]);
  function goal(name) {
    if (!goals.has(name)) return;
    try { window.ym(id, "reachGoal", name); } catch { /* Analytics must never block the application. */ }
  }
  document.querySelectorAll("[data-analytics-goal]").forEach((element) => goal(element.dataset.analyticsGoal));
  // Capture runs before the filter's change handler submits its form.
  document.addEventListener("change", (event) => {
    if (event.target.matches("[data-registry-filter]")) goal("registry_filter");
  }, true);
  document.addEventListener("submit", (event) => {
    if (event.target.matches(".js-fetch-result")) goal("result_parse");
  }, true);
})();
