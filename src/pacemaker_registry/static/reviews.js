(() => {
  document.querySelectorAll('[data-remove-review-comment]').forEach(form => {
    form.addEventListener('submit', event => {
      if (!window.confirm('Удалить комментарий? Оценка сохранится.')) event.preventDefault();
    });
  });

  const form = document.querySelector('[data-review-form]');
  if (!form) return;

  // Use the form path so the short and numeric profile URLs share one tab draft.
  const key = 'review-draft:' + new URL(form.action).pathname;
  const score = form.elements.namedItem('score');
  const event = form.elements.namedItem('event_id');

  try {
    const draft = JSON.parse(sessionStorage.getItem(key));
    if (draft) {
      if (Array.from(event.options).some(option => option.value === draft.event)) {
        event.value = draft.event;
      }
      score.value = draft.score;
    }
  } catch {
    // The form still works if the browser disables storage.
  }

  form.addEventListener('change', ({target}) => {
    if (target.name !== 'score' && target !== event) return;
    try {
      sessionStorage.setItem(key, JSON.stringify({score: score.value, event: event.value}));
    } catch {}
  });

  form.addEventListener('submit', () => {
    try {
      sessionStorage.removeItem(key);
    } catch {}
  });
})();
