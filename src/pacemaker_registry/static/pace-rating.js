document.querySelectorAll('[data-pace-rating], [data-pace-widget]').forEach(form => {
  const editable = form.matches('[data-pace-rating]');
  const defaults = JSON.parse(form.querySelector('[data-pace-defaults]').textContent);
  const fields = editable ? Object.fromEntries(Object.keys(defaults).map(key => [key, form.elements.namedItem(key)])) : {};
  const chart = form.querySelector('[data-pace-chart]');
  const readout = form.querySelector('[data-readout]');
  const state = form.querySelector('[data-save-state]');
  const slider = form.querySelector('[data-pace-curve]');
  const submit = form.querySelector('[type=submit]');
  const ns = 'http://www.w3.org/2000/svg';
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const round = (v, digits = 0) => Number(v.toFixed(digits));
  const format = v => v.toLocaleString('ru-RU', {maximumFractionDigits: 1});
  let config = {...defaults}, extent = 60, drag = null, inspected = 0;
  const x = seconds => 52 + seconds / extent * 700;
  const y = score => 26 + (10 - score) * 23.2;
  const secondsAt = position => clamp((position - 52) / 700 * extent, 0, extent);
  const scoreAt = position => clamp(10 - (position - 26) / 23.2, 0, 10);

  function score(seconds) {
    const d = Math.abs(seconds), c = config;
    if (d <= c.tolerance) return 10;
    if (d <= c.checkpoint) return 10 - (10 - c.checkpoint_score) * (d - c.tolerance) / (c.checkpoint - c.tolerance);
    if (d <= c.bad) return c.checkpoint_score - (c.checkpoint_score - c.bad_score) * (d - c.checkpoint) / (c.bad - c.checkpoint);
    return c.bad_score * Math.exp(-Math.min(700, ((d - c.bad) / c.decay) ** c.curve));
  }

  function element(tag, attrs, parent, text) {
    const node = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    if (text !== undefined) node.textContent = text;
    parent.append(node);
    return node;
  }

  function readFields() {
    if (!editable) return true;
    const c = Object.fromEntries(Object.entries(fields).map(([key, input]) => [key, input.valueAsNumber]));
    const valid = Object.values(c).every(Number.isFinite)
      && 0 <= c.tolerance && c.tolerance < c.checkpoint && c.checkpoint < c.bad && c.bad <= 600
      && .1 <= c.bad_score && c.bad_score <= c.checkpoint_score && c.checkpoint_score <= 10
      && 1 <= c.decay && c.decay <= 300 && .5 <= c.curve && c.curve <= 3;
    fields.bad.setCustomValidity(valid ? '' : 'Границы должны возрастать, а баллы — убывать. Проверьте настройки темпа.');
    if (!valid) {
      state.textContent = 'Проверьте настройки: допуск < опорная точка < экспонента; 10 ≥ баллы точки ≥ баллы экспоненты ≥ 0,1. График показывает последнюю корректную шкалу.';
      return false;
    }
    config = c;
    slider.value = c.curve;
    return true;
  }

  function syncFields() {
    Object.entries(config).forEach(([key, value]) => { fields[key].value = value; });
    fields.bad.setCustomValidity('');
    slider.value = config.curve;
    state.textContent = 'Есть несохранённые изменения оценки темпа.';
  }

  function inspect(seconds) {
    inspected = clamp(seconds, 0, extent);
    const value = score(inspected), hover = chart.querySelector('[data-hover]');
    hover.removeAttribute('hidden');
    hover.querySelector('line').setAttribute('x1', x(inspected));
    hover.querySelector('line').setAttribute('x2', x(inspected));
    hover.querySelector('circle').setAttribute('cx', x(inspected));
    hover.querySelector('circle').setAttribute('cy', y(value));
    readout.textContent = `Отклонение ±${format(inspected)} с/км → ${value.toLocaleString('ru-RU', {minimumFractionDigits: 2, maximumFractionDigits: 2})} балла`;
  }

  function render(rescale = true) {
    if (rescale) extent = Math.max(60, config.bad + 2 * config.decay);
    const zones = chart.querySelector('[data-zones]'), grid = chart.querySelector('[data-grid]');
    const curves = chart.querySelector('[data-curves]'), handles = chart.querySelector('[data-handles]');
    [zones, grid, curves, handles].forEach(group => group.replaceChildren());
    [[0, config.tolerance, '#e9f5ef'], [config.tolerance, config.bad, '#fff7e2'], [config.bad, extent, '#fbefec']].forEach(([from, to, fill]) => {
      element('rect', {x: x(from), y: 26, width: x(to) - x(from), height: 232, fill}, zones);
    });
    for (let value = 0; value <= 10; value += 2) {
      element('line', {x1: 52, x2: 752, y1: y(value), y2: y(value)}, grid);
      element('text', {x: 36, y: y(value) + 4, 'text-anchor': 'end'}, grid, value);
    }
    const step = Math.max(5, Math.ceil(extent / (chart.clientWidth < 600 ? 4 : 8) / 5) * 5);
    for (let t = 0; t <= extent; t += step) {
      element('text', {x: x(t), y: 305, 'text-anchor': 'middle'}, grid, t ? `±${format(t)}` : '0');
    }
    const line = (points, color) => element('path', {
      d: points.map(([t, value], i) => `${i ? 'L' : 'M'}${x(t)},${y(value)}`).join(' '),
      fill: 'none', stroke: color, 'stroke-width': 3,
    }, curves);
    line([[0, 10], [config.tolerance, 10]], '#176f59');
    line([[config.tolerance, 10], [config.checkpoint, config.checkpoint_score], [config.bad, config.bad_score]], '#b98216');
    const points = [[config.bad, config.bad_score]];
    for (let px = x(config.bad) + 2; px < 752; px += 2) {
      const t = secondsAt(px); points.push([t, score(t)]);
    }
    points.push([extent, score(extent)]);
    line(points, '#bc4c43');
    if (!chart.querySelector('[data-hover]').hasAttribute('hidden')) inspect(inspected);
    if (!editable) return;
    [['tolerance', 10, '#176f59'], ['checkpoint', config.checkpoint_score, '#b98216'], ['bad', config.bad_score, '#bc4c43']].forEach(([key, value, fill]) => {
      const handle = element('circle', {cx: x(config[key]), cy: y(value), r: 8, fill, class: 'event-handle', 'data-pace-handle': key}, handles);
      element('title', {}, handle, `${format(config[key])} с/км: ${format(value)} баллов`);
    });
    const probe = drag?.key === 'decay' ? drag.probe : config.bad + config.decay;
    element('rect', {x: x(probe) - 7, y: y(score(probe)) - 7, width: 14, height: 14, rx: 2, fill: '#bc4c43', class: 'event-handle', 'data-pace-handle': 'decay'}, handles);
    if (!chart.querySelector('[data-hover]').hasAttribute('hidden')) inspect(inspected);
  }

  function position(event) {
    return new DOMPoint(event.clientX, event.clientY).matrixTransform(chart.getScreenCTM().inverse());
  }
  chart.addEventListener('pointerdown', event => {
    if (!editable) return;
    const key = event.target.dataset.paceHandle;
    if (!key || !readFields()) return;
    drag = {key, probe: config.bad + config.decay};
    chart.setPointerCapture(event.pointerId);
    event.preventDefault();
  });
  chart.addEventListener('pointermove', event => {
    const point = position(event), t = round(secondsAt(point.x));
    if (drag) {
      const value = round(scoreAt(point.y), 1);
      switch (drag.key) {
        case 'tolerance': config.tolerance = clamp(t, 0, config.checkpoint - 1); break;
        case 'checkpoint':
          config.checkpoint = clamp(t, config.tolerance + 1, config.bad - 1);
          config.checkpoint_score = clamp(value, config.bad_score, 10); break;
        case 'bad':
          config.bad = clamp(t, config.checkpoint + 1, 600);
          config.bad_score = clamp(value, .1, config.checkpoint_score); break;
        case 'decay':
          config.decay = clamp(round((drag.probe - config.bad) / (-Math.log(clamp(scoreAt(point.y) / config.bad_score, .000001, .999999))) ** (1 / config.curve)), 1, 300); break;
      }
      syncFields(); render(false);
    }
    inspect(t);
  });
  function release() { if (drag) { drag = null; render(); } }
  chart.addEventListener('pointerup', release);
  chart.addEventListener('pointercancel', release);
  chart.addEventListener('lostpointercapture', release);
  chart.addEventListener('pointerleave', () => { if (!drag) chart.querySelector('[data-hover]').setAttribute('hidden', ''); });
  chart.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    inspect(event.key === 'Home' ? 0 : event.key === 'End' ? extent : inspected + (event.key === 'ArrowLeft' ? -1 : 1));
  });
  readFields(); render();
  if (window.ResizeObserver) new ResizeObserver(() => { if (!drag) render(); }).observe(chart);
  if (!editable) return;
  Object.values(fields).forEach(input => input.addEventListener('input', () => {
    if (readFields()) { state.textContent = 'Есть несохранённые изменения оценки темпа.'; render(); }
  }));
  slider.addEventListener('input', () => { fields.curve.value = slider.value; fields.curve.dispatchEvent(new Event('input')); });
  form.querySelector('[data-reset-pace]').addEventListener('click', () => { config = {...defaults}; syncFields(); render(); });
  form.addEventListener('submit', event => {
    if (!readFields() || !form.reportValidity()) { event.preventDefault(); return; }
    submit.disabled = true; submit.textContent = 'Сохраняем…';
  });
  window.addEventListener('pageshow', () => { submit.disabled = false; submit.textContent = 'Сохранить темп'; });
});
