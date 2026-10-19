// The update and component calls, with made-up versions.

const SHIPPED_ENGINE = "2026.08.19";
const NEWER_ENGINE = "2026.10.09";

let components = { app_version: "0.0.1", engine_version: SHIPPED_ENGINE };

function emit(event) {
  window.scmp3.emit(JSON.parse(JSON.stringify(event)));
}

function setEngine(version) {
  components = { ...components, engine_version: version };
  emit({ type: "components", components });
}

export function snapshotPart() {
  return { components };
}

export function check_updates() {
  emit({ type: "toast", level: "info", text: "sc-mp3 and the download engine are up to date." });
  return {};
}

export function update_engine() {
  setEngine(NEWER_ENGINE);
  return {};
}

export function revert_engine() {
  setEngine(SHIPPED_ENGINE);
  return {};
}

export function install_youtube_support() {
  emit({ type: "toast", level: "info", text: "YouTube support is installed." });
  return {};
}
