// The mock API for --dev-ui: the same calls as window.pywebview.api, backed by sample data.
// Calls that succeed and have nothing to return answer {}, and failures answer {error: kind}.
import "./drops.js";

import * as account from "./account.js";
import * as components from "./components.js";
import * as files from "./files.js";
import * as jobs from "./jobs.js";
import * as settings from "./settings.js";

// One module per area, like the Python mixins. Each exports its calls and a snapshotPart().
const AREAS = [jobs, files, settings, account, components];

// Calls answer after a short wait, like the real bridge, so the UI never counts on instant replies.
const LATENCY_MS = 30;
// ?mock&slow_start holds the snapshot back, for trying what the window shows while it starts.
const SLOW_START_MS = new URLSearchParams(window.location.search).has("slow_start") ? 3000 : 0;

function snapshot() {
  return Object.assign({}, ...AREAS.map((area) => area.snapshotPart()));
}

async function slowSnapshot() {
  await new Promise((resolve) => setTimeout(resolve, SLOW_START_MS));
  return snapshot();
}

// A JSON round trip copies data both ways, the way pywebview's bridge does.
function copy(value) {
  return value === undefined ? null : JSON.parse(JSON.stringify(value));
}

function answering(call) {
  return async (...args) => {
    await new Promise((resolve) => setTimeout(resolve, LATENCY_MS));
    return copy(await call(...copy(args)));
  };
}

function buildApi() {
  const api = { snapshot: SLOW_START_MS ? answering(slowSnapshot) : answering(snapshot) };
  for (const area of AREAS) {
    for (const [name, call] of Object.entries(area)) {
      if (name !== "snapshotPart") api[name] = answering(call);
    }
  }
  return api;
}

export const mockApi = buildApi();
