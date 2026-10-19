// The Replace dialog, from a file, a link or a pasted or dropped image, and what dialogs share.
import { html } from "htm/preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { api } from "../api.js";
import { addToast, reportError } from "../store.js";
import { strings } from "../strings/index.js";
import { Icon } from "./icons.js";
import { pasteKeys } from "./inputs.js";

const text = strings.main;

// Bigger images make a slow trip across the bridge for no visible gain.
const MAX_IMAGE_MB = 20;

function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

// An image file's contents as a data: URL, or the reason it can't be used.
async function imageData(file) {
  if (!file.type.startsWith("image/")) return { problem: text.notAnImage };
  if (file.size > MAX_IMAGE_MB * 1024 * 1024) return { problem: text.imageTooBig(MAX_IMAGE_MB) };
  try {
    return { value: await readAsDataUrl(file) };
  } catch (error) {
    console.warn("An image couldn't be read", error);
    return { problem: text.imageUnreadable };
  }
}

// Replaces the cover. A new image starts with the centered square, so no crop goes with it.
export async function setCover(job, artwork, crop = null) {
  const result = await api.set_artwork(job.id, artwork, crop);
  reportError(result);
}

export async function applyImageFile(job, file) {
  const image = await imageData(file);
  if (image.problem) {
    addToast("error", image.problem);
    return;
  }
  await setCover(job, { kind: "data_url", value: image.value });
}

// A native modal dialog: it keeps focus inside, makes the page inert and closes on Esc.
export function useModal(onClose) {
  const ref = useRef(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog.showModal();
    return () => dialog.open && dialog.close();
  }, []);
  const onCancel = (event) => {
    event.preventDefault();
    onClose();
  };
  return { ref, onCancel };
}

export function DialogHead({ id, title, onClose }) {
  return html`
    <div class="dialog-head">
      <h2 id=${id} class="dialog-title">${title}</h2>
      <button
        type="button"
        class="control-button is-icon"
        aria-label=${text.close}
        onClick=${onClose}
      >
        <${Icon} name="close" size=${18} />
      </button>
    </div>
  `;
}

export function ReplaceCoverDialog({ job, onClose }) {
  const modal = useModal(onClose);
  const [link, setLink] = useState("");
  const [problem, setProblem] = useState(null);

  async function chooseFile() {
    const result = await api.pick_image();
    if (result?.error) {
      setProblem(strings.shell.errors[result.error] ?? strings.shell.unknownError(result.error));
      return;
    }
    // No path means the picker was cancelled.
    if (!result?.path) return;
    onClose();
    setCover(job, { kind: "file", value: result.path });
  }

  function submitLink(event) {
    event.preventDefault();
    const value = link.trim();
    if (!/^https:\/\/\S+$/i.test(value)) {
      setProblem(text.badImageLink);
      return;
    }
    onClose();
    setCover(job, { kind: "url", value });
  }

  function useOriginal() {
    onClose();
    setCover(job, { kind: "source" });
  }

  // An image dropped anywhere on the dialog counts too.
  const onDragOver = (event) => event.preventDefault();
  const onDrop = (event) => {
    event.preventDefault();
    event.stopPropagation();
    const [file] = event.dataTransfer.files;
    if (!file) return;
    onClose();
    applyImageFile(job, file);
  };

  return html`
    <dialog
      ref=${modal.ref}
      class="dialog"
      aria-labelledby="replace-cover-title"
      onCancel=${modal.onCancel}
      onDragOver=${onDragOver}
      onDrop=${onDrop}
    >
      <div class="dialog-body">
        <${DialogHead} id="replace-cover-title" title=${text.replaceTitle} onClose=${onClose} />
        <div class="replace-sources">
          <button type="button" class="control-button" autofocus onClick=${chooseFile}>
            <${Icon} name="image" />
            <span>${text.chooseImage}</span>
          </button>
          ${job.artwork?.kind !== "source" &&
          html`
            <button type="button" class="control-button is-ghost" onClick=${useOriginal}>
              ${text.useOriginalCover}
            </button>
          `}
        </div>
        <form class="replace-link" novalidate onSubmit=${submitLink}>
          <label for="replace-link-input" class="replace-link-label">${text.imageLink}</label>
          <div class="replace-link-row">
            <input
              id="replace-link-input"
              class="text-field"
              type="url"
              inputmode="url"
              placeholder=${text.imageLinkPlaceholder}
              value=${link}
              aria-invalid=${problem === text.badImageLink ? "true" : undefined}
              aria-describedby=${problem ? "replace-problem" : undefined}
              onInput=${(event) => setLink(event.currentTarget.value)}
            />
            <button type="submit" class="control-button">${text.useLink}</button>
          </div>
        </form>
        ${problem && html`<p id="replace-problem" class="field-error" role="alert">${problem}</p>`}
        <p class="dialog-text replace-hint">${text.replaceHint(pasteKeys())}</p>
      </div>
    </dialog>
  `;
}
