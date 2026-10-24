// The fields and controls that the options menu and first-run setup share.
import { html } from "htm/preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { api } from "../../api.js";
import { reportError } from "../../store.js";
import { strings } from "../../strings/index.js";
import { folderLabel } from "../paths.js";

const text = strings.settings;

// How long typing has to pause before a text setting saves, so previews follow without a save
// for every key.
const TYPING_SAVE_MS = 300;

// The values each choice offers, in menu order. Their labels are in strings/settings.js.
export const CHOICES = {
  when_file_exists: ["ask", "keep_both", "replace", "skip"],
  title_style: ["artist_field", "feat_in_title", "all_in_title", "custom"],
  artist_separator: [", ", " & ", " x "],
  feat_wording: ["feat.", "ft.", "featuring", "with"],
  remove_producer_credits: [true, false],
  remove_promo_text: [true, false],
  singles_album: ["title", "fixed", "blank"],
  audio_format: ["mp3_320", "mp3_v0", "m4a"],
  cover_max_size: [300, 640, 1000, 1400],
  comment_mode: ["source_url", "description", "none"],
  concurrent_downloads: [1, 2, 3, 4],
  engine_channel: ["stable", "nightly"],
};

export function fieldId(setting) {
  return `setting-${setting.replaceAll("_", "-")}`;
}

// What went wrong with one setting, from the problem update_settings sent for it. A folder in
// the problem is named the way the folder field shows it.
export function problemText(problem) {
  const describe = text.fieldErrors[problem.reason] ?? text.fieldErrors.INVALID_VALUE;
  const folder = problem.folder ? folderLabel(problem.folder) : text.thisFolder;
  return describe({ ...problem, folder });
}

// Arrow keys move the choice in a radio group, which is one tab stop.
const ARROW_STEPS = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };

// A group of radio buttons that saves the choice as it moves: the checked one takes Tab, or the
// first when none is checked, and the arrow keys move and pick. Returns the props for the group
// and for each choice.
export function useRadioGroup(values, checked, pick) {
  const group = useRef(null);

  function onKeyDown(event) {
    const step = ARROW_STEPS[event.key];
    if (!step) return;
    event.preventDefault();
    const index = Number(event.currentTarget.dataset.choice);
    const next = (index + step + values.length) % values.length;
    group.current.querySelector(`[data-choice="${next}"]`).focus();
    pick(values[next]);
  }

  const tabStop = values.includes(checked) ? checked : values[0];
  const choiceProps = (value, index) => ({
    role: "radio",
    "aria-checked": String(value === checked),
    tabindex: value === tabStop ? "0" : "-1",
    "data-choice": String(index),
    onClick: () => pick(value),
    onKeyDown,
  });
  return { groupProps: { role: "radiogroup", ref: group }, choiceProps };
}

// Asks for a folder. Resolves to its path, or null when the dialog was cancelled or failed.
async function pickFolder() {
  const result = await api.pick_folder();
  if (reportError(result)) return null;
  return result.path || null;
}

// The ids of a field's error and hint, and of a line elsewhere that describes it too, for
// aria-describedby. Null when there are none.
function describedBy(id, error, hint, also = null) {
  const ids = [error && `${id}-error`, hint && `${id}-hint`, also].filter(Boolean);
  return ids.length > 0 ? ids.join(" ") : null;
}

// The lines under a field: what's wrong with it, if anything, then its hint.
function FieldNotes({ id, error, hint }) {
  return html`
    ${error &&
    html`<span id=${`${id}-error`} class="field-error">${problemText(error)}</span>`}
    ${hint && html`<span id=${`${id}-hint`} class="settings-hint">${hint}</span>`}
  `;
}

// The library folder by its short name, with a button that asks for another. The field always
// shows the saved folder, the one in use, and a refused folder is named in the message under it.
export function FolderField({ label, buttonLabel, folder, error, hint, save }) {
  const id = fieldId("library_dir");

  async function pick() {
    const path = await pickFolder();
    if (path) await save({ library_dir: path });
  }

  return html`
    <div class="field">
      <label class="field-label" for=${id}>${label}</label>
      <div class="settings-folder-picker">
        <input
          id=${id}
          class="text-field settings-mono"
          type="text"
          readonly
          value=${folderLabel(folder)}
          title=${folder}
          aria-invalid=${error ? "true" : null}
          aria-describedby=${describedBy(id, error, hint)}
        />
        <button type="button" class="control-button is-raised" onClick=${pick}>
          ${buttonLabel}
        </button>
      </div>
      <${FieldNotes} id=${id} error=${error} hint=${hint} />
    </div>
  `;
}

