// The cover: a preview, Replace and Crop, the warnings for small and avatar covers, and a caption.
import { html } from "htm/preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { api } from "../api.js";
import { reportError } from "../store.js";
import { strings } from "../strings/index.js";
import { applyDroppedPath, applyImageFile, ReplaceCoverDialog, setCover } from "./CoverDialogs.js";
import { CropDialog } from "./CropDialog.js";
import { Icon } from "./icons.js";
import { coverCrop, coverSide, cropStyle, jobTitle, sourceKind } from "./job-view.js";

const text = strings.main;

// Spotify shows covers soft below this size.
const SMALL_COVER_PX = 500;

// An image dragged out of a browser arrives as its link. Only https covers can be shown.
function droppedImageLink(dataTransfer) {
  const [firstLink] = dataTransfer.getData("text/uri-list").split(/\r?\n/);
  const link = (firstLink || dataTransfer.getData("text/plain")).trim();
  return /^https:\/\//i.test(link) ? link : null;
}

// Drop handlers for the preview, which takes an image file or an image dragged from a browser.
function useImageDrop(job, editable) {
  const [over, setOver] = useState(false);
  const accept = (event) => {
    const types = [...event.dataTransfer.types];
    if (!editable || (!types.includes("Files") && !types.includes("text/uri-list"))) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setOver(true);
  };
  const handlers = {
    // WebKit refuses a drop whose last move entered a new element, unless dragenter took it too.
    onDragEnter: accept,
    onDragOver: accept,
    onDragLeave: (event) => {
      // Moving onto the image inside fires dragleave too.
      if (!event.currentTarget.contains(event.relatedTarget)) setOver(false);
    },
    onDrop: (event) => {
      setOver(false);
      if (!editable) return;
      event.preventDefault();
      // The drop is the cover's, so the queue's drop zones and the window guard don't see it.
      event.stopPropagation();
      const [file] = event.dataTransfer.files;
      if (file) {
        applyImageFile(job, file);
        return;
      }
      const link = droppedImageLink(event.dataTransfer);
      if (link) {
        setCover(job, { kind: "url", value: link });
        return;
      }
      applyDroppedPath(job, event.dataTransfer);
    },
  };
  return { over, handlers };
}

// Whether a paste is meant for the cover: Replace is open, or focus is on the cover's own
// controls. The crop dialog sits inside the panel too, but a paste there isn't a new cover.
function pasteIsForCover(panel, replaceOpen) {
  if (replaceOpen) return true;
  const focus = document.activeElement;
  return Boolean(panel?.contains(focus)) && !focus.closest("dialog");
}

// A pasted image becomes the cover while Replace is open or the cover has focus.
function useImagePaste(job, panel, editable, replaceOpen, onPasted) {
  const current = useRef({ job, editable, replaceOpen, onPasted });
  current.current = { job, editable, replaceOpen, onPasted };
  useEffect(() => {
    function onPaste(event) {
      if (!current.current.editable) return;
      if (!pasteIsForCover(panel.current, current.current.replaceOpen)) return;
      const files = [...(event.clipboardData?.files ?? [])];
      const file = files.find((item) => item.type.startsWith("image/"));
      if (!file) return;
      event.preventDefault();
      current.current.onPasted();
      applyImageFile(current.current.job, file);
    }
    // Capture runs this before the top bar's handler, which skips pastes already taken.
    document.addEventListener("paste", onPaste, true);
    return () => document.removeEventListener("paste", onPaste, true);
  }, [panel]);
}

// Whether the cover image loaded, kept with its URL so a new cover starts fresh. Its size
// comes from cover_size, since the image may be a small preview.
function useCoverImage(url) {
  const [known, setKnown] = useState({ url: null, loaded: false, failed: false });
  const current = known.url === url ? known : { url, loaded: false, failed: false };
  return {
    loaded: current.loaded,
    failed: current.failed,
    onLoad: () => setKnown({ url, loaded: true, failed: false }),
    onError: () => setKnown({ url, loaded: false, failed: true }),
  };
}

// A re-tagged file's own cover, not cropped, which the save leaves as it is.
function keepsFileCover(job) {
  return job.kind === "retag" && job.artwork?.kind === "source" && !job.artwork.crop;
}

// What the preview says when there's no image to show.
function missingText(job, image) {
  // Python couldn't read the file's cover for a preview, but the save leaves it as it is.
  if (keepsFileCover(job) && (job.cover_unseen || image.failed)) return text.coverKeptUnseen;
  return image.failed ? text.coverFailed : text.noCover;
}

