// How file and folder paths read in the UI, for the main window and the settings alike.

// How many leading parts of a path are the home folder: ~, C:\Users\<name>, /Users/<name> or
// /home/<name>. None when the path is somewhere else.
function homeLength(parts) {
  if (parts[0] === "~") return 1;
  if (/^[A-Za-z]:$/.test(parts[0]) && parts[1]?.toLowerCase() === "users") return 3;
  if (parts[0] === "Users" || parts[0] === "home") return 2;
  return 0;
}

// A folder in the home folder reads from there, as "Music › sc-mp3". Others show in full.
export function folderLabel(path) {
  const parts = path.split(/[\\/]+/).filter(Boolean);
  const start = homeLength(parts);
  if (start === 0 || start >= parts.length) return path;
  return parts.slice(start).join(" › ");
}

// The last part of a path, such as "old-favorite.mp3".
export function baseName(path) {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? path;
}

export function folderOf(path) {
  return path.replace(/[\\/][^\\/]*$/, "");
}
