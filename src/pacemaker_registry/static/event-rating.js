(() => {
  const form = document.querySelector("[data-event-rating]");
  if (!form) return;
  const defaults = JSON.parse(document.getElementById("rating-defaults").textContent);
  const keys = Object.keys(defaults);
  const fields = Object.fromEntries(keys.map(key => [key, form.elements.namedItem(key)]));
  const svg = form.querySelector("[data-event-chart]");
  const readout = form.querySelector("[data-readout]");
  const state = form.querySelector("[data-save-state]");
  const boundaries = ["left_bad", "left_good", "right_good", "right_bad"];
  const colors = ["#bc4c43", "#b98216", "#176f59", "#b98216", "#bc4c43"];
  let config;
  let drag = null;
  let selectedSecond = 0;
  let extent = 180;
  let focus = 45;
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const format = n => n === 0 ? "0 с" : (n > 0 ? "+" : "−") + Math.abs(n) + " с";
  const y = score => 26 + (10 - score) * 23.2;
  // The central interval has 76% of the plot; distant tails are compressed.
  function x(t) {
    if (t < -focus) return 52 + (t + extent) / (extent - focus) * 84;
    if (t > focus) return 668 + (t - focus) / (extent - focus) * 84;
    return 136 + (t + focus) / (2 * focus) * 532;
  }
  function seconds(px) {
    if (px < 136) return -extent + (px - 52) / 84 * (extent - focus);
    if (px > 668) return focus + (px - 668) / 84 * (extent - focus);
    return -focus + (px - 136) / 532 * 2 * focus;
  }
  function rating(t) {
    if (t >= config.left_good && t <= config.right_good) return 10;
    const left = t < config.left_good;
    const good = left ? config.left_good : config.right_good;
    const bad = left ? config.left_bad : config.right_bad;
    const width = Math.abs(good - bad);
    if (width > 0 && (left ? t >= bad : t <= bad)) {
      return 10 - 2 * Math.abs(t - good) / width;
    }
    const side = left ? "left" : "right";
    return (width > 0 ? 8 : 10) * Math.exp(-Math.min(700,
      (Math.abs(t - bad) / config[side + "_decay"]) ** config[side + "_curve"]));
  }
  function element(tag, attrs, text) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function valid() {
    return keys.every(key => fields[key].value !== "" && fields[key].validity.valid)
      && config.left_bad <= config.left_good && config.left_good <= 0
      && config.right_good >= 0 && config.right_good <= config.right_bad;
  }
  function readConfig() {
    config = Object.fromEntries(keys.map(key => [key, Number(fields[key].value)]));
    if (!valid()) {
      state.textContent = "Проверьте границы и значения: зоны должны идти слева направо.";
      svg.setAttribute("aria-invalid", "true");
      return false;
    }
    svg.removeAttribute("aria-invalid");
    return true;
  }
  function render(rescale = true) {
    if (!readConfig()) return;
    if (rescale) {
      focus = Math.max(45, Math.abs(config.left_bad), config.right_bad);
      extent = focus + Math.max(90, config.left_decay * 2, config.right_decay * 2);
    }
    const zones = svg.querySelector("[data-zones]");
    const grid = svg.querySelector("[data-grid]");
    const curves = svg.querySelector("[data-curves]");
    const handles = svg.querySelector("[data-handles]");
    [zones, grid, curves, handles].forEach(node => node.replaceChildren());
    const stops = [-extent, ...boundaries.map(key => config[key]), extent];
    for (let i = 0; i < 5; i++) {
      zones.append(element("rect", {x: x(stops[i]), y: 26, width: Math.max(0, x(stops[i + 1]) - x(stops[i])), height: 232, fill: colors[i], opacity: 0.09}));
      const points = [];
      const steps = Math.max(2, Math.ceil(x(stops[i + 1]) - x(stops[i])));
      for (let j = 0; j <= steps; j++) {
        const t = stops[i] + (stops[i + 1] - stops[i]) * j / steps;
        points.push(x(t) + "," + y(rating(t)));
      }
      if (stops[i] !== stops[i + 1]) curves.append(element("polyline", {points: points.join(" "), fill: "none", stroke: colors[i], "stroke-width": 3}));
    }
    [0, 2, 4, 6, 8, 10].forEach(score => {
      grid.append(element("line", {x1: 52, x2: 752, y1: y(score), y2: y(score)}));
      grid.append(element("text", {x: 36, y: y(score) + 4, "text-anchor": "end"}, score));
    });
    const ticks = [-extent, -focus, -45, -30, -15, 0, 15, 30, 45, focus, extent];
    let lastX = -Infinity;
    [...new Set(ticks)].sort((a,b) => a-b).forEach(t => {
      const px = x(t);
      if (px - lastX < 28) return;
      lastX = px;
      grid.append(element("text", {x: px, y: 309, "text-anchor": "middle"}, Math.round(t)));
    });
    boundaries.forEach((key, i) => {
      const px = x(config[key]);
      grid.append(element("line", {x1: px, x2: px, y1: 26, y2: 258, "stroke-dasharray": "4 4"}));
      const handle = element("circle", {cx: px, cy: i % 2 ? 284 : 267, r: 9, fill: colors[i + 1], "data-handle": key, class: "event-handle"});
      const labels = ["Начало левой жёлтой зоны", "Начало допуска", "Конец допуска", "Конец правой жёлтой зоны"];
      handle.append(element("title", {}, labels[i] + ": " + format(config[key])));
      handles.append(handle);
    });
    ["left", "right"].forEach(side => {
      const t = config[side + "_bad"] + (side === "left" ? -1 : 1) * config[side + "_decay"];
      const handle = element("rect", {x: x(t) - 7, y: y(rating(t)) - 7, width: 14, height: 14, rx: 2, fill: colors[0], "data-handle": side + "_decay", "data-second": t, class: "event-handle"});
      handle.append(element("title", {}, "Резкость экспоненты: " + (side === "left" ? "раньше цели" : "позже цели")));
      handles.append(handle);
    });
    form.querySelector("[data-left-range]").textContent = config.left_bad === config.left_good ? "Зона выключена" : format(config.left_bad) + " → " + format(config.left_good);
    form.querySelector("[data-right-range]").textContent = config.right_bad === config.right_good ? "Зона выключена" : format(config.right_good) + " → " + format(config.right_bad);
    form.querySelectorAll("[data-curve-slider]").forEach(slider => { slider.value = config[slider.dataset.curveSlider + "_curve"]; });
  }
  function changed(rescale = true) {
    render(rescale);
    if (valid()) state.textContent = "Есть несохранённые изменения. Нажмите «Сохранить формулу».";
  }
  keys.forEach(key => fields[key].addEventListener("input", () => changed()));
  form.querySelectorAll("[data-curve-slider]").forEach(slider => slider.addEventListener("input", () => {
    fields[slider.dataset.curveSlider + "_curve"].value = slider.value;
    changed();
  }));
  form.querySelector("[data-reset-rating]").addEventListener("click", () => {
    keys.forEach(key => { fields[key].value = defaults[key]; });
    changed();
  });
  function position(event) {
    const point = svg.createSVGPoint();
    point.x = event.clientX; point.y = event.clientY;
    return point.matrixTransform(svg.getScreenCTM().inverse());
  }
  function show(t) {
    if (!valid()) return;
    selectedSecond = Math.round(clamp(t, -extent, extent));
    const score = rating(selectedSecond);
    readout.textContent = format(selectedSecond) + " · " + score.toFixed(2).replace(".", ",") + " балла";
    const hover = svg.querySelector("[data-hover]");
    hover.removeAttribute("hidden");
    const px = x(selectedSecond);
    hover.querySelector("line").setAttribute("x1", px);
    hover.querySelector("line").setAttribute("x2", px);
    hover.querySelector("circle").setAttribute("cx", px);
    hover.querySelector("circle").setAttribute("cy", y(score));
  }
  svg.addEventListener("pointerdown", event => {
    const handle = event.target.closest("[data-handle]");
    if (!handle || !valid()) return;
    event.preventDefault();
    drag = {key: handle.dataset.handle, second: Number(handle.dataset.second)};
    svg.setPointerCapture(event.pointerId);
  });
  svg.addEventListener("pointermove", event => {
    const p = position(event);
    if (drag) {
      const key = drag.key;
      if (boundaries.includes(key)) {
        const limits = {
          left_bad: [-600, config.left_good], left_good: [config.left_bad, 0],
          right_good: [0, config.right_bad], right_bad: [config.right_good, 600],
        };
        fields[key].value = Math.round(clamp(seconds(p.x), ...limits[key]));
      } else {
        const side = key.startsWith("left") ? "left" : "right";
        const edge = config[side + "_bad"];
        const base = edge === config[side + "_good"] ? 10 : 8;
        const ratio = clamp((10 - (p.y - 26) / 23.2) / base, 0.001, 0.999);
        fields[key].value = Math.round(clamp(Math.abs(drag.second - edge) / (-Math.log(ratio)) ** (1 / config[side + "_curve"]), 1, 300));
      }
      changed(false);
    }
    show(seconds(p.x));
  });
  const finishDrag = () => { drag = null; };
  svg.addEventListener("pointerup", finishDrag);
  svg.addEventListener("pointercancel", finishDrag);
  svg.addEventListener("lostpointercapture", finishDrag);
  svg.addEventListener("keydown", event => {
    if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
      event.preventDefault(); show(selectedSecond + (event.key === "ArrowRight" ? 1 : -1));
    }
  });
  svg.addEventListener("pointerleave", () => {
    if (!drag) svg.querySelector("[data-hover]").setAttribute("hidden", "");
  });
  form.addEventListener("submit", event => {
    if (!readConfig()) { event.preventDefault(); return; }
    form.querySelector('[type="submit"]').disabled = true;
    state.textContent = "Сохраняем…";
  });
  window.addEventListener("pageshow", () => {
    form.querySelector('[type="submit"]').disabled = false;
  });
  render();
})();