function Preview({ job, image, editable }) {
  const drop = useImageDrop(job, editable);
  const crop = coverCrop(job);
  const showImage = job.cover_url && !image.failed;
  return html`
    <div
      class="cover-preview"
      data-drop-over=${drop.over ? "true" : undefined}
      title=${drop.over ? text.dropCover : undefined}
      ...${drop.handlers}
    >
      ${showImage
        ? html`
            <img
              class=${crop ? "cover-image is-cropped" : "cover-image"}
              src=${job.cover_url}
              alt=${text.coverAlt(jobTitle(job))}
              style=${cropStyle(crop)}
              onLoad=${image.onLoad}
              onError=${image.onError}
            />
          `
        : html`
            <span class="cover-missing">
              <${Icon} name="image" size=${28} />
              <span>${missingText(job, image)}</span>
            </span>
          `}
    </div>
  `;
}

// Why the last cover picked couldn't be used, as Python reports it with the job. Choosing another
// cover clears it, and so does its close button.
function CoverProblem({ job }) {
  const message = text.coverProblems[job.cover_problem] ?? text.coverProblems.internal;
  const dismiss = async () => reportError(await api.dismiss_cover_problem(job.id));
  return html`
    <div class="note" data-tone="danger" role="status">
      <${Icon} name="error" className="tone-icon" />
      <span class="note-text">${message}</span>
      <button
        type="button"
        class="note-dismiss"
        aria-label=${strings.shell.dismiss}
        onClick=${dismiss}
      >
        <${Icon} name="close" size=${16} />
      </button>
    </div>
  `;
}

function Notes({ job, side }) {
  const avatar = job.source?.artwork_is_avatar && job.artwork?.kind === "source";
  const small = side != null && side < SMALL_COVER_PX;
  return html`
    ${job.cover_problem && html`<${CoverProblem} job=${job} />`}
    ${small &&
    html`
      <div class="note" data-tone="warn">
        <${Icon} name="warning" className="tone-icon" />
        <span>${text.smallCover(side, side)}</span>
      </div>
    `}
    ${avatar &&
    html`
      <div class="note" data-tone="warn">
        <${Icon} name="person" className="tone-icon" />
        <span>${text.avatarCover(job.source.uploader)}</span>
      </div>
    `}
  `;
}

function coverFrom(job) {
  const kind = job.artwork?.kind ?? "source";
  if (kind === "url") return text.coverFrom.url;
  if (kind === "file") return text.coverFrom.added;
  return text.coverFrom[sourceKind(job)];
}

// Where the cover comes from and the size it's embedded at. A re-tagged file's own cover, not
// cropped, isn't processed at all.
function caption(job, embedded) {
  if (keepsFileCover(job) && job.cover_url) return text.coverKept(coverFrom(job));
  return text.coverCaption(coverFrom(job), embedded);
}

export function CoverPanel({ job, settings, editable = true }) {
  const panel = useRef(null);
  const image = useCoverImage(job.cover_url);
  const [dialog, setDialog] = useState(null);
  const close = () => setDialog(null);
  useImagePaste(job, panel, editable, dialog === "replace", close);

  const side = coverSide(job);
  const embedded = side ? Math.min(side, settings?.cover_max_size ?? side) : null;
  const canCrop = image.loaded && Boolean(job.cover_size);
  return html`
    <div class="cover-panel" ref=${panel}>
      <${Preview} job=${job} image=${image} editable=${editable} />
      <${Notes} job=${job} side=${side} />
      <div class="cover-buttons">
        <button
          type="button"
          class="control-button cover-button"
          onClick=${() => setDialog("replace")}
        >
          <${Icon} name="image" />
          <span>${text.replace}</span>
        </button>
        <button
          type="button"
          class="control-button cover-button"
          disabled=${!canCrop}
          onClick=${() => setDialog("crop")}
        >
          <${Icon} name="crop" />
          <span>${text.crop}</span>
        </button>
      </div>
      <span class="cover-caption">${caption(job, embedded)}</span>
      ${dialog === "replace" && html`<${ReplaceCoverDialog} job=${job} onClose=${close} />`}
      ${dialog === "crop" &&
      canCrop &&
      html`<${CropDialog} job=${job} size=${job.cover_size} onClose=${close} />`}
    </div>
  `;
}
