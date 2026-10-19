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

export function reveal(path) {
  console.info(`Mock API: this would show ${path} in the file manager.`);
  return {};
}

export function open_spotify() {
  console.info("Mock API: this would open Spotify.");
  return {};
}
