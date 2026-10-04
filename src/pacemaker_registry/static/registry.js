const cards = document.querySelectorAll(".result-summary");
const registryFilters = document.querySelectorAll("[data-registry-filter]");
const ratingHelp = document.querySelector("[data-rating-help]");
const ratingMethodology = document.querySelector("#rating-methodology");
const ratingCharts = document.querySelectorAll("[data-rating-chart]");
let activePopover = null;

registryFilters.forEach((filter) => {
  filter.addEventListener("change", () => filter.form?.requestSubmit());
});

ratingHelp?.addEventListener("click", () => {
  if (!ratingMethodology) return;
  ratingMethodology.open = true;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  ratingMethodology.scrollIntoView({
    behavior: reduceMotion ? "auto" : "smooth",
    block: "start",
  });
});

function calculateChartRating(difference, mode) {
  if (mode === "symmetric") {
    const absoluteDifference = Math.abs(difference);
    if (absoluteDifference <= 45) return 10 - absoluteDifference / 15;
    return 7 * Math.exp(-(absoluteDifference - 45) / 30);
  }
  if (difference < -45) return 7 * Math.exp((difference + 45) / 30);
  if (difference <= 0) return 10 + difference / 15;
  return 10 * Math.exp(-difference / 30);
}

function formatDifference(difference) {
  if (difference === 0) return "ровно";
  return `${difference > 0 ? "+" : "−"}${Math.abs(difference)} с`;
}

ratingCharts.forEach((chart) => {
  const svg = chart.querySelector(".rating-chart-svg");
  const cursor = chart.querySelector("[data-rating-chart-cursor]");
  const cursorLine = cursor?.querySelector("line");
  const cursorPoint = cursor?.querySelector("circle");
  const tooltip = chart.querySelector("[data-rating-chart-tooltip]");
  const time = chart.querySelector("[data-rating-chart-time]");
  const score = chart.querySelector("[data-rating-chart-score]");
  if (!svg || !cursor || !cursorLine || !cursorPoint || !tooltip || !time || !score) return;

  let selectedDifference = 0;

  const showRating = (difference) => {
    selectedDifference = Math.max(-75, Math.min(75, Math.round(difference)));
    const rating = calculateChartRating(selectedDifference, chart.dataset.ratingMode);
    const x = 48 + (selectedDifference + 75) / 150 * 564;
    const y = 18 + (10 - rating) / 10 * 184;

    cursorLine.setAttribute("x1", x);
    cursorLine.setAttribute("x2", x);
    cursorPoint.setAttribute("cx", x);
    cursorPoint.setAttribute("cy", y);
    tooltip.style.left = `${x / 6.6}%`;
    tooltip.style.top = `${y / 2.6}%`;
    tooltip.classList.toggle("rating-chart-tooltip--below", y < 62);
    tooltip.hidden = false;
    time.textContent = formatDifference(selectedDifference);
    score.textContent = `${rating.toFixed(1).replace(".", ",")} балла`;
    chart.classList.add("rating-chart--active");
    svg.setAttribute(
      "aria-label",
      `${formatDifference(selectedDifference)}, ${rating.toFixed(1)} балла`,
    );
  };

  const showRatingFromPointer = (event) => {
    const bounds = svg.getBoundingClientRect();
    const svgX = (event.clientX - bounds.left) / bounds.width * 660;
    showRating((svgX - 48) / 564 * 150 - 75);
  };

  const hideRating = () => {
    tooltip.hidden = true;
    chart.classList.remove("rating-chart--active");
  };

  svg.addEventListener("pointerenter", showRatingFromPointer);
  svg.addEventListener("pointermove", showRatingFromPointer);
  svg.addEventListener("pointerdown", showRatingFromPointer);
  svg.addEventListener("pointerleave", (event) => {
    if (event.pointerType === "mouse") hideRating();
  });
  svg.addEventListener("focus", () => showRating(selectedDifference));
  svg.addEventListener("blur", hideRating);
  svg.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    showRating(selectedDifference + (event.key === "ArrowRight" ? 1 : -1));
  });
});

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