// A text setting that saves after a pause in typing and when it loses focus. While it's being
// edited, it shows what was typed; afterwards, what was saved.
export function TextSetting(props) {
  const { setting, shown, toValue, save, error, hint, describedById, multiline, mono, ...rest } =
    props;
  const id = fieldId(setting);
  const [draft, setDraft] = useState(null);
  const timer = useRef(null);
  // Typed text that hasn't been sent yet, and the last save.
  const unsent = useRef(null);
  const lastSave = useRef(Promise.resolve({ saved: true }));
  const focused = useRef(false);

  function send() {
    clearTimeout(timer.current);
    if (unsent.current === null) return lastSave.current;
    const value = toValue(unsent.current);
    unsent.current = null;
    lastSave.current = save({ [setting]: value });
    return lastSave.current;
  }

  // Leaving the view saves whatever was typed in the last moment.
  useEffect(() => () => send(), []);

  function onInput(event) {
    const typed = event.currentTarget.value;
    setDraft(typed);
    unsent.current = typed;
    clearTimeout(timer.current);
    timer.current = setTimeout(send, TYPING_SAVE_MS);
  }

  async function onBlur() {
    focused.current = false;
    const { saved } = await send();
    // A refused value stays, so it can be fixed next to its message.
    if (saved && !focused.current) setDraft(null);
  }

  const fieldProps = {
    id,
    class: mono ? "text-field settings-mono" : "text-field",
    value: draft ?? shown,
    onInput,
    onFocus: () => {
      focused.current = true;
    },
    onBlur,
    "aria-invalid": error ? "true" : null,
    "aria-describedby": describedBy(id, error, hint, describedById),
    spellcheck: false,
    ...rest,
  };
  return html`
    <div class="field">
      <label class="field-label" for=${id}>${text.labels[setting]}</label>
      ${multiline
        ? html`<textarea ...${fieldProps} />`
        : html`<input type="text" ...${fieldProps} />`}
      <${FieldNotes} id=${id} error=${error} hint=${hint} />
    </div>
  `;
}

// A choice setting as a select. Options are indexes, since some values aren't text.
export function SelectSetting({ setting, value, save, error, inline = false }) {
  const id = fieldId(setting);
  const values = CHOICES[setting];
  const labels = text.choices[setting];
  const hint = text.hints[setting];
  const select = html`
    <select
      id=${id}
      class="text-field"
      value=${String(values.indexOf(value))}
      onChange=${(event) => save({ [setting]: values[Number(event.currentTarget.value)] })}
      aria-invalid=${error ? "true" : null}
      aria-describedby=${describedBy(id, error, hint)}
    >
      ${values.map(
        (choice, index) => html`<option value=${String(index)}>${labels[String(choice)]}</option>`,
      )}
    </select>
  `;
  if (inline) {
    return html`
      <div class="settings-row">
        <label for=${id}>${text.labels[setting]}</label>
        ${select}
      </div>
    `;
  }
  return html`
    <div class="field">
      <label class="field-label" for=${id}>${text.labels[setting]}</label>
      ${select}
      <${FieldNotes} id=${id} error=${error} hint=${hint} />
    </div>
  `;
}

// An on or off setting as a switch, with its label and hint.
export function SwitchSetting({ setting, checked, save }) {
  const id = fieldId(setting);
  const { label, hint } = text.switches[setting];
  return html`
    <div class="settings-row">
      <span class="settings-row-text">
        <span id=${`${id}-label`}>${label}</span>
        ${hint && html`<span id=${`${id}-hint`} class="settings-hint">${hint}</span>`}
      </span>
      <button
        type="button"
        role="switch"
        class="switch"
        aria-checked=${String(checked)}
        aria-labelledby=${`${id}-label`}
        aria-describedby=${hint ? `${id}-hint` : null}
        onClick=${() => save({ [setting]: !checked })}
      >
        <span class="switch-knob"></span>
      </button>
    </div>
  `;
}

// The props every field takes: its setting, how to save it, and its problem if it has one.
export function bind(form, setting) {
  return { setting, save: form.save, error: form.errors[setting] };
}

export function Mono({ children }) {
  return html`<span class="settings-hint-value">${children}</span>`;
}

export function Selects({ names, settings, form }) {
  return names.map(
    (setting) => html`
      <${SelectSetting} key=${setting} ...${bind(form, setting)} value=${settings[setting]} />
    `,
  );
}

export function Switches({ names, settings, form }) {
  return names.map(
    (setting) => html`
      <${SwitchSetting} key=${setting} ...${bind(form, setting)} checked=${settings[setting]} />
    `,
  );
}
