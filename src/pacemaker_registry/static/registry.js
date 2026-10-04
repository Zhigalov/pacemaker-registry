const cards = document.querySelectorAll(".result-summary");
const registryFilters = document.querySelectorAll("[data-registry-filter]");
const ratingHelp = document.querySelector("[data-rating-help]");
const ratingMethodology = document.querySelector("#rating-methodology");
const ratingCharts = document.querySelectorAll("[data-rating-chart]");
const ratingModeFilter = document.querySelector("#rating-mode-filter");
const customPointsInput = document.querySelector("[data-custom-points-input]");
const customLeftStartInput = document.querySelector("[data-custom-left-start-input]");
const customRightStartInput = document.querySelector("[data-custom-right-start-input]");
const customLeftDecayInput = document.querySelector("[data-custom-left-decay-input]");
const customRightDecayInput = document.querySelector("[data-custom-right-decay-input]");
const customEditor = document.querySelector("[data-custom-editor]");
const customStorageKey = "pacemaker-custom-rating-v1";
const defaultCustomConfig = {
  points: [7, 8, 9, 10, 9, 8, 7],
  leftStart: 45,
  rightStart: 45,
  leftDecay: 30,
  rightDecay: 30,
};
let activePopover = null;

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function sanitizeCustomConfig(value) {
  const points = Array.isArray(value?.points) && value.points.length === 7
    ? value.points.map((point) => clamp(Number(point) || 0.1, 0.1, 10))
    : [...defaultCustomConfig.points];
  const numericOrDefault = (candidate, fallback) => {
    const numeric = Number(candidate);
    return Number.isFinite(numeric) ? numeric : fallback;
  };
  return {
    points,
    leftStart: Math.round(clamp(numericOrDefault(value?.leftStart ?? value?.start, 45), 0, 65)),
    rightStart: Math.round(clamp(numericOrDefault(value?.rightStart ?? value?.start, 45), 0, 65)),
    leftDecay: Math.round(clamp(numericOrDefault(value?.leftDecay ?? value?.decay, 30), 10, 90)),
    rightDecay: Math.round(clamp(numericOrDefault(value?.rightDecay ?? value?.decay, 30), 10, 90)),
  };
}

