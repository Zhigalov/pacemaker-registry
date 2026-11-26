(() => {
  document.querySelectorAll('[data-remove-review-comment]').forEach(form => {
    form.addEventListener('submit', event => {
      if (!window.confirm('Удалить комментарий? Оценка сохранится.')) event.preventDefault();
    });
  });

  document.querySelectorAll('[data-edit-review-comment]').forEach(button => {
    const editor = document.getElementById(button.getAttribute('aria-controls'));
    const comment = button.closest('li').querySelector('.review-comment');
    function toggleEditor(open) {
      editor.hidden = !open;
      comment.hidden = open;
      button.setAttribute('aria-expanded', String(open));
      if (open) {
        editor.elements.namedItem('comment').focus();
      } else {
        editor.reset();
        button.focus();
      }
    }
    button.addEventListener('click', () => toggleEditor(editor.hidden));
    editor.querySelector('[data-cancel-review-edit]').addEventListener('click', () => toggleEditor(false));
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
