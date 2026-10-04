const cards = document.querySelectorAll(".result-summary");
const registryFilters = document.querySelectorAll("[data-registry-filter]");
const ratingHelp = document.querySelector("[data-rating-help]");
const ratingMethodology = document.querySelector("#rating-methodology");
const ratingCharts = document.querySelectorAll("[data-rating-chart]");
const ratingModeFilter = document.querySelector("#rating-mode-filter");
const customPointsInput = document.querySelector("[data-custom-points-input]");
const customStartInput = document.querySelector("[data-custom-start-input]");
const customDecayInput = document.querySelector("[data-custom-decay-input]");
const customEditor = document.querySelector("[data-custom-editor]");
const customStorageKey = "pacemaker-custom-rating-v1";
const defaultCustomConfig = {
  points: [7, 8, 9, 10, 9, 8, 7],
  start: 45,
  decay: 30,
};
let activePopover = null;

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function sanitizeCustomConfig(value) {
  const points = Array.isArray(value?.points) && value.points.length === 7
    ? value.points.map((point) => clamp(Number(point) || 0.1, 0.1, 10))
    : [...defaultCustomConfig.points];
  return {
    points,
    start: Math.round(clamp(Number(value?.start) || 45, 45, 65)),
    decay: Math.round(clamp(Number(value?.decay) || 30, 10, 90)),
  };
}

function configFromInputs() {
  return sanitizeCustomConfig({
    points: customPointsInput?.value.split(",").map(Number),
    start: customStartInput?.value,
    decay: customDecayInput?.value,
  });
}

function loadStoredCustomConfig() {
  try {
    const stored = localStorage.getItem(customStorageKey);
    return stored ? sanitizeCustomConfig(JSON.parse(stored)) : null;
  } catch (_error) {
    return null;
  }
}

function saveCustomConfig(config) {
  try {
    localStorage.setItem(customStorageKey, JSON.stringify(config));
  } catch (_error) {
    // The editor remains usable when private browsing disables local storage.
  }
}

function setCustomInputsEnabled(enabled) {
  [customPointsInput, customStartInput, customDecayInput].forEach((input) => {
    if (input) input.disabled = !enabled;
  });
}

function writeCustomConfig(config) {
  if (customPointsInput) {
    customPointsInput.value = config.points
      .map((point) => Number(point.toFixed(1)))
      .join(",");
  }
  if (customStartInput) customStartInput.value = config.start;
  if (customDecayInput) customDecayInput.value = config.decay;
  if (customEditor) {
    customEditor.dataset.customPoints = customPointsInput?.value || "";
    customEditor.dataset.customStart = config.start;
    customEditor.dataset.customDecay = config.decay;
  }
}

const query = new URLSearchParams(window.location.search);
const queryHasCustomConfig = ["custom_points", "custom_start", "custom_decay"]
  .every((parameter) => query.has(parameter));
const storedCustomConfig = loadStoredCustomConfig();
let customConfig = queryHasCustomConfig
  ? configFromInputs()
  : storedCustomConfig || configFromInputs();
writeCustomConfig(customConfig);
setCustomInputsEnabled(ratingModeFilter?.value === "custom");
if (queryHasCustomConfig) saveCustomConfig(customConfig);

