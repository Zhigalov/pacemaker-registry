(() => {
  const pagination = document.querySelector('[data-registry-pagination]');
  const next = pagination?.querySelector('[data-next-page]');
  const list = document.querySelector('.registry-list');
  if (!next || !list || !('IntersectionObserver' in window)) return;

  const status = pagination.querySelector('[data-page-status]');
  let loading = false;
  let failed = false;
  const observer = new IntersectionObserver(entries => {
    if (entries.some(entry => entry.isIntersecting) && !failed) loadNext();
  }, { rootMargin: '300px' });

  async function loadNext() {
    if (loading) return;
    loading = true;
    failed = false;
    observer.unobserve(pagination);
    next.setAttribute('aria-disabled', 'true');
    status.textContent = 'Загружаем…';
    try {
      const url = new URL(next.href);
      url.searchParams.set('fragment', 'true');
      const response = await fetch(url, { signal: AbortSignal.timeout(20000) });
      if (!response.ok) throw new Error('Page unavailable');
      const document = new DOMParser().parseFromString(await response.text(), 'text/html');
      const fragment = document.querySelector('[data-registry-fragment]');
      const rows = fragment?.querySelectorAll('.pacemaker-row');
      if (!rows?.length) throw new Error('No results');
      list.append(...rows);
      for (const row of rows) {
        for (const image of row.querySelectorAll('[data-avatar-image]')) {
          image.onerror = () => { image.hidden = true; };
          if (image.complete && !image.naturalWidth) image.hidden = true;
        }
      }
      status.textContent = 'Показано пейсмейкеров: ' + list.querySelectorAll('.pacemaker-row').length;
      if (fragment.dataset.nextUrl) {
        next.href = fragment.dataset.nextUrl;
        observer.observe(pagination);
      } else {
        next.hidden = true;
        observer.disconnect();
      }
    } catch {
      failed = true;
      status.textContent = 'Не удалось загрузить. Нажмите «Далее», чтобы повторить.';
      observer.observe(pagination);
    } finally {
      loading = false;
      next.removeAttribute('aria-disabled');
    }
  }

  next.addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    loadNext();
  });
  observer.observe(pagination);
})();
