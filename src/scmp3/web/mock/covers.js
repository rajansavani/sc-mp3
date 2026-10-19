// The mock's covers: the URL the page shows for each job's cover, and the size in pixels that
// the bridge would crop and embed from. Like the bridge, it sends an image added by hand as a
// small preview, with its full size in cover_size.
import { artSize } from "./sample-art.js";

// The longest side of the preview sent for an image added by hand.
const PREVIEW_PX = 256;

// Images added by hand, by job id: the preview the page shows, and the image's own size.
const addedCovers = new Map();
// Sizes learned by loading a linked image, by URL.
const measured = new Map();

function previewOf(image, size) {
  const scale = Math.min(1, PREVIEW_PX / Math.max(size.width, size.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(size.width * scale));
  canvas.height = Math.max(1, Math.round(size.height * scale));
  canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.85);
}

// A canvas a picture can't be read back from leaves the full image as its own preview.
function previewFor(image, size, url) {
  try {
    return previewOf(image, size);
  } catch (error) {
    console.info("Mock API: a cover couldn't be scaled down, so it's sent whole.", error);
    return url;
  }
}

// Loads an image, then calls back with it and its size. A failure is logged and left at that.
function load(url, onLoaded) {
  const image = new Image();
  image.onload = () => onLoaded(image, { width: image.naturalWidth, height: image.naturalHeight });
  image.onerror = () => {
    console.info("Mock API: a cover couldn't be loaded, so its size is unknown.");
  };
  image.src = url;
}

// Takes a pasted, dropped or picked image as the job's cover, and calls back once its preview
// is ready.
export function addCover(jobId, url, onReady) {
  addedCovers.delete(jobId);
  load(url, (image, size) => {
    addedCovers.set(jobId, { url: previewFor(image, size, url), size });
    onReady();
  });
}

export function coverUrl(job) {
  const artwork = job.artwork ?? { kind: "source" };
  if (artwork.kind === "url") return artwork.value;
  if (artwork.kind === "file") return addedCovers.get(job.id)?.url ?? null;
  if (artwork.kind === "none") return null;
  return job.source?.artwork_url ?? job.source?.playlist?.artwork_url ?? null;
}

export function coverSize(job) {
  if (job.artwork?.kind === "file") return addedCovers.get(job.id)?.size ?? null;
  const url = coverUrl(job);
  if (!url) return null;
  return artSize(url) ?? measured.get(url) ?? null;
}

// Loads a linked image to learn its size, then calls back.
export function measureCover(url, onMeasured) {
  if (!url || artSize(url) || measured.has(url)) return;
  load(url, (image, size) => {
    measured.set(url, size);
    onMeasured();
  });
}
