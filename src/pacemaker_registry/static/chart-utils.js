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
  observe(svg, render) {
    if (window.ResizeObserver) new ResizeObserver(render).observe(svg);
  },
};
