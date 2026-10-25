document.querySelectorAll("[data-registry-filter]").forEach((filter) => {
  filter.addEventListener("change", () => filter.form?.requestSubmit());
});
