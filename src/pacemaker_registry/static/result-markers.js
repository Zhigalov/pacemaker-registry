// Read-only observations share the exact scales of the existing rating charts.
window.resultChartMarkers = {
  read(widget) {
    const data = widget.querySelector('[data-result-markers]');
    return data ? JSON.parse(data.textContent).filter(point =>
      Number.isFinite(point.deviation) && Number.isFinite(point.score)) : [];
  },
  render(chart, points, x, y, readout, pace = false) {
    let layer = chart.querySelector('[data-result-observations]');
    if (!layer) {
      layer = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      layer.setAttribute('data-result-observations', '');
      chart.append(layer);
    }
    layer.replaceChildren();
    if (!points.length) return;
    const element = (tag, attrs, parent, text) => {
      const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
      Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
      if (text !== undefined) node.textContent = text;
      parent.append(node);
      return node;
    };
    const format = n => n.toLocaleString('ru-RU', {maximumFractionDigits: 2});
    const signed = n => (n > 0 ? '+' : n < 0 ? '−' : '') + format(Math.abs(n));
    const chartWidth = Math.max(1, chart.getBoundingClientRect().width);
    const compact = chartWidth < 600;
    // SVG units shrink with the chart. Keep labels readable and tappable in CSS pixels.
    const fontSize = Math.max(14, 14 * 800 / chartWidth);
    const labelHeight = Math.max(32, 44 * 800 / chartWidth);
    const sorted = points.map((point, index) => ({...point, index,
      px: x(pace ? Math.abs(point.deviation) : point.deviation), py: y(point.score)}))
      .sort((a, b) => a.px - b.px || a.index - b.index);
    const labelText = point => pace ? point.label : `${point.label} ${signed(point.deviation)} с`;
    const labelWidth = Math.min(700, Math.max(...sorted.map(point => labelText(point).length * fontSize * .63 + 24)));
    const columns = Math.max(1, Math.floor(700 / (labelWidth + 10)));
    const rows = Math.ceil(points.length / columns);
    chart.setAttribute('viewBox', `0 0 800 ${340 + rows * (labelHeight + 12)}`);
    chart.setAttribute('role', 'group');
    const leaders = element('g', {}, layer);
    // Keep points on the curve. Only their labels move into non-overlapping lanes.
    for (let offset = 0; offset < sorted.length; offset += columns) {
      const row = sorted.slice(offset, offset + columns);
      let previous = 52 - labelWidth / 2 - 10;
      row.forEach((point, i) => {
        const rightLimit = 752 - labelWidth / 2 - (row.length - i - 1) * (labelWidth + 10);
        point.lx = Math.min(rightLimit, Math.max(point.px, previous + labelWidth + 10));
        previous = point.lx;
      });
      row.forEach(point => {
        const ly = 334 + (offset / columns) * (labelHeight + 12);
        const description = `${point.label} · ${signed(point.deviation)} ${pace ? 'с/км' : 'с'} · ${format(point.score)} балла`;
        const group = element('g', {'data-result-marker': point.index, class: 'result-chart-marker',
          tabindex: 0, role: 'button', 'aria-label': description}, layer);
        element('title', {}, group, description);
        const leader = element('path', {d: `M${point.px},${point.py} L${point.px},318 L${point.lx},${ly}`,
          class: 'result-marker-leader'}, leaders);
        element('circle', {cx: point.px, cy: point.py, r: compact ? 7 : 5,
          class: 'result-marker-dot'}, group);
        element('rect', {x: point.lx - labelWidth / 2, y: ly, width: labelWidth,
          height: labelHeight, rx: 7, class: 'result-marker-label'}, group);
        const label = element('text', {x: point.lx, y: ly + labelHeight / 2,
          'text-anchor': 'middle', 'dominant-baseline': 'central'}, group, labelText(point));
        label.style.fontSize = `${fontSize}px`;
        const select = () => {
          layer.querySelectorAll('[data-result-marker]').forEach(node => node.classList.toggle('is-selected', node === group));
          leaders.querySelectorAll('path').forEach(node => node.classList.toggle('is-selected', node === leader));
          chart.querySelector('[data-hover]').setAttribute('hidden', '');
          readout.textContent = description;
          // Coincident dots remain independently selectable through their labels.
          let active = layer.querySelector('[data-active-observation]');
          if (!active) active = element('circle', {'data-active-observation': '',
            r: compact ? 8 : 6, fill: '#123e70', stroke: '#fff', 'stroke-width': 2,
            'pointer-events': 'none'}, layer);
          active.setAttribute('cx', point.px);
          active.setAttribute('cy', point.py);
        };
        group.addEventListener('pointerenter', select);
        group.addEventListener('focus', select);
        group.addEventListener('click', select);
        group.addEventListener('keydown', event => {
          if (['Enter', ' '].includes(event.key)) { event.preventDefault(); select(); }
        });
      });
    }
  },
};
