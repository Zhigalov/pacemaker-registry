const fetchForm = document.querySelector(".js-fetch-result");
const targetTimeInput = document.querySelector("#target-time");
const targetPaceOutput = document.querySelector("#target-pace");

function setFetchLoading(isLoading) {
  if (!fetchForm) return;
  const button = fetchForm.querySelector("button[type='submit']");
  fetchForm.setAttribute("aria-busy", String(isLoading));
  fetchForm.classList.toggle("is-loading", isLoading);
  if (button) button.disabled = isLoading;
}

function calculateTargetPace() {
  if (!targetTimeInput || !targetPaceOutput) return;

  const match = targetTimeInput.value.trim().match(/^(\d{1,2}):([0-5]\d)$/);
  const distance = Number(targetPaceOutput.dataset.distance.replace(",", "."));
  if (!match || !Number.isFinite(distance) || distance <= 0) {
    targetPaceOutput.value = "—";
    return;
  }

  const totalSeconds = (Number(match[1]) * 60 + Number(match[2])) * 60;
  const secondsPerKm = Math.round(totalSeconds / distance);
  const minutes = Math.floor(secondsPerKm / 60);
  const seconds = String(secondsPerKm % 60).padStart(2, "0");
  targetPaceOutput.value = `${String(minutes).padStart(2, "0")}:${seconds} /км`;
}

if (fetchForm) {
  fetchForm.addEventListener("submit", () => setFetchLoading(true));
  window.addEventListener("pageshow", () => setFetchLoading(false));
}

if (targetTimeInput && targetPaceOutput) {
  targetTimeInput.addEventListener("input", calculateTargetPace);
  calculateTargetPace();
}
