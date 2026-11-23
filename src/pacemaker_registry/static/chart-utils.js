/* Shared SVG primitives; finish and pace editors retain their own controls. */
window.ratingChart = {
  clamp: (value, lo, hi) => Math.min(hi, Math.max(lo, value)),
  y: score => 26 + (10 - score) * 23.2,
  element(tag, attrs, text) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    if (text !== undefined) node.textContent = text;
    return node;
  },
  position(svg, event) {
    const point = svg.createSVGPoint();
    point.x = event.clientX; point.y = event.clientY;
    return point.matrixTransform(svg.getScreenCTM().inverse());
  },
  grid(svg) {
    const groups = ['zones', 'grid', 'curves', 'handles'].map(name => svg.querySelector(`[data-${name}]`));
    groups.forEach(group => group.replaceChildren());
    for (let score = 0; score <= 10; score += 2) {
      groups[1].append(this.element('line', {x1: 52, x2: 752, y1: this.y(score), y2: this.y(score)}),
        this.element('text', {x: 36, y: this.y(score) + 4, 'text-anchor': 'end'}, score));
    }
    return groups;
  },
  hover(svg, x, score) {
    const hover = svg.querySelector('[data-hover]');
    hover.removeAttribute('hidden');
    hover.querySelector('line').setAttribute('x1', x);
    hover.querySelector('line').setAttribute('x2', x);
    hover.querySelector('circle').setAttribute('cx', x);
    hover.querySelector('circle').setAttribute('cy', this.y(score));
  },
  interact(svg, {start, move, end, keys, step}) {
    const controller = {drag: null};
    svg.addEventListener('pointerdown', event => {
      controller.drag = start(event);
      if (controller.drag) { event.preventDefault(); svg.setPointerCapture(event.pointerId); }
    });
    svg.addEventListener('pointermove', event => {
      if (!event.target.closest('[data-result-marker]')) move(this.position(svg, event), controller.drag);
    });
    const finish = () => { if (controller.drag) { controller.drag = null; end(); } };
    ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(name => svg.addEventListener(name, finish));
    svg.addEventListener('pointerleave', () => {
      if (!controller.drag) svg.querySelector('[data-hover]').setAttribute('hidden', '');
    });
    svg.addEventListener('keydown', event => {
      if (keys.includes(event.key)) { event.preventDefault(); step(event.key); }
    });
    return controller;
  },
  observe(svg, render) {
    if (window.ResizeObserver) new ResizeObserver(render).observe(svg);
  },
};
