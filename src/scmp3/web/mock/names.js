// File and folder names made safe the way core/filenames.py does, and artist names cleaned the
// way app/job_artists.py does, closely enough for the mock.

export function safeName(name) {
  return name
    .replace(/[\\/:*?"<>|]/g, "-")
    .replace(/\s+/g, " ")
    .replace(/^[ .-]+|[ .-]+$/g, "");
}

// Artist folders are capped at 100 characters, as in core/library.py.
export function artistFolder(albumArtist) {
  const capped = safeName(albumArtist).slice(0, 100);
  // The cut can leave a space or a dot at the end, so it's trimmed again.
  return safeName(capped) || "Unknown artist";
}

// Spaces and invisible format characters at a name's edges, as Python strips them. Tag letters,
// as in flags, stay.
const EDGES = /^(?:(?![\u{E0020}-\u{E007F}])[\s\p{Cf}])+|(?:(?![\u{E0020}-\u{E007F}])[\s\p{Cf}])+$/gu;

// Names the publisher's field never splits, a stand-in for wordlists.toml's [artists] whole_names.
const WHOLE_NAMES = ["Tyler, The Creator"];

function cleanedNames(names, seen) {
  const kept = [];
  for (const name of names) {
    const cleaned = name.replace(EDGES, "");
    const key = cleaned.toLowerCase();
    if (!cleaned || seen.has(key)) continue;
    seen.add(key);
    kept.push(cleaned);
  }
  return kept;
}

// An artist list as Python keeps it: names cleaned, and each credited once.
export function cleanedArtists(main, featured) {
  const seen = new Set();
  return { main: cleanedNames(main, seen), featured: cleanedNames(featured, seen) };
}

// Pasted names, split on commas and line breaks, with whole names kept whole.
export function pastedNames(text) {
  let held = text.replace(/\r?\n/g, ", ");
  WHOLE_NAMES.forEach((name, index) => {
    held = held.split(name).join(`\u0001${index}\u0001`);
  });
  const parts = held.split(",").map((part) => {
    return part.replace(/\u0001(\d+)\u0001/g, (_, index) => WHOLE_NAMES[Number(index)]);
  });
  return cleanedNames(parts, new Set());
}

// Featured artists with no main one, in a list set by hand, hold the save.
export function needsMainArtist(job) {
  const artists = job.artists;
  if (!artists || "artist" in job.overrides) return false;
  return artists.main.length === 0 && artists.featured.length > 0;
}
