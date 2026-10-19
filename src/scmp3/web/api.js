// How the UI talks to Python: calls go to window.pywebview.api, and events come back through
// window.scmp3.emit. pywebview injects its bridge only after the page has loaded, so the app waits
// for "pywebviewready". `scmp3 --dev-ui` opens the page with ?mock, which loads web/mock/ instead.

// How long to wait for the bridge before hinting, in the console, that ?mock may be missing.
const BRIDGE_HINT_MS = 5000;

const listeners = new Set();

// Python calls this for each event, through evaluate_js, and the mock calls it the same way.
window.scmp3 = {
  emit(event) {
    for (const listener of listeners) {
      // A batch calls emit once per event, so one failing listener mustn't stop the rest.
      try {
        listener(event);
      } catch (error) {
        console.error("An event listener failed", event, error);
      }
    }
  },
};

export function onEvent(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function usesMock() {
  return new URLSearchParams(window.location.search).has("mock");
}

function waitForPywebview() {
  return new Promise((resolve) => {
    if (window.pywebview?.api?.snapshot) {
      resolve(window.pywebview.api);
      return;
    }
    const hint = setTimeout(() => {
      console.info("Still waiting for pywebview. Outside the app, open this page with ?mock.");
    }, BRIDGE_HINT_MS);
    window.addEventListener(
      "pywebviewready",
      () => {
        clearTimeout(hint);
        resolve(window.pywebview.api);
      },
      { once: true },
    );
  });
}

async function loadMock() {
  // Imported only in dev mode, so the app never loads the sample data.
  const { mockApi } = await import("./mock/index.js");
  return mockApi;
}

const backend = usesMock() ? loadMock() : waitForPywebview();

// Every call by its Python name, such as api.save([id]), without a list to keep up to date.
// Calls made before the bridge is ready wait for it.
export const api = new Proxy(
  {},
  {
    get(_target, name) {
      // Not a thenable, so awaiting api by mistake doesn't call a "then" method on Python.
      if (name === "then") return undefined;
      return async (...args) => {
        const target = await backend;
        if (typeof target[name] !== "function") {
          throw new Error(`The API has no call named ${String(name)}`);
        }
        return target[name](...args);
      };
    },
  },
);
