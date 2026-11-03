document.querySelectorAll("[data-registry-filter]").forEach((filter) => {
  filter.addEventListener("change", () => filter.form?.requestSubmit());
});
document.querySelectorAll('[data-avatar-image]').forEach((image) => {
  image.addEventListener('error', () => { image.hidden = true; });
});

document.querySelectorAll('[data-registry-search]').forEach((input) => {
  // Native search-field clear also works without losing the other form controls.
  input.addEventListener('input', () => {
    if (!input.value) input.form?.requestSubmit();
  });
});
