// The Title style section: the style, custom templates and the cleanup choices.
import { html } from "htm/preact";

import { strings } from "../../strings/index.js";
import { CHOICES, Selects, TextSetting, bind, useRadioGroup } from "./fields.js";

const text = strings.settings;

// The title styles as a radio group that looks like the design's segmented control.
function StylePicker({ settings, form }) {
  const styles = CHOICES.title_style;
  const pick = (style) => form.save({ title_style: style });
  const { groupProps, choiceProps } = useRadioGroup(styles, settings.title_style, pick);
  return html`
    <div class="settings-field">
      <span id="settings-style-label" class="settings-label">${text.labels.title_style}</span>
      <div class="settings-segmented" aria-labelledby="settings-style-label" ...${groupProps}>
        ${styles.map(
          (style, index) => html`
            <button
              key=${style}
              type="button"
              class="settings-segment"
              ...${choiceProps(style, index)}
            >
              ${text.choices.title_style[style]}
            </button>
          `,
        )}
      </div>
    </div>
  `;
}

function CustomTemplates({ settings, form }) {
  return html`
    <div class="settings-grid settings-grid-wide">
      ${["custom_title_template", "custom_artist_template"].map(
        (setting) => html`
          <${TextSetting}
            key=${setting}
            ...${bind(form, setting)}
            shown=${settings[setting]}
            toValue=${(typed) => typed}
            describedById="settings-title-preview"
            mono
          />
        `,
      )}
    </div>
  `;
}

export function TitleSection({ settings, form, preview }) {
  const isCustom = settings.title_style === "custom";
  const templateError = form.errors.custom_title_template || form.errors.custom_artist_template;
  // The token list for custom templates, then the preview while the templates are usable.
  const hint = [
    isCustom && text.templateTokens,
    preview && !templateError && text.titlePreview(preview.title, preview.artist),
  ];
  const selects = [
    "artist_separator",
    "feat_wording",
    "remove_producer_credits",
    "remove_promo_text",
    "singles_album",
  ];
  const fixedAlbum = settings.singles_album === "fixed";
  return html`
    <${StylePicker} settings=${settings} form=${form} />
    ${isCustom && html`<${CustomTemplates} settings=${settings} form=${form} />`}
    <span id="settings-title-preview" class="settings-hint">
      ${hint.filter(Boolean).join(" ")}
    </span>
    <div class="settings-grid">
      <${Selects} names=${selects} settings=${settings} form=${form} />
      ${fixedAlbum &&
      html`
        <${TextSetting}
          ...${bind(form, "singles_album_text")}
          shown=${settings.singles_album_text}
          toValue=${(typed) => typed}
        />
      `}
    </div>
    <${TextSetting}
      ...${bind(form, "custom_words")}
      shown=${settings.custom_words.join("\n")}
      toValue=${(typed) =>
        typed
          .split("\n")
          .map((word) => word.trim())
          .filter(Boolean)}
      multiline
      rows="2"
      placeholder=${text.customWordsPlaceholder}
    />
  `;
}