registryFilters.forEach((filter) => {
  filter.addEventListener("change", () => {
    if (filter === ratingModeFilter) {
      setCustomInputsEnabled(filter.value === "custom");
      if (filter.value === "custom") writeCustomConfig(customConfig);
    }
    filter.form?.requestSubmit();
  });
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

if (
  ratingModeFilter?.value === "custom"
  && storedCustomConfig
  && !queryHasCustomConfig
) {
  setCustomInputsEnabled(true);
  ratingModeFilter.form?.requestSubmit();
}

if (window.location.hash === "#rating-methodology" && ratingMethodology) {
  ratingMethodology.open = true;
  requestAnimationFrame(() => ratingMethodology.scrollIntoView({ block: "start" }));
}

function calculateCustomRating(difference, config) {
  if (difference >= -45 && difference <= 45) {
    const position = (difference + 45) / 15;
    const leftIndex = Math.min(Math.floor(position), config.points.length - 2);
    const fraction = position - leftIndex;
    return config.points[leftIndex]
      + (config.points[leftIndex + 1] - config.points[leftIndex]) * fraction;
  }

  if (difference < -45) {
    const edgeSlope = (config.points[1] - config.points[0]) / 15;
    const boundary = clamp(
      config.points[0] + edgeSlope * (45 - config.start),
      0.1,
      10,
    );
    if (difference >= -config.start) {
      return clamp(config.points[0] + edgeSlope * (difference + 45), 0.1, 10);
    }
    return boundary * Math.exp((difference + config.start) / config.decay);
  }

  const edgeSlope = (config.points[6] - config.points[5]) / 15;
  const boundary = clamp(
    config.points[6] + edgeSlope * (config.start - 45),
    0.1,
    10,
  );
  if (difference <= config.start) {
    return clamp(config.points[6] + edgeSlope * (difference - 45), 0.1, 10);
  }
  return boundary * Math.exp(-(difference - config.start) / config.decay);
}

function calculateChartRating(difference, mode) {
  if (mode === "custom") return calculateCustomRating(difference, customConfig);
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

function ratingToY(rating) {
  return 18 + (10 - clamp(rating, 0, 10)) / 10 * 184;
}

function differenceToX(difference) {
  return 48 + (difference + 75) / 150 * 564;
}

function pointerToSvg(svg, event) {
  const bounds = svg.getBoundingClientRect();
  return {
    x: (event.clientX - bounds.left) / bounds.width * 660,
    y: (event.clientY - bounds.top) / bounds.height * 260,
  };
}

function renderCustomEditor() {
  if (!customEditor) return;
  const path = customEditor.querySelector("[data-custom-path]");
  const pointElements = customEditor.querySelectorAll("[data-custom-point]");
  const boundaryLeft = customEditor.querySelector("[data-custom-boundary-left]");
  const boundaryRight = customEditor.querySelector("[data-custom-boundary-right]");
  const boundaryHandle = customEditor.querySelector("[data-custom-boundary-handle]");
  const decayHandle = customEditor.querySelector("[data-custom-decay-handle]");
  const startDisplay = document.querySelector("[data-custom-start-display]");
  const decayDisplay = document.querySelector("[data-custom-decay-display]");

  const pathPoints = [];
  for (let difference = -75; difference <= 75; difference += 1) {
    pathPoints.push(
      `${differenceToX(difference).toFixed(1)},${ratingToY(calculateCustomRating(difference, customConfig)).toFixed(1)}`,
    );
  }
  path?.setAttribute("points", pathPoints.join(" "));

  pointElements.forEach((point, index) => {
    point.setAttribute("cy", ratingToY(customConfig.points[index]));
  });

  const rightX = differenceToX(customConfig.start);
  const leftX = differenceToX(-customConfig.start);
  [boundaryLeft, boundaryRight].forEach((line, index) => {
    const x = index === 0 ? leftX : rightX;
    line?.setAttribute("x1", x);
    line?.setAttribute("x2", x);
  });
  boundaryHandle?.setAttribute(
    "d",
    `M${rightX} 191 L${rightX + 11} 202 L${rightX} 213 L${rightX - 11} 202 Z`,
  );
  decayHandle?.setAttribute(
    "cy",
    ratingToY(calculateCustomRating(75, customConfig)),
  );
  if (startDisplay) startDisplay.textContent = `${customConfig.start} с`;
  if (decayDisplay) decayDisplay.textContent = customConfig.decay;
  writeCustomConfig(customConfig);
}

renderCustomEditor();

if (customEditor) {
  const svg = customEditor.querySelector(".rating-chart-svg");
  const reset = document.querySelector("[data-custom-reset]");
  const apply = document.querySelector("[data-custom-apply]");
  let dragControl = null;

  const updateDraggedControl = (event) => {
    if (!svg || !dragControl) return;
    const position = pointerToSvg(svg, event);
    if (dragControl.type === "point") {
      const rating = Math.round(clamp(10 - (position.y - 18) / 184 * 10, 0.1, 10) * 10) / 10;
      customConfig.points[dragControl.index] = rating;
    } else if (dragControl.type === "boundary") {
      const difference = (position.x - 48) / 564 * 150 - 75;
      customConfig.start = Math.round(clamp(difference, 45, 65));
    } else if (dragControl.type === "decay") {
      const targetRating = clamp(10 - (position.y - 18) / 184 * 10, 0.1, 9.9);
      const boundaryRating = calculateCustomRating(customConfig.start, customConfig);
      const ratio = clamp(targetRating / boundaryRating, 0.02, 0.98);
      customConfig.decay = Math.round(clamp(
        -(75 - customConfig.start) / Math.log(ratio),
        10,
        90,
      ));
    }
    renderCustomEditor();
  };

  svg?.addEventListener("pointerdown", (event) => {
    const point = event.target.closest?.("[data-custom-point]");
    const boundary = event.target.closest?.("[data-custom-boundary-handle]");
    const decay = event.target.closest?.("[data-custom-decay-handle]");
    if (!point && !boundary && !decay) return;
    event.preventDefault();
    dragControl = point
      ? { type: "point", index: Number(point.dataset.customPoint) }
      : { type: boundary ? "boundary" : "decay" };
    svg.setPointerCapture?.(event.pointerId);
    updateDraggedControl(event);
  });
  svg?.addEventListener("pointermove", updateDraggedControl);
  const finishDrag = () => {
    if (!dragControl) return;
    dragControl = null;
    saveCustomConfig(customConfig);
    writeCustomConfig(customConfig);
  };
  svg?.addEventListener("pointerup", finishDrag);
  svg?.addEventListener("pointercancel", finishDrag);

  reset?.addEventListener("click", () => {
    customConfig = sanitizeCustomConfig(defaultCustomConfig);
    renderCustomEditor();
    saveCustomConfig(customConfig);
  });

  apply?.addEventListener("click", () => {
    saveCustomConfig(customConfig);
    writeCustomConfig(customConfig);
    setCustomInputsEnabled(true);
    if (ratingModeFilter) ratingModeFilter.value = "custom";
    if (ratingModeFilter?.form) {
      ratingModeFilter.form.action = "/#rating-methodology";
      ratingModeFilter.form.requestSubmit();
    }
  });
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
    const x = differenceToX(selectedDifference);
    const y = ratingToY(rating);

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
    const position = pointerToSvg(svg, event);
    showRating((position.x - 48) / 564 * 150 - 75);
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
