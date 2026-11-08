(() => {
  const search = document.querySelector('[data-events-search]');
  if (!search) return;
  const input = search.querySelector('input');
  const status = search.querySelector('[data-events-status]');
  const empty = document.querySelector('[data-events-empty]');
  const normalize = value => value.toLocaleLowerCase('ru').replaceAll('ё', 'е');
  const rows = Array.from(document.querySelectorAll('[data-event-row]'), row => ({
    row,
    name: normalize(row.querySelector('[data-event-name]').textContent),
  }));

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

  search.hidden = false;
  input.addEventListener('input', filterEvents);
  window.addEventListener('pageshow', filterEvents);
  filterEvents();
})();
