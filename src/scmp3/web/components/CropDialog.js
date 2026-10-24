// The crop dialog: a square over the cover that can be moved and resized with the mouse or keys.
import { html } from "htm/preact";
import { useState } from "preact/hooks";

import { strings } from "../strings/index.js";
import { DialogHead, setCover, useModal } from "./CoverDialogs.js";
import { coverCrop } from "./job-view.js";

const text = strings.main;

// The crop stage's largest size on screen, inside the dialog's padding. In a short window the
// stage gives up height, so the dialog's buttons stay in sight.
const STAGE_MAX = { width: 600, height: 420 };
const STAGE_MIN_HEIGHT = 160;
const DIALOG_CHROME_HEIGHT = 340;

function stageHeight() {
  return Math.max(STAGE_MIN_HEIGHT, Math.min(STAGE_MAX.height, innerHeight - DIALOG_CHROME_HEIGHT));
}
// The smallest crop, as a share of the image's shorter side.
const MIN_CROP_SHARE = 0.1;

// A square in the image's own pixels: its left and top edges and its side.
function centeredSquare(size) {
  const side = Math.min(size.width, size.height);
  return { x: (size.width - side) / 2, y: (size.height - side) / 2, side };
}

function squareFromCrop(crop, size) {
  if (!crop) return centeredSquare(size);
  const side = Math.min(crop.width * size.width, crop.height * size.height);
  return { x: crop.left * size.width, y: crop.top * size.height, side };
}

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}

function moved(square, dx, dy, size) {
  return {
    ...square,
    x: clamp(square.x + dx, 0, size.width - square.side),
    y: clamp(square.y + dy, 0, size.height - square.side),
  };
}

// Resizes from one corner while the opposite corner stays put. `corner` holds -1 or 1 per axis.
function resized(square, corner, dx, dy, size) {
  const anchorX = corner.x > 0 ? square.x : square.x + square.side;
  const anchorY = corner.y > 0 ? square.y : square.y + square.side;
  const roomX = corner.x > 0 ? size.width - anchorX : anchorX;
  const roomY = corner.y > 0 ? size.height - anchorY : anchorY;
  const minimum = Math.min(size.width, size.height) * MIN_CROP_SHARE;
  const grown = square.side + (corner.x * dx + corner.y * dy) / 2;
  const side = clamp(grown, minimum, Math.min(roomX, roomY));
  return {
    x: corner.x > 0 ? anchorX : anchorX - side,
    y: corner.y > 0 ? anchorY : anchorY - side,
    side,
  };
}

// Grows or shrinks around the center, staying inside the image.
function scaled(square, change, size) {
  const minimum = Math.min(size.width, size.height) * MIN_CROP_SHARE;
  const side = clamp(square.side + change, minimum, Math.min(size.width, size.height));
  const centerX = square.x + square.side / 2;
  const centerY = square.y + square.side / 2;
  return moved({ x: centerX - side / 2, y: centerY - side / 2, side }, 0, 0, size);
}

// What each key does to the square, with `step` in image pixels.
const CROP_KEYS = {
  ArrowLeft: (square, step, size) => moved(square, -step, 0, size),
  ArrowRight: (square, step, size) => moved(square, step, 0, size),
  ArrowUp: (square, step, size) => moved(square, 0, -step, size),
  ArrowDown: (square, step, size) => moved(square, 0, step, size),
  "+": (square, step, size) => scaled(square, 2 * step, size),
  "=": (square, step, size) => scaled(square, 2 * step, size),
  "-": (square, step, size) => scaled(square, -2 * step, size),
  _: (square, step, size) => scaled(square, -2 * step, size),
};

const CORNERS = [
  { name: "top-left", x: -1, y: -1 },
  { name: "top-right", x: 1, y: -1 },
  { name: "bottom-left", x: -1, y: 1 },
  { name: "bottom-right", x: 1, y: 1 },
];

function toCropBox(square, size) {
  return {
    left: square.x / size.width,
    top: square.y / size.height,
    width: square.side / size.width,
    height: square.side / size.height,
  };
}

