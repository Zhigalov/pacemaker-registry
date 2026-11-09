(() => {
  const widget = document.querySelector('[data-event-suggest]');
  if (!widget) return;
  const field = widget.parentElement;
  const select = field.querySelector('select');
  const input = widget.querySelector('input');
  const clear = widget.querySelector('button');
  const popup = widget.querySelector('.event-suggest-popup');
  const list = widget.querySelector('[role="listbox"]');
  const empty = widget.querySelector('[role="status"]');
  const normalize = text => text.toLocaleLowerCase('ru').replaceAll('ё', 'е');
  const options = Array.from(select.options, option => {
    const name = option.textContent.trim();
    return { id: option.value, name, search: normalize(name) };
  });
  let matches = [];
  let active = -1;

  function selectedName() {
    return select.value === '0' ? '' : select.selectedOptions[0].textContent.trim();
  }

  function close() {
    popup.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    input.value = selectedName();
    clear.hidden = select.value === '0';
    active = -1;
  }

  function show(query = '') {
    const words = normalize(query).trim().split(/\s+/).filter(Boolean);
    matches = options.filter(option => words.every(word => option.search.includes(word)));
    list.replaceChildren(...matches.map(({ id, name }, index) => {
      const option = document.createElement('li');
      option.id = `event-filter-option-${id}`;
      option.dataset.index = index;
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', String(id === select.value));
      option.textContent = name;
      return option;
    }));
    active = -1;
    list.scrollTop = 0;
    empty.hidden = matches.length > 0;
    popup.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    input.removeAttribute('aria-activedescendant');
  }

  function choose(id) {
    const changed = select.value !== id;
    select.value = id;
    close();
    if (changed) select.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function highlight(index) {
    active = index;
    for (const [i, option] of Array.from(list.children).entries()) {
      option.setAttribute('aria-selected', String(i === active));
      option.toggleAttribute('data-active', i === active);
    }
    const option = list.children[active];
    input.setAttribute('aria-activedescendant', option.id);
    option.scrollIntoView({ block: 'nearest' });
  }

  input.addEventListener('focus', () => {
    input.select();
    show();
  });
  input.addEventListener('click', () => { if (popup.hidden) show(); });
  input.addEventListener('input', () => show(input.value));
  input.addEventListener('keydown', event => {
    if (event.isComposing) return;
    if (event.key === 'Escape' && !popup.hidden) {
      event.preventDefault();
      close();
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (popup.hidden) show();
      if (!matches.length) return;
      if (event.key === 'ArrowDown') highlight(Math.min(active + 1, matches.length - 1));
      else highlight(active < 0 ? matches.length - 1 : Math.max(active - 1, 0));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (!popup.hidden && active >= 0) choose(matches[active].id);
      else if (!input.value.trim()) choose('0');
      else if (!popup.hidden && matches.length === 1) choose(matches[0].id);
    }
  });
  list.addEventListener('mousedown', event => {
    if (event.target.closest('[role="option"]')) event.preventDefault();
  });
  list.addEventListener('click', event => {
    const option = event.target.closest('[role="option"]');
    if (option) choose(matches[Number(option.dataset.index)].id);
  });
  widget.addEventListener('focusout', event => {
    if (!widget.contains(event.relatedTarget)) close();
  });
  clear.addEventListener('click', () => {
    input.focus();
    choose('0');
  });
  window.addEventListener('pageshow', close);

  field.querySelector('[data-event-filter-label]').htmlFor = input.id;
  field.querySelector('[data-event-filter-native]').hidden = true;
  widget.hidden = false;
  close();
})();
