(() => {
  const controls = document.querySelector('[data-events-controls]');
  if (!controls) return;
  const input = controls.querySelector('input');
  const sort = controls.querySelector('[data-events-sort]');
  const summary = sort.querySelector('summary');
  const sortLabel = sort.querySelector('[data-sort-label]');
  const options = Array.from(sort.querySelectorAll('[data-sort-key]'));
  const status = controls.querySelector('[data-events-status]');
  const list = document.querySelector('#events-list');
  const empty = document.querySelector('[data-events-empty]');
  const normalize = value => value.toLocaleLowerCase('ru').replaceAll('ё', 'е');
  const collator = new Intl.Collator('ru', { sensitivity: 'base', numeric: true });
  const rows = Array.from(list.querySelectorAll('[data-event-row]'), row => ({
    row,
    name: normalize(row.querySelector('[data-event-name]').textContent),
    participants: Number(row.dataset.participants),
    rating: Number(row.dataset.teamRating || '-1'),
  }));

  function sortEvents() {
    const key = options.find(option => option.getAttribute('aria-pressed') === 'true').dataset.sortKey;
    rows.sort((a, b) => (key === 'name' ? 0 : b[key] - a[key]) || collator.compare(a.name, b.name));
    list.append(...rows.map(({ row }) => row));
  }

  function filterEvents() {
    const words = normalize(input.value).trim().split(/\s+/).filter(Boolean);
    let visible = 0;
    for (const { row, name } of rows) {
      row.hidden = !words.every(word => name.includes(word));
      if (!row.hidden) visible += 1;
    }
    empty.hidden = visible > 0;
    status.textContent = `Показано соревнований: ${visible} из ${rows.length}.`;
  }

  controls.hidden = false;
  input.addEventListener('input', filterEvents);
  for (const option of options) {
    option.addEventListener('click', () => {
      for (const other of options) other.setAttribute('aria-pressed', String(other === option));
      sortLabel.textContent = option.textContent;
      sortEvents();
      sort.open = false;
      summary.focus();
    });
  }
  sort.addEventListener('keydown', event => {
    if (event.key === 'Escape' && sort.open) {
      event.preventDefault();
      sort.open = false;
      summary.focus();
    }
  });
  sort.addEventListener('focusout', event => {
    if (event.relatedTarget && !sort.contains(event.relatedTarget)) sort.open = false;
  });
  document.addEventListener('click', event => {
    if (!sort.contains(event.target)) sort.open = false;
  });
  window.addEventListener('pageshow', () => {
    sortEvents();
    filterEvents();
  });
  sortEvents();
  filterEvents();
})();
