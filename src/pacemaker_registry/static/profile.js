(() => {
  'use strict';
  document.querySelector('[data-profile-filter]')?.addEventListener('change', event => event.target.form.requestSubmit());
  const fields = document.querySelector('[data-contact-fields]');
  document.querySelector('[data-add-contact]')?.addEventListener('click', () => {
    if (fields.querySelectorAll('[name="contact_type"]').length >= 20) return;
    const fragment = document.querySelector('#other-contact-template').content.cloneNode(true);
    const input = fragment.querySelector('input[name="contact_label"]');
    fields.append(fragment);
    input.focus();
  });
  fields?.addEventListener('click', event => event.target.closest('[data-remove-contact]')?.closest('.profile-contact-row').remove());
  document.querySelectorAll('[data-avatar-image], [data-avatar-preview]').forEach(image => {
    image.addEventListener('error', () => { image.hidden = true; });
  });
  const input = document.querySelector('[data-avatar-input]');
  const preview = document.querySelector('[data-avatar-preview]');
  const remove = document.querySelector('[data-avatar-remove]');
  const original = preview?.getAttribute('src');
  const filename = document.querySelector('[data-photo-filename]');
  const form = input?.form;
  const photoError = document.querySelector('[data-photo-error]');
  let preparedSource, preparedPhoto, busy = false;
  function error(message) {
    photoError.textContent = message;
    photoError.hidden = !message;
    if (message) photoError.scrollIntoView({block: 'center', behavior: 'smooth'});
  }
  // Native submission still handles redirects/validation pages. Replace only the file part.
  form?.addEventListener('formdata', event => {
    if (preparedPhoto && input.files[0] === preparedSource && !remove?.checked) {
      event.formData.set('photo', preparedPhoto, 'avatar.jpg');
    }
  });
  form?.addEventListener('submit', async event => {
    const file = input.files[0];
    if (!file || remove?.checked) return;
    if (!('FormDataEvent' in window)) {
      if (file.size <= 2 * 1024 * 1024) return;
      event.preventDefault();
      error('В этом браузере выберите фото до 2 МБ или обновите браузер для автоматического сжатия.');
      return;
    }
    if (preparedSource === file && preparedPhoto) return;
    event.preventDefault();
    if (busy) return;
    busy = true;
    const button = event.submitter || form.querySelector('[type="submit"]');
    const label = button.textContent;
    button.disabled = true;
    button.textContent = 'Подготавливаем фото…';
    error('');
    try {
      const photo = await PacersAvatar.prepare(file);
      // The user can select/remove a file while decoding; never send a stale or raw large file.
      if (input.files[0] !== file || remove?.checked) return;
      preparedSource = file;
      preparedPhoto = photo;
    } catch (failure) {
      error(failure.message || 'Не удалось подготовить фото. Выберите другой файл.');
    } finally {
      busy = false;
      button.disabled = false;
      button.textContent = label;
    }
    if (preparedSource === input.files[0] && preparedPhoto) form.requestSubmit(button);
  });
  let objectURL;
  input?.addEventListener('change', () => {
    preparedSource = preparedPhoto = null;
    error('');
    if (objectURL) URL.revokeObjectURL(objectURL);
    const file = input.files[0];
    if (filename) filename.textContent = file ? file.name : 'Файл не выбран';
    input.setCustomValidity(file && file.size > 5 * 1024 * 1024 ? 'Выберите фотографию размером до 5 МБ.' : '');
    if (!input.checkValidity()) { input.reportValidity(); return; }
    if (file) {
      objectURL = URL.createObjectURL(file);
      preview.src = objectURL;
      preview.hidden = false;
      if (remove) remove.checked = false;
    } else {
      preview.hidden = !original;
      if (original) preview.src = original;
    }
  });
  remove?.addEventListener('change', () => {
    if (remove.checked) { input.value = ''; input.setCustomValidity(''); preview.hidden = true; if (filename) filename.textContent = 'Фотография будет удалена'; }
    else if (original) { preview.src = original; preview.hidden = false; if (filename) filename.textContent = 'Файл не выбран'; }
  });
})();
