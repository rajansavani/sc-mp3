// The Library section: the folder, the file name, collisions, and artist and album folders.
import { html } from "htm/preact";

import { strings } from "../../strings/index.js";
import { FolderField, Mono, Selects, Switches, TextSetting, bind } from "./fields.js";

const text = strings.settings;

export function LibrarySection({ settings, form, preview }) {
  const error = form.errors.filename_template;
  // With no template of its own, the field shows the title style's default.
  const template = settings.filename_template ?? preview?.filename_template ?? "";
  const hint =
    preview && !error
      ? html`${text.previewLabel} <${Mono}>${preview.filename}<//> · ${text.fileNameTokens}`
      : text.fileNameTokens;
  return html`
    <${FolderField}
      label=${text.labels.library_dir}
      buttonLabel=${text.change}
      folder=${settings.library_dir}
      error=${form.errors.library_dir}
      save=${form.save}
    />
    <${TextSetting}
      ...${bind(form, "filename_template")}
      shown=${template}
      toValue=${(typed) => (typed.trim() ? typed : null)}
      hint=${hint}
      mono
    />
    <div class="settings-grid settings-grid-wide">
      <${Selects} names=${["when_file_exists"]} settings=${settings} form=${form} />
    </div>
    <${Switches}
      names=${["artist_folders", "album_subfolders"]}
      settings=${settings}
      form=${form}
    />
  `;
}
