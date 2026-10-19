// The Engine and updates section, a placeholder until T-071 builds updates.
import { html } from "htm/preact";

import { strings } from "../../strings/index.js";

const text = strings.settings;

// yt-dlp names its releases like 2026.08.19, and package metadata shortens that to 2026.8.19.
export function engineVersionLabel(version) {
  if (!version) return null;
  const [year, month, day, ...rest] = version.split(".");
  if (!/^\d{4}$/.test(year) || !/^\d+$/.test(month ?? "") || !/^\d+$/.test(day ?? "")) {
    return version;
  }
  return [year, month.padStart(2, "0"), day.padStart(2, "0"), ...rest].join(".");
}

// TODO(T-071): Add the update checks, the channel, Update now and Revert, YouTube support,
// the client ID override, and the logs and diagnostics.
export function EngineSection({ components }) {
  const engineVersion = engineVersionLabel(components?.engine_version);
  return html`
    <div class="settings-card">
      <span class="settings-card-text">
        <span class="settings-card-title">${text.engine.app(components?.app_version ?? "")}</span>
      </span>
    </div>
    <div class="settings-card">
      <span class="settings-card-text">
        <span class="settings-card-title">${text.engine.engine(engineVersion)}</span>
        <span class="settings-card-detail">
          ${engineVersion ? text.engine.builtIn : text.engine.versionUnknown}
        </span>
      </span>
    </div>
    <p class="settings-hint">${text.engine.notBuilt}</p>
  `;
}
