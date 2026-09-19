const cards = document.querySelectorAll(".result-summary");
let activePopover = null;

function hidePopover(popover) {
  if (!popover) return;
  if (typeof popover.hidePopover === "function" && popover.matches(":popover-open")) {
    popover.hidePopover();
  }
  popover.classList.remove("result-popover--fallback-open");
  if (activePopover === popover) activePopover = null;
}

function placePopover(card, popover) {
  const cardRect = card.getBoundingClientRect();
  const gap = 10;
  const edge = 12;
  const width = popover.offsetWidth;
  const height = popover.offsetHeight;
  const left = Math.min(
    Math.max(edge, cardRect.left),
    window.innerWidth - width - edge,
  );
  const below = cardRect.bottom + gap;
  const top = below + height <= window.innerHeight - edge
    ? below
    : Math.max(edge, cardRect.top - height - gap);

  popover.style.left = `${left}px`;
  popover.style.top = `${top}px`;
}

function showPopover(card, popover) {
  if (activePopover && activePopover !== popover) hidePopover(activePopover);
  if (typeof popover.showPopover === "function") {
    if (!popover.matches(":popover-open")) popover.showPopover();
  } else {
    popover.classList.add("result-popover--fallback-open");
  }
  activePopover = popover;
  requestAnimationFrame(() => placePopover(card, popover));
}

cards.forEach((card) => {
  const popover = document.getElementById(card.dataset.tooltipId);
  if (!popover) return;

  card.addEventListener("mouseenter", () => showPopover(card, popover));
  card.addEventListener("mouseleave", () => hidePopover(popover));
  card.addEventListener("focus", () => showPopover(card, popover));
  card.addEventListener("blur", () => hidePopover(popover));
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") hidePopover(activePopover);
});

window.addEventListener("resize", () => hidePopover(activePopover));
window.addEventListener("scroll", () => hidePopover(activePopover), true);