function configFromInputs() {
  return sanitizeCustomConfig({
    points: customPointsInput?.value.split(",").map(Number),
    leftStart: customLeftStartInput?.value,
    rightStart: customRightStartInput?.value,
    leftDecay: customLeftDecayInput?.value,
    rightDecay: customRightDecayInput?.value,
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
  [
    customPointsInput,
    customLeftStartInput,
    customRightStartInput,
    customLeftDecayInput,
    customRightDecayInput,
  ].forEach((input) => {
    if (input) input.disabled = !enabled;
  });
}

function writeCustomConfig(config) {
  if (customPointsInput) {
    customPointsInput.value = config.points
      .map((point) => Number(point.toFixed(1)))
      .join(",");
  }
  if (customLeftStartInput) customLeftStartInput.value = config.leftStart;
  if (customRightStartInput) customRightStartInput.value = config.rightStart;
  if (customLeftDecayInput) customLeftDecayInput.value = config.leftDecay;
  if (customRightDecayInput) customRightDecayInput.value = config.rightDecay;
  if (customEditor) {
    customEditor.dataset.customPoints = customPointsInput?.value || "";
    customEditor.dataset.customLeftStart = config.leftStart;
    customEditor.dataset.customRightStart = config.rightStart;
    customEditor.dataset.customLeftDecay = config.leftDecay;
    customEditor.dataset.customRightDecay = config.rightDecay;
  }
}

const query = new URLSearchParams(window.location.search);
const queryHasCustomConfig = [
  "custom_points",
  "custom_left_start",
  "custom_right_start",
  "custom_left_decay",
  "custom_right_decay",
]
  .every((parameter) => query.has(parameter))
  || ["custom_points", "custom_start", "custom_decay"]
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
  if (difference < 0) {
    const boundary = -config.leftStart;
    if (difference >= boundary) return calculateCustomLinearRating(difference, config.points);
    const boundaryRating = calculateCustomLinearRating(boundary, config.points);
    return boundaryRating * Math.exp((difference + config.leftStart) / config.leftDecay);
  }

  const boundary = config.rightStart;
  if (difference <= boundary) return calculateCustomLinearRating(difference, config.points);
  const boundaryRating = calculateCustomLinearRating(boundary, config.points);
  return boundaryRating * Math.exp(-(difference - config.rightStart) / config.rightDecay);
}

function calculateCustomLinearRating(difference, points) {
  if (difference < -45) {
    const edgeSlope = (points[1] - points[0]) / 15;
    return clamp(points[0] + edgeSlope * (difference + 45), 0.1, 10);
  }
  if (difference > 45) {
    const edgeSlope = (points[6] - points[5]) / 15;
    return clamp(points[6] + edgeSlope * (difference - 45), 0.1, 10);
  }
  const position = (difference + 45) / 15;
  const leftIndex = Math.min(Math.floor(position), points.length - 2);
  const fraction = position - leftIndex;
  return points[leftIndex] + (points[leftIndex + 1] - points[leftIndex]) * fraction;
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
  const leftBoundaryHandle = customEditor.querySelector('[data-custom-boundary-handle="left"]');
  const rightBoundaryHandle = customEditor.querySelector('[data-custom-boundary-handle="right"]');
  const leftDecayHandle = customEditor.querySelector('[data-custom-decay-handle="left"]');
  const rightDecayHandle = customEditor.querySelector('[data-custom-decay-handle="right"]');
  const leftStartDisplay = document.querySelector("[data-custom-left-start-display]");
  const rightStartDisplay = document.querySelector("[data-custom-right-start-display]");
  const leftDecayDisplay = document.querySelector("[data-custom-left-decay-display]");
  const rightDecayDisplay = document.querySelector("[data-custom-right-decay-display]");

  const pathPoints = [];
  for (let difference = -75; difference <= 75; difference += 1) {
    pathPoints.push(
      `${differenceToX(difference).toFixed(1)},${ratingToY(calculateCustomRating(difference, customConfig)).toFixed(1)}`,
    );
  }
  path?.setAttribute("points", pathPoints.join(" "));

  pointElements.forEach((point, index) => {
    point.setAttribute("cy", ratingToY(customConfig.points[index]));
    const difference = -45 + index * 15;
    const inactive = difference < -customConfig.leftStart
      || difference > customConfig.rightStart;
    point.classList.toggle("custom-rating-point--inactive", inactive);
  });

  const leftX = differenceToX(-customConfig.leftStart);
  const rightX = differenceToX(customConfig.rightStart);
  boundaryLeft?.setAttribute("x1", leftX);
  boundaryLeft?.setAttribute("x2", leftX);
  boundaryRight?.setAttribute("x1", rightX);
  boundaryRight?.setAttribute("x2", rightX);
  leftBoundaryHandle?.setAttribute(
    "d",
    `M${leftX} 191 L${leftX + 11} 202 L${leftX} 213 L${leftX - 11} 202 Z`,
  );
  rightBoundaryHandle?.setAttribute(
    "d",
    `M${rightX} 191 L${rightX + 11} 202 L${rightX} 213 L${rightX - 11} 202 Z`,
  );
  leftDecayHandle?.setAttribute(
    "cy",
    ratingToY(calculateCustomRating(-75, customConfig)),
  );
  rightDecayHandle?.setAttribute(
    "cy",
    ratingToY(calculateCustomRating(75, customConfig)),
  );
  if (leftStartDisplay) leftStartDisplay.textContent = `−${customConfig.leftStart} с`;
  if (rightStartDisplay) rightStartDisplay.textContent = `+${customConfig.rightStart} с`;
  if (leftDecayDisplay) leftDecayDisplay.textContent = customConfig.leftDecay;
  if (rightDecayDisplay) rightDecayDisplay.textContent = customConfig.rightDecay;
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
      if (dragControl.side === "left") {
        customConfig.leftStart = Math.round(clamp(-difference, 0, 65));
      } else {
        customConfig.rightStart = Math.round(clamp(difference, 0, 65));
      }
    } else if (dragControl.type === "decay") {
      const targetRating = clamp(10 - (position.y - 18) / 184 * 10, 0.1, 9.9);
      const isLeft = dragControl.side === "left";
      const start = isLeft ? customConfig.leftStart : customConfig.rightStart;
      const boundary = isLeft ? -start : start;
      const boundaryRating = calculateCustomLinearRating(boundary, customConfig.points);
      const ratio = clamp(targetRating / boundaryRating, 0.02, 0.98);
      const decay = Math.round(clamp(
        -(75 - start) / Math.log(ratio),
        10,
        90,
      ));
      if (isLeft) customConfig.leftDecay = decay;
      else customConfig.rightDecay = decay;
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
      : {
        type: boundary ? "boundary" : "decay",
        side: (boundary || decay).dataset.customBoundaryHandle
          || (boundary || decay).dataset.customDecayHandle,
      };
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
