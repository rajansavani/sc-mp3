// How engine versions read in the UI.

// yt-dlp names its releases like 2026.08.19, and package metadata shortens that to 2026.8.19.
export function engineVersionLabel(version) {
  if (!version) return null;
  const [year, month, day, ...rest] = version.split(".");
  if (!/^\d{4}$/.test(year) || !/^\d+$/.test(month ?? "") || !/^\d+$/.test(day ?? "")) {
    return version;
  }
  return [year, month.padStart(2, "0"), day.padStart(2, "0"), ...rest].join(".");
}
