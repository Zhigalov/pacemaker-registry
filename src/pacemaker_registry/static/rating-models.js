/* Pure chart calculations. Kept independent of DOM for Python/JS parity tests. */
(function(root) {
  function finish(t, c) {
    if (t >= c.left_good && t <= c.right_good) return 10;
    const left = t < c.left_good, side = left ? 'left' : 'right';
    const good = c[side + '_good'], bad = c[side + '_bad'];
    const width = Math.abs(good - bad), edge = c[side + '_score'];
    if (width > 0 && (left ? t >= bad : t <= bad)) {
      return 10 - (10 - edge) * Math.abs(t - good) / width;
    }
    return (width > 0 ? edge : 10) * Math.exp(-Math.min(700,
      (Math.abs(t - bad) / c[side + '_decay']) ** c[side + '_curve']));
  }

  function pace(seconds, c) {
    const d = Math.abs(seconds);
    if (d <= c.tolerance) return 10;
    if (d <= c.checkpoint) return 10 - (10 - c.checkpoint_score) * (d - c.tolerance) / (c.checkpoint - c.tolerance);
    if (d <= c.bad) return c.checkpoint_score - (c.checkpoint_score - c.bad_score) * (d - c.checkpoint) / (c.bad - c.checkpoint);
    return c.bad_score * Math.exp(-Math.min(700, ((d - c.bad) / c.decay) ** c.curve));
  }

  const models = {finish, pace};
  if (typeof module !== 'undefined' && module.exports) module.exports = models;
  else root.ratingModels = models;
})(globalThis);
