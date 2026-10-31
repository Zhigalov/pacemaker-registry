document.querySelectorAll("[data-registry-filter]").forEach((filter) => {
  filter.addEventListener("change", () => filter.form?.requestSubmit());
});
document.querySelectorAll('[data-avatar-image]').forEach((image) => {
  image.addEventListener('error', () => { image.hidden = true; });
});
