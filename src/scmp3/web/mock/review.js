// What the bridge adds to each job it sends: the rendered tags, the file name, the format, and
// the cover with its size. This follows core/suggest closely enough for the sample data.
import { coverSize, coverUrl } from "./covers.js";
import { artistFolder, needsMainArtist } from "./names.js";
import {
  albumSettled,
  albumTags,
  defaultMode,
  placedTrack,
  setState,
  trackPlaces,
} from "./sets.js";
import { snapshotPart as settingsPart } from "./settings.js";

// Characters that can't go in a file name on Windows.
const UNSAFE_IN_NAMES = /[\\/:*?"<>|]/g;
// Changes whose text sat in front of the title, as in "Juno Vale - Static Bloom".
const LEADING_KINDS = new Set(["artist_from_title", "strip_redundant_artist"]);
const TRACK = /^(\d+)(?:\s*\/\s*(\d+))?$/;

// A change that's off puts its text back, the way apply_changes does. An artist list set by
// hand stays as it is.
function applied(job) {
  const { suggestion, source, artists } = job;
  let title = suggestion.base_title;
  let main = suggestion.main_artists;
  let featured = suggestion.featured;
  for (const change of suggestion.changes.filter((candidate) => !candidate.enabled)) {
    if (change.kind === "extract_feature") featured = [];
    if (change.kind === "artist_from_title") main = [source.uploader];
    const leading = LEADING_KINDS.has(change.kind);
    title = leading ? `${change.span} - ${title}` : `${title} ${change.span}`;
  }
  if (artists) ({ main, featured } = artists);
  return { ...suggestion, base_title: title.trim(), main_artists: main, featured };
}

// The suggestion as the bridge sends it, crediting the artists the tags are rendered with.
function sentSuggestion(job) {
  if (!job.source || !job.suggestion) return job.suggestion;
  const { main_artists, featured } = applied(job);
  return { ...job.suggestion, main_artists, featured };
}

function withVersion(title, suggestion) {
  return `${title}${suggestion.version ?? ""}`;
}

// The title and artist in each style. Empty groups collapse, as in render.fill.
const STYLES = {
  artist_field: (track, separator) => ({
    title: withVersion(track.base_title, track),
    artist: [...track.main_artists, ...track.featured].join(separator),
  }),
  feat_in_title: (track, separator, wording) => {
    const featured = track.featured.join(separator);
    const title = featured ? `${track.base_title} (${wording} ${featured})` : track.base_title;
    return { title: withVersion(title, track), artist: track.main_artists.join(separator) };
  },
  all_in_title: (track, separator) => {
    const everyone = [...track.main_artists, ...track.featured].join(separator);
    const title = everyone ? `${track.base_title} (${everyone})` : track.base_title;
    return { title: withVersion(title, track), artist: track.main_artists.join(separator) };
  },
  custom: (track, separator, wording, settings) => {
    const values = {
      title: track.base_title,
      version: track.version ?? "",
      main: track.main_artists.join(separator),
      featured: track.featured.join(separator),
      artists: [...track.main_artists, ...track.featured].join(separator),
    };
    const fill = (template) => {
      return template.replace(/\{(\w+)\}/g, (whole, name) => values[name] ?? whole);
    };
    return {
      title: fill(settings.custom_title_template),
      artist: fill(settings.custom_artist_template),
    };
  },
};

function trackNumbers(overrides, suggestion) {
  if (!("track_no" in overrides)) return [suggestion.track_no, suggestion.track_total];
  const match = TRACK.exec(overrides.track_no.trim());
  if (!match) return [null, suggestion.track_total];
  return [Number(match[1]), match[2] ? Number(match[2]) : suggestion.track_total];
}

function numberOr(overrides, field, fallback) {
  if (!(field in overrides)) return fallback;
  return overrides[field].trim() ? Number(overrides[field]) : null;
}

// The album artist as render picks it: typed, or the suggestion's while a typed artist still
// names it, split on commas or the artist separator, or else the typed artist whole. Matching
// ignores case only, unlike the real one.
function albumArtist(overrides, track, separator) {
  if ("album_artist" in overrides) return overrides.album_artist;
  const suggested = track.album_artist ?? track.main_artists[0] ?? "";
  if (!("artist" in overrides)) return suggested;
  const typed = overrides.artist;
  const parts = [typed, ...typed.split(","), ...typed.toLowerCase().split(separator)];
  const names = parts.map((name) => name.trim().toLowerCase());
  return names.includes(suggested.toLowerCase()) ? suggested : typed;
}

function renderTags(job, settings) {
  const track = applied(job);
  const style = job.style ?? settings.title_style;
  const render = STYLES[style] ?? STYLES.artist_field;
  const rendered = render(track, settings.artist_separator, settings.feat_wording, settings);
  const overrides = job.overrides;
  const [trackNo, trackTotal] = trackNumbers(overrides, track);
  return {
    title: overrides.title ?? rendered.title,
    artist: overrides.artist ?? rendered.artist,
    album: overrides.album ?? track.album ?? withVersion(track.base_title, track),
    album_artist: albumArtist(overrides, track, settings.artist_separator),
    year: numberOr(overrides, "year", track.year),
    genre: "genre" in overrides ? overrides.genre || null : track.genre,
    track_no: trackNo,
    track_total: trackTotal,
    comment: job.source.url,
    source_url: job.source.url,
    source_id: job.source.id,
    isrc: null,
    label: null,
  };
}

function fileName(tags, job, settings) {
  const style = job.style ?? settings.title_style;
  const styleDefault = style === "all_in_title" ? "{title}" : "{artist} - {title}";
  const template = settings.filename_template ?? styleDefault;
  const filled = template.replace(/\{(\w+)\}/g, (whole, name) => String(tags[name] ?? ""));
  const extension = settings.audio_format === "m4a" ? ".m4a" : ".mp3";
  const stem = filled.replace(UNSAFE_IN_NAMES, "_").replace(/^ - | - $/g, "").trim();
  const name = `${stem}${extension}`;
  // Relative to the library, as the bridge sends it, so the artist's folder comes first.
  return settings.artist_folders ? `${artistFolder(tags.album_artist)}/${name}` : name;
}

// The artist's original when the source offers one, or else the best stream.
function chosenFormat(source) {
  if (source.original?.ext) {
    const ext = source.original.ext;
    return {
      format_id: "download",
      ext,
      codec: ext,
      bitrate: null,
      is_preview: false,
      is_original: true,
    };
  }
  const streams = source.formats.filter((format) => !format.is_preview);
  return streams.sort((a, b) => (b.bitrate ?? 0) - (a.bitrate ?? 0))[0] ?? null;
}

// A set as the bridge sends it: its tracks at their places in the album, and the album's fields.
function setForTheUi(job) {
  const places = trackPlaces(job);
  const children = job.children.map((child, index) => forTheUi(child, places[index]));
  return {
    ...job,
    state: setState(job),
    children,
    default_mode: defaultMode(job),
    tags: job.source ? albumTags(job) : null,
    filename: null,
    format: null,
    album_settled: albumSettled(job),
    cover_url: coverUrl(job),
    cover_size: coverSize(job),
    default_crop: null,
  };
}

// A job as the bridge sends it: its own fields, plus what the UI can't work out by itself. A
// set's track is tagged at its `place` in the album.
export function forTheUi(job, place = null) {
  if (job.kind === "set") return setForTheUi(job);
  const { settings } = settingsPart();
  const placed = placedTrack(job, place);
  const tags = job.source && job.suggestion ? renderTags(placed, settings) : null;
  const format = job.source && job.kind !== "retag" ? chosenFormat(job.source) : null;
  return {
    ...job,
    suggestion: sentSuggestion(job),
    artists_edited: Boolean(job.artists),
    needs_main_artist: needsMainArtist(job),
    tags,
    // A re-tagged file keeps its own name, as job_views.retag_view sends it.
    filename: job.kind === "retag" ? job.input.split(/[\\/]/).at(-1) : tags ? fileName(tags, job, settings) : null,
    saves_once_in: Boolean(job.saves_once_in),
    format: format ? { format, needs_bitrate_check: false, fallback: null } : null,
    cover_url: coverUrl(job),
    cover_size: coverSize(job),
    // Python cuts a letterboxed thumbnail from its picture, which sample jobs say themselves.
    default_crop: job.default_crop ?? null,
    children: [],
    file_tags: job.kind === "retag" ? fileTags(job.source) : undefined,
  };
}

// The title, artist and album a file holds now, as job_views.retag_view sends them. The mock's
// file sources hold the artist as their uploader, as localfile.py makes them.
function fileTags(source) {
  if (!source) return null;
  return { title: source.title, artist: source.uploader, album: source.album ?? "" };
}

// The title, artist and album each re-tag was confirmed for, by job id, as job_renames.py keeps.
const confirmedIdentities = new Map();

// The title, artist and album a re-tag would write, as one string, when they're new.
function newIdentity(job) {
  const { settings } = settingsPart();
  if (job.kind !== "retag" || !settings.retag_playlist_warning || !job.source) return null;
  const view = forTheUi(job);
  if (!view.tags) return null;
  const fields = ["title", "artist", "album"];
  if (fields.every((field) => view.tags[field] === view.file_tags[field])) return null;
  return JSON.stringify(fields.map((field) => view.tags[field]));
}

// Whether saving a re-tag needs the playlist question first, as job_renames.py decides.
export function asksAboutPlaylists(job) {
  const identity = newIdentity(job);
  return identity !== null && identity !== confirmedIdentities.get(job.id);
}

export function confirmRename(job) {
  confirmedIdentities.set(job.id, newIdentity(job));
}
