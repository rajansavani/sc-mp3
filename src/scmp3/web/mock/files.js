// The file dialog and file manager calls, answered with made-up paths.

export function snapshotPart() {
  return {};
}

export function pick_folder() {
  return { path: "~/Music/sc-mp3" };
}

export function pick_audio_files() {
  return { paths: ["~/Music/old-favorite.mp3"] };
}

export function pick_image() {
  return { path: "~/Pictures/cover.png" };
}

// A browser's drops carry their files, so none is ever left for the page to ask about.
export function take_dropped_paths() {
  return { paths: [] };
}

export function reveal(path) {
  console.info(`Mock API: this would show ${path} in the file manager.`);
  return {};
}

export function open_spotify() {
  console.info("Mock API: this would open Spotify.");
  return {};
}

export function open_local_files_folder() {
  console.info("Mock API: this would show Spotify's local-files.bnk in the file manager.");
  return {};
}
