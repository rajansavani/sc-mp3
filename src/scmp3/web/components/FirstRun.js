// First-run setup, shown instead of everything else until its last step is done. Each choice
// saves as it's made, and finishing saves first_run_done, so setup doesn't come back.
import { html } from "htm/preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { api } from "../api.js";
import { closeView, openDialog, reportError, useStore } from "../store.js";
import { strings } from "../strings/index.js";
import { folderLabel } from "./paths.js";
import { CHOICES, FolderField, useRadioGroup } from "./settings/fields.js";
import { Logo, SaveBanner } from "./settings/parts.js";
import { usePreview, useSettingsSaver } from "./settings/saving.js";

const text = strings.settings.firstRun;

// Saving the folder again makes sure it exists and can be written to before setup goes on.
async function confirmFolder(settings, save) {
  return (await save({ library_dir: settings.library_dir })).saved;
}

// Settings may have been opened with Ctrl+, during setup, and the queue comes first after it.
async function finishSetup(settings, save) {
  closeView();
  return (await save({ first_run_done: true })).saved;
}

// The steps in order. `beforeNext` runs when the step's button is pressed, and the step only
// moves on when it resolves to true.
const STEPS = [
  { id: "folder", Body: FolderStep, beforeNext: confirmFolder },
  { id: "titles", Body: TitlesStep },
  { id: "account", Body: AccountStep },
  { id: "spotify", Body: SpotifyStep, beforeNext: finishSetup },
];

// The title styles a card shows. Custom templates are set up in Settings.
const PRESETS = CHOICES.title_style.filter((style) => style !== "custom");

// The OS the app runs on, for Spotify's menus. WKWebView on macOS has no userAgentData.
export function currentOs() {
  const platform = navigator.userAgentData?.platform ?? navigator.platform ?? "";
  if (/mac/i.test(platform)) return "mac";
  if (/win/i.test(platform)) return "windows";
  return "linux";
}

function FolderStep({ settings, form }) {
  return html`
    <div class="first-run-body">
      <p class="first-run-intro">${text.folderIntro}</p>
      <${FolderField}
        label=${text.folderLabel}
        buttonLabel=${text.chooseFolder}
        folder=${settings.library_dir}
        error=${form.errors.library_dir}
        hint=${text.folderHint}
        save=${form.save}
      />
    </div>
  `;
}

function StyleCard({ style, settings, radio }) {
  const preview = usePreview(settings, style);
  return html`
    <button type="button" class="first-run-style" data-style=${style} ...${radio}>
      <span class="first-run-style-name">
        <span>${strings.settings.choices.title_style[style]}</span>
        <span class="first-run-style-dot" aria-hidden="true"></span>
      </span>
      <span class="first-run-style-part">
        <span class="first-run-style-label">${text.cardTitle}</span>
        <span class="first-run-style-title">${preview?.title ?? ""}</span>
      </span>
      <span class="first-run-style-part">
        <span class="first-run-style-label">${text.cardArtist}</span>
        <span>${preview?.artist ?? ""}</span>
      </span>
    </button>
  `;
}

function StyleCards({ settings, onPick }) {
  const { groupProps, choiceProps } = useRadioGroup(PRESETS, settings.title_style, onPick);
  return html`
    <div class="first-run-styles" aria-label=${text.titleStylesLabel} ...${groupProps}>
      ${PRESETS.map(
        (style, index) => html`
          <${StyleCard}
            key=${style}
            style=${style}
            settings=${settings}
            radio=${choiceProps(style, index)}
          />
        `,
      )}
    </div>
  `;
}

function TitlesStep({ settings, form }) {
  return html`
    <div class="first-run-body first-run-body-tight">
      <p class="first-run-intro">${text.titlesIntro}</p>
      <p class="first-run-sample">${text.sampleTitle}</p>
      <${StyleCards} settings=${settings} onPick=${(style) => form.save({ title_style: style })} />
      <span class="settings-hint">${text.titlesHint}</span>
    </div>
  `;
}

function BenefitsTable() {
  return html`
    <div class="first-run-table">
      <table>
        <thead>
          <tr>
            <th scope="col"><span class="visually-hidden">${text.benefitsCorner}</span></th>
            ${text.benefitsColumns.map(
              (column) => html`<th scope="col" key=${column}>${column}</th>`,
            )}
          </tr>
        </thead>
        <tbody>
          ${text.benefits.map(
            (row) => html`
              <tr key=${row.what}>
                <th scope="row">${row.what}</th>
                ${row.values.map((value, index) => html`<td key=${index}>${value}</td>`)}
              </tr>
            `,
          )}
        </tbody>
      </table>
    </div>
  `;
}

