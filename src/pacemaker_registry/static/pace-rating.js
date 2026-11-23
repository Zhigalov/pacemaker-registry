document.querySelectorAll('[data-pace-rating], [data-pace-widget]').forEach(form => {
  const editable = form.matches('[data-pace-rating]');
  const defaults = JSON.parse(form.querySelector('[data-pace-defaults]').textContent);
  const fields = editable ? Object.fromEntries(Object.keys(defaults).map(key => [key, form.elements.namedItem(key)])) : {};
  const chart = form.querySelector('[data-pace-chart]');
  const readout = form.querySelector('[data-readout]');
  const observations = window.resultChartMarkers?.read(form) || [];
  const state = form.querySelector('[data-save-state]');
  const slider = form.querySelector('[data-pace-curve]');
  const toggle = form.querySelector('[data-splits-toggle]');
  const settings = form.querySelector('#pace-settings');
  const splitsStatus = form.querySelector('[data-splits-status]');
  const submit = form.querySelector('[type=submit]');
  const clamp = window.ratingChart.clamp;
  const round = (v, digits = 0) => Number(v.toFixed(digits));
  const format = v => v.toLocaleString('ru-RU', {maximumFractionDigits: 1});
  let config = {...defaults}, extent = 60, inspected = 0;
  const x = seconds => 52 + seconds / extent * 700;
  const y = window.ratingChart.y;
  const secondsAt = position => clamp((position - 52) / 700 * extent, 0, extent);
  const scoreAt = position => clamp(10 - (position - 26) / 23.2, 0, 10);

  const score = seconds => window.ratingModels.pace(seconds, config);
  function element(tag, attrs, parent, text) {
    const node = window.ratingChart.element(tag, attrs, text);
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
    const value = score(inspected);
    window.ratingChart.hover(chart, x(inspected), value);
    readout.textContent = `Отклонение ±${format(inspected)} с/км → ${value.toLocaleString('ru-RU', {minimumFractionDigits: 2, maximumFractionDigits: 2})} балла`;
  }

  function render(rescale = true) {
    if (rescale) extent = Math.max(60, config.bad + 2 * config.decay,
      ...observations.map(point => Math.abs(point.deviation) * 1.1));
    const [zones, grid, curves, handles] = window.ratingChart.grid(chart);
    [[0, config.tolerance, '#e9f5ef'], [config.tolerance, config.bad, '#fff7e2'], [config.bad, extent, '#fbefec']].forEach(([from, to, fill]) => {
      element('rect', {x: x(from), y: 26, width: x(to) - x(from), height: 232, fill}, zones);
    });
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
    if (observations.length) window.resultChartMarkers.render(chart, observations, x, y, readout, true);
    if (!editable) return;
    [['tolerance', 10, '#176f59'], ['checkpoint', config.checkpoint_score, '#b98216'], ['bad', config.bad_score, '#bc4c43']].forEach(([key, value, fill]) => {
      const handle = element('circle', {cx: x(config[key]), cy: y(value), r: 8, fill, class: 'event-handle', 'data-pace-handle': key}, handles);
      element('title', {}, handle, `${format(config[key])} с/км: ${format(value)} баллов`);
    });
    const probe = interaction.drag?.key === 'decay' ? interaction.drag.probe : config.bad + config.decay;
    element('rect', {x: x(probe) - 7, y: y(score(probe)) - 7, width: 14, height: 14, rx: 2, fill: '#bc4c43', class: 'event-handle', 'data-pace-handle': 'decay'}, handles);
    if (!chart.querySelector('[data-hover]').hasAttribute('hidden')) inspect(inspected);
  }

  const interaction = window.ratingChart.interact(chart, {
    start(event) {
      const key = event.target.dataset.paceHandle;
      return editable && key && readFields() ? {key, probe: config.bad + config.decay} : null;
    },
    move(point, drag) {
      const t = round(secondsAt(point.x));
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
    },
    end: () => render(),
    keys: ['ArrowLeft', 'ArrowRight', 'Home', 'End'],
    step: key => inspect(key === 'Home' ? 0 : key === 'End' ? extent : inspected + (key === 'ArrowLeft' ? -1 : 1)),
  });
  readFields(); render();
  window.ratingChart.observe(chart, () => { if (!interaction.drag) render(); });
  if (!editable) return;
  function syncToggle() {
    settings.disabled = !toggle.checked;
    settings.inert = !toggle.checked;
    splitsStatus.textContent = toggle.checked
      ? 'При достаточных данных темп даёт 40% итоговой оценки.'
      : 'Итоговая оценка считается только по финишу.';
  }
  toggle.addEventListener('change', () => {
    syncToggle();
    state.textContent = 'Есть несохранённые изменения учёта отсечек.';
  });
  syncToggle();
  Object.values(fields).forEach(input => input.addEventListener('input', () => {
    if (readFields()) { state.textContent = 'Есть несохранённые изменения оценки темпа.'; render(); }
  }));
  slider.addEventListener('input', () => { fields.curve.value = slider.value; fields.curve.dispatchEvent(new Event('input')); });
  form.querySelector('[data-reset-pace]').addEventListener('click', () => { config = {...defaults}; syncFields(); render(); });
  form.addEventListener('submit', event => {
    if (toggle.checked && (!readFields() || !form.reportValidity())) { event.preventDefault(); return; }
    submit.disabled = true; submit.textContent = 'Сохраняем…';
  });
  window.addEventListener('pageshow', () => { syncToggle(); submit.disabled = false; submit.textContent = 'Сохранить темп'; });
});
