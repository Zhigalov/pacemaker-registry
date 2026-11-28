(() => {
  const form = document.querySelector('[data-support-form]');
  if (!form) return;
  const files = form.elements.images;
  const channel = form.elements.contact_kind;
  const contact = form.elements.contact;
  const label = form.querySelector('[data-contact-label]');
  const list = form.querySelector('[data-file-list]');
  const error = form.querySelector('[data-support-error]');
  const button = form.querySelector('[type=submit]');
  const maxFiles = Number(form.dataset.maxFiles);
  const maxUpload = Number(form.dataset.maxUpload);
  let prepared, busy = false;

  function updateContact() {
    const email = channel.value === 'email';
    label.textContent = email ? 'Ваш email' : 'Ваш Telegram';
    contact.inputMode = email ? 'email' : 'text';
    contact.placeholder = email ? 'name@example.com' : '@username или ссылка t.me';
  }
  function showError(message) {
    error.textContent = message;
    error.hidden = !message;
  }
  function filesMatch(selected) {
    return selected.length === files.files.length && selected.every((file, i) => file === files.files[i]);
  }
  function resetFiles() {
    prepared = null;
    list.replaceChildren(...Array.from(files.files, file => {
      const item = document.createElement('li');
      item.textContent = file.name;
      return item;
    }));
    list.hidden = files.files.length === 0;
    const message = files.files.length > maxFiles ? 'Выберите не более пяти картинок.' : '';
    files.setCustomValidity(message);
    showError(message);
  }

  async function compress(file) {
    if (file.size <= maxUpload) return file;
    if (file.size > 16 * 1024 * 1024) throw new Error('Выберите картинку размером до 16 МБ.');
    const url = URL.createObjectURL(file);
    try {
      const image = new Image();
      image.src = url;
      await image.decode();
      const canvas = document.createElement('canvas');
      const scale = Math.min(1, 1600 / Math.max(image.naturalWidth, image.naturalHeight));
      if (!image.naturalWidth || !image.naturalHeight) throw new Error('Не удалось прочитать картинку.');
      for (const [size, quality] of [[scale, .85], [scale, .7], [scale * .75, .72], [scale * .6, .65]]) {
        canvas.width = Math.max(1, Math.round(image.naturalWidth * size));
        canvas.height = Math.max(1, Math.round(image.naturalHeight * size));
        const ctx = canvas.getContext('2d', {alpha: false});
        ctx.fillStyle = 'white';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
        const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', quality));
        if (blob?.size && blob.size <= maxUpload) return blob;
      }
      throw new Error('Не удалось уменьшить картинку. Выберите файл меньшего размера.');
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  channel.addEventListener('change', updateContact);
  files.addEventListener('change', resetFiles);
  updateContact();
  resetFiles();

  form.addEventListener('formdata', event => {
    if (!prepared || !filesMatch(prepared.source)) return;
    event.formData.delete('images');
    prepared.images.forEach((image, i) => event.formData.append('images', image,
      image === prepared.source[i] ? image.name : 'image-' + (i + 1) + '.jpg'));
  });
  form.addEventListener('submit', async event => {
    const source = Array.from(files.files);
    if (busy) { event.preventDefault(); return; }
    if (prepared && filesMatch(prepared.source)) return;
    if (source.every(file => file.size <= maxUpload)) return;
    event.preventDefault();
    if (!('FormDataEvent' in window)) {
      showError('Для этого браузера выберите картинки до ' + Math.floor(maxUpload / 1024) + ' КБ каждая.');
      return;
    }
    busy = true;
    button.disabled = true;
    button.textContent = 'Подготавливаем картинки…';
    showError('');
    try {
      const images = [];
      for (const file of source) images.push(await compress(file));
      if (filesMatch(source)) prepared = {source, images};
    } catch (failure) {
      if (filesMatch(source)) showError(failure.message || 'Не удалось подготовить картинки.');
    } finally {
      busy = false;
      button.disabled = false;
      button.textContent = 'Отправить обращение';
    }
    if (prepared && filesMatch(prepared.source)) form.requestSubmit(button);
  });
})();
