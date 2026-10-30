// Keeps keyboard focus in the window: when the focused control goes away, such as a banner's
// Retry button or a dialog that closes on its own, focus moves to the current view's heading
// instead of falling to the page, where the next Tab would start over from the top.

// Where focus lands, in order: a view's own heading, then the selected queue item, then the
// link field. A new view adds its heading here.
const HOMES = [
  ".settings-title",
  ".first-run-heading",
  ".app-editor h1[tabindex]",
  '.queue-item[aria-current="true"]',
  "#top-bar-paste",
];

// The control that last had focus.
let lastFocused = null;
let checkPending = false;

// A control is gone when it left the page, or sits somewhere that can't be seen or used now.
function isGone(element) {
  if (!element.isConnected) return true;
  if (element.closest("[inert]") || element.matches(":disabled")) return true;
  return element.getClientRects().length === 0;
}

function home() {
  for (const selector of HOMES) {
    const element = document.querySelector(selector);
    if (element && !isGone(element)) return element;
  }
  return null;
}

function rescueFocus() {
  checkPending = false;
  if (document.activeElement !== document.body || !lastFocused) return;
  // An open dialog puts focus back itself when it closes.
  if (document.querySelector("dialog[open]")) return;
  if (!isGone(lastFocused)) return;
  lastFocused = null;
  home()?.focus({ preventScroll: true });
}

// Checked once per frame at most, after the page has changed.
function scheduleCheck() {
  if (checkPending) return;
  checkPending = true;
  requestAnimationFrame(rescueFocus);
}

export function keepFocus(root = document.body) {
  document.addEventListener("focusin", (event) => {
    lastFocused = event.target;
  });
  // Removing a focused element fires no event, so changes to the page trigger the check.
  new MutationObserver(scheduleCheck).observe(root, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["disabled", "inert", "hidden", "open"],
  });
}