// Follows one pointer from pointerdown until it's let go.
function dragWith(event, scale, onDelta) {
  event.preventDefault();
  event.stopPropagation();
  const target = event.currentTarget;
  const start = { x: event.clientX, y: event.clientY };
  target.setPointerCapture(event.pointerId);
  const onMove = (move) => {
    onDelta((move.clientX - start.x) / scale, (move.clientY - start.y) / scale);
  };
  const onEnd = () => {
    target.removeEventListener("pointermove", onMove);
    target.removeEventListener("pointerup", onEnd);
    target.removeEventListener("pointercancel", onEnd);
  };
  target.addEventListener("pointermove", onMove);
  target.addEventListener("pointerup", onEnd);
  target.addEventListener("pointercancel", onEnd);
}

export function CropDialog({ job, size, onClose }) {
  const modal = useModal(onClose);
  const [square, setSquare] = useState(() => squareFromCrop(coverCrop(job), size));
  const scale = Math.min(STAGE_MAX.width / size.width, stageHeight() / size.height);
  const step = Math.max(1, Math.round(Math.min(size.width, size.height) / 100));

  const startMove = (event) => {
    const from = square;
    dragWith(event, scale, (dx, dy) => setSquare(moved(from, dx, dy, size)));
  };
  const startResize = (event, corner) => {
    const from = square;
    dragWith(event, scale, (dx, dy) => setSquare(resized(from, corner, dx, dy, size)));
  };
  const apply = (event) => {
    event.preventDefault();
    onClose();
    const artwork = { kind: job.artwork?.kind ?? "source", value: job.artwork?.value ?? null };
    setCover(job, artwork, toCropBox(square, size));
  };
  const onKeyDown = (event) => {
    if (event.key === "Enter") {
      apply(event);
      return;
    }
    const change = CROP_KEYS[event.key];
    if (!change) return;
    event.preventDefault();
    setSquare(change(square, event.shiftKey ? step * 10 : step, size));
  };
  // Back to the square Python cuts without a crop: centered, or in a letterboxed picture.
  const center = () => setSquare(squareFromCrop(job.default_crop ?? null, size));

  const stage = { width: `${size.width * scale}px`, height: `${size.height * scale}px` };
  const box = {
    left: `${square.x * scale}px`,
    top: `${square.y * scale}px`,
    width: `${square.side * scale}px`,
    height: `${square.side * scale}px`,
  };
  return html`
    <dialog
      ref=${modal.ref}
      class="dialog is-wide"
      aria-labelledby="crop-title"
      onCancel=${modal.onCancel}
    >
      <form class="dialog-body" onSubmit=${apply}>
        <${DialogHead} id="crop-title" title=${text.cropTitle} onClose=${onClose} />
        <p id="crop-help" class="dialog-text">${text.cropHelp}</p>
        <div class="crop-stage" style=${stage}>
          <img class="crop-image" src=${job.cover_url} alt="" draggable="false" />
          <div
            class="crop-box"
            style=${box}
            tabindex="0"
            autofocus
            role="group"
            aria-label=${text.cropArea}
            aria-describedby="crop-help crop-size"
            onPointerDown=${startMove}
            onKeyDown=${onKeyDown}
          >
            ${CORNERS.map(
              (corner) => html`
                <span
                  key=${corner.name}
                  class="crop-handle"
                  data-corner=${corner.name}
                  onPointerDown=${(event) => startResize(event, corner)}
                ></span>
              `,
            )}
          </div>
        </div>
        <p id="crop-size" class="crop-size">${text.cropSize(Math.round(square.side))}</p>
        <div class="dialog-actions">
          <button
            type="button"
            class="control-button is-large is-ghost crop-center"
            onClick=${center}
          >
            ${text.cropReset}
          </button>
          <button type="button" class="control-button is-large is-ghost" onClick=${onClose}>
            ${text.cancel}
          </button>
          <button type="submit" class="control-button is-large is-primary">${text.useCrop}</button>
        </div>
      </form>
    </dialog>
  `;
}
