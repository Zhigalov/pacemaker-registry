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
  let objectURL;
  input?.addEventListener('change', () => {
    if (objectURL) URL.revokeObjectURL(objectURL);
    const file = input.files[0];
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
    if (remove.checked) { input.value = ''; input.setCustomValidity(''); preview.hidden = true; }
    else if (original) { preview.src = original; preview.hidden = false; }
  });
})();
