// What the bridge adds to a set it sends, as app/job_views.py and app/set_tracks.py work it out:
// the set's state from its tracks, the album's fields, and each track's number in the album.

const FINAL_ERRORS = new Set(["drm", "preview_only", "unavailable", "geo_blocked"]);
const ALBUM_TYPES = new Set(["album", "ep", "single"]);
const WAITING = new Set(["queued", "resolving", "needs_review", "error"]);

export function canNeverSave(track) {
  return track.state === "error" && FINAL_ERRORS.has(track.error?.kind);
}

// A track gets a number when it's included and its info is in.
function isNumbered(track) {
  return track.included && Boolean(track.suggestion);
}

export function defaultMode(set) {
  const playlist = set.source?.playlist;
  if (!playlist) return null;
  if (playlist.set_type) return ALBUM_TYPES.has(playlist.set_type) ? "album" : "singles";
  return playlist.is_album ? "album" : "singles";
}

export function albumMode(set) {
  return set.album_mode ?? defaultMode(set) ?? "singles";
}

// The album's fields: the set's title and owner, and its year and genre or else its first
// track's, under the set's edits.
export function albumFields(set) {
  const playlist = set.source?.playlist ?? {};
  const first = set.children.find(isNumbered)?.suggestion;
  const fields = { album: playlist.title ?? "", album_artist: playlist.owner ?? "" };
  const year = playlist.release_date?.slice(0, 4) ?? first?.year;
  const genre = playlist.genre ?? first?.genre;
  if (year) fields.year = String(year);
  if (genre) fields.genre = genre;
  return { ...fields, ...set.overrides };
}

// The album fields as the set's editor shows them.
export function albumTags(set) {
  const fields = albumFields(set);
  const year = (fields.year ?? "").trim();
  return {
    album: fields.album ?? "",
    album_artist: fields.album_artist ?? "",
    year: /^\d+$/.test(year) ? Number(year) : null,
    genre: fields.genre || null,
  };
}

// Whether a track counts toward the album's numbers: included, and resolved or still able to be.
function takesANumber(track) {
  return track.included && !canNeverSave(track);
}

// Each track's place in the album: its fields, number and the total, or null.
export function trackPlaces(set) {
  if (albumMode(set) !== "album") return set.children.map(() => null);
  const total = set.children.filter(takesANumber).length;
  const fields = albumFields(set);
  let count = 0;
  return set.children.map((track) => {
    if (!takesANumber(track)) return null;
    count += 1;
    return isNumbered(track) ? { fields, track_no: count, track_total: total } : null;
  });
}

// A track as it's tagged at its place: the album's fields under its own edits, and its number.
export function placedTrack(track, place) {
  if (!place) return track;
  const suggestion = { ...track.suggestion, track_no: place.track_no, track_total: place.track_total };
  return { ...track, suggestion, overrides: { ...place.fields, ...track.overrides } };
}

export function setState(set) {
  if (!set.source || set.state === "skipped") return set.state;
  const active = set.children.filter((track) => track.included && !canNeverSave(track));
  const states = new Set(active.map((track) => track.state));
  if (states.has("saving") || active.some((track) => track.saves_once_in)) return "saving";
  if ([...states].some((state) => WAITING.has(state))) return "needs_review";
  if (set.children.some((track) => track.state === "saved")) return "saved";
  return active.length > 0 ? "skipped" : "needs_review";
}

export function albumSettled(set) {
  return set.children.some((track) => track.state === "saved");
}