function PersonIcon() {
  return html`
    <svg class="first-run-icon" width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="8" r="4" />
      <path d="M4 20c1.5-4 4.5-6 8-6s6.5 2 8 6" />
    </svg>
  `;
}

// TODO(T-041): Sign in here, and show who's signed in.
function AccountStep() {
  return html`
    <div class="first-run-body first-run-body-tight">
      <p class="first-run-intro">${text.accountIntro}</p>
      <${BenefitsTable} />
      <div class="first-run-actions">
        <button
          type="button"
          class="control-button is-raised first-run-icon-button"
          onClick=${() => openDialog("sign_in")}
        >
          <${PersonIcon} />
          <span>${text.signIn}</span>
        </button>
        <span class="first-run-tip">${text.passwordTip}</span>
      </div>
    </div>
  `;
}

async function openSpotify() {
  reportError(await api.open_spotify());
}

function SpotifyStep({ settings }) {
  const steps = text.spotifySteps[currentOs()](folderLabel(settings.library_dir));
  return html`
    <div class="first-run-body">
      <p class="first-run-intro">${text.spotifyIntro}</p>
      <ol class="first-run-numbered">
        ${steps.map(
          (step, index) => html`
            <li class="first-run-numbered-step" key=${index}>
              <span class="first-run-badge" aria-hidden="true">${index + 1}</span>
              <span class="first-run-numbered-text">${step}</span>
            </li>
          `,
        )}
      </ol>
      <div class="first-run-actions">
        <button type="button" class="control-button is-raised" onClick=${openSpotify}>
          ${text.openSpotify}
        </button>
        <span class="first-run-tip">${text.phoneTip}</span>
      </div>
    </div>
  `;
}

function StepBar({ index }) {
  return html`
    <ol class="first-run-steps" aria-label=${text.stepsLabel}>
      ${STEPS.map(
        (step, position) => html`
          <li
            key=${step.id}
            class="first-run-step"
            aria-current=${position === index ? "step" : null}
            data-reached=${position <= index ? "true" : null}
          >
            <span class="first-run-step-bar"></span>
            <span class="first-run-step-label">${text.steps[step.id].label}</span>
          </li>
        `,
      )}
    </ol>
  `;
}

// Back, except on the first step, and the button that goes on.
function StepButtons({ onBack, onNext, nextLabel, busy }) {
  return html`
    <div class="first-run-footer">
      ${onBack
        ? html`<button type="button" class="first-run-back" onClick=${onBack}>${text.back}</button>`
        : html`<span></span>`}
      <button
        type="button"
        class="first-run-next"
        aria-disabled=${busy ? "true" : null}
        onClick=${onNext}
      >
        ${nextLabel}
      </button>
    </div>
  `;
}

function nextLabel(step, account) {
  const labels = text.steps[step.id];
  return account && labels.nextSignedIn ? labels.nextSignedIn : labels.next;
}

export function FirstRun() {
  const state = useStore();
  const form = useSettingsSaver();
  const [index, setIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const heading = useRef(null);
  const moved = useRef(false);
  const step = STEPS[index];

  // A new step's heading takes focus, so a screen reader reads it out. Not on the first one.
  useEffect(() => {
    if (moved.current) heading.current.focus();
    moved.current = true;
  }, [index]);

  async function next() {
    if (busy) return;
    setBusy(true);
    const ready = step.beforeNext ? await step.beforeNext(state.settings, form.save) : true;
    setBusy(false);
    // After the last step, the saved first_run_done hands the window to the main layout.
    if (ready && index < STEPS.length - 1) setIndex(index + 1);
  }

  return html`
    <div class="first-run">
      <header class="settings-header">
        <div class="settings-brand">
          <${Logo} />
          <span class="settings-brand-name">${strings.main.appName}</span>
          <span class="settings-brand-caption">${text.setup}</span>
        </div>
      </header>
      <main class="first-run-main">
        <div class="first-run-column">
          <${StepBar} index=${index} />
          <section class="first-run-card" aria-labelledby="first-run-heading">
            <div class="first-run-heading-group">
              <span class="first-run-step-number">${text.stepOf(index + 1, STEPS.length)}</span>
              <h1 id="first-run-heading" class="first-run-heading" tabindex="-1" ref=${heading}>
                ${text.steps[step.id].heading}
              </h1>
            </div>
            <${step.Body} settings=${state.settings} form=${form} />
            <${SaveBanner} message=${form.banner} onDismiss=${form.dismissBanner} />
            <${StepButtons}
              onBack=${index > 0 ? () => setIndex(index - 1) : null}
              onNext=${next}
              nextLabel=${nextLabel(step, state.account)}
              busy=${busy}
            />
          </section>
        </div>
      </main>
    </div>
  `;
}
