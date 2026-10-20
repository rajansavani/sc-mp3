// The settings calls, starting from the defaults of Settings in core/models.py. The checks and
// the previews follow app/bridge/settings.py closely enough to try every state in --dev-ui, but
// the previews only understand the design's sample track.
//
// Canned folders: a path with "read-only" in it can't be written to, and one ending in .mp3 is a
// file. ?mock&first_run starts with first-run setup, the way a clean profile does.
import { artistFolder, safeName } from "./names.js";

const DEFAULTS = {
  library_dir: "~/Music/sc-mp3",
  filename_template: null,
  when_file_exists: "ask",
  artist_folders: true,
  album_subfolders: false,
  title_style: "artist_field",
  custom_title_template: "{title}{version}",
  custom_artist_template: "{artists}",
  artist_separator: ", ",
  feat_wording: "feat.",
  remove_producer_credits: true,
  remove_promo_text: true,
  custom_words: [],
  singles_album: "title",
  singles_album_text: "",
  audio_format: "mp3_320",
  keep_lossless_originals: false,
  cover_max_size: 1000,
  save_cover_jpg: false,
  comment_mode: "source_url",
  review_before_saving: true,
  download_while_reviewing: true,
  offer_clipboard_links: true,
  concurrent_downloads: 2,
  check_app_updates: true,
  check_engine_updates: true,
  engine_channel: "stable",
  soundcloud_client_id: null,
  first_run_done: false,
};

// The values each choice accepts, as in core/models.py.
const CHOICES = {
  when_file_exists: ["ask", "keep_both", "replace", "skip"],
  title_style: ["artist_field", "feat_in_title", "all_in_title", "custom"],
  artist_separator: [", ", " & ", " x "],
  feat_wording: ["feat.", "ft.", "featuring", "with"],
  singles_album: ["title", "fixed", "blank"],
  audio_format: ["mp3_320", "mp3_v0", "m4a"],
  cover_max_size: [300, 640, 1000, 1400],
  comment_mode: ["source_url", "description", "none"],
  concurrent_downloads: [1, 2, 3, 4],
  engine_channel: ["stable", "nightly"],
};
// Text settings that may also be null.
const NULLABLE = new Set(["filename_template", "soundcloud_client_id"]);

const BRACED = /\{[^{}]*\}/g;
const FILENAME_TOKEN = /^\{(artist|title|album|album_artist|year|track)(?::0[1-9])?\}$/;
const TITLE_TOKEN = /^\{(title|version|main|featured|artists)\}$/;

// The design's track, as the suggestion engine splits it up.
const SAMPLE = {
  title: "Midnight Drive (feat. Lou V) [prod. Hazy] FREE DL",
  uploader: "Kaia",
  base_title: "Midnight Drive",
  main: ["Kaia"],
  featured: ["Lou V"],
  producer: "[prod. Hazy]",
  promo: "FREE DL",
  year: 2026,
};

const startsWithSetup = new URLSearchParams(window.location.search).has("first_run");

let settings = { ...DEFAULTS, first_run_done: !startsWithSetup };

function fitsType(key, value) {
  if (key in CHOICES) return CHOICES[key].includes(value);
  if (NULLABLE.has(key)) return value === null || typeof value === "string";
  const expected = DEFAULTS[key];
  if (Array.isArray(expected)) {
    return Array.isArray(value) && value.every((item) => typeof item === "string");
  }
  return typeof value === typeof expected;
}

function templateProblem(template, tokenPattern) {
  const braced = template.match(BRACED) ?? [];
  const unknown = braced.find((part) => !tokenPattern.test(part));
  if (unknown) return { reason: "UNKNOWN_TOKEN", token: unknown };
  if (/[{}]/.test(template.replace(BRACED, ""))) return { reason: "OPEN_BRACE" };
  if (braced.length === 0) return { reason: "NO_TOKENS" };
  return null;
}

function titleTemplateProblem(template) {
  if (template.includes("{feat}")) return { reason: "FEAT_TOKEN" };
  return templateProblem(template, TITLE_TOKEN);
}

function folderProblem(path) {
  if (!/^(?:[A-Za-z]:[\\/]|\/|~[\\/])/.test(path)) return { reason: "NOT_ABSOLUTE" };
  if (/\.mp3$/i.test(path)) return { reason: "NOT_A_FOLDER", folder: path };
  if (/read-only/i.test(path)) return { reason: "NOT_WRITABLE", folder: path };
  return null;
}

// Checks for values of the right type, by setting.
const VALUE_CHECKS = {
  library_dir: folderProblem,
  filename_template: (template) =>
    template === null ? null : templateProblem(template, FILENAME_TOKEN),
  custom_title_template: titleTemplateProblem,
  custom_artist_template: titleTemplateProblem,
};

function problemOf(key, value) {
  if (!(key in DEFAULTS)) return { reason: "UNKNOWN_SETTING" };
  if (!fitsType(key, value)) return { reason: "INVALID_VALUE" };
  return VALUE_CHECKS[key]?.(value) ?? null;
}

// The sample's parts once the cleanup settings have had their say.
function sampleTrack(sample) {
  if (sample?.title !== undefined && sample.title !== SAMPLE.title) {
    const uploader = sample.uploader ?? SAMPLE.uploader;
    return { title: sample.title, version: "", main: [uploader], featured: [] };
  }
  const kept = [
    settings.remove_producer_credits ? null : SAMPLE.producer,
    settings.remove_promo_text ? null : SAMPLE.promo,
  ];
  const title = [SAMPLE.base_title, ...kept].filter(Boolean).join(" ");
  return { title, version: "", main: SAMPLE.main, featured: SAMPLE.featured };
}

function fill(template, values) {
  const filled = template.replace(/\{(\w+)\}/g, (token, name) => values[name] ?? token);
  return filled.replace(/\s*(?:\(\s*\)|\[\s*\])/g, "").replace(/\s+/g, " ").trim();
}

function render(track, style) {
  const separator = settings.artist_separator;
  const values = {
    title: track.title,
    version: track.version,
    main: track.main.join(separator),
    featured: track.featured.join(separator),
    artists: [...track.main, ...track.featured].join(separator),
  };
  const templates = {
    artist_field: ["{title}{version}", "{artists}"],
    feat_in_title: [`{title} (${settings.feat_wording} {featured}){version}`, "{main}"],
    all_in_title: ["{title} ({artists}){version}", "{main}"],
    custom: [settings.custom_title_template, settings.custom_artist_template],
  };
  const [title, artist] = templates[style];
  // An empty group takes its words along, so no "(feat. )" is left.
  const titleTemplate = values.featured
    ? title
    : title.replace(/\s*\([^()]*\{featured\}[^()]*\)/, "");
  return { title: fill(titleTemplate, values), artist: fill(artist, values) };
}

function singlesAlbum(track) {
  const albums = { title: track.title, fixed: settings.singles_album_text, blank: "" };
  return albums[settings.singles_album];
}

function styleTemplate(style) {
  return style === "all_in_title" ? "{title}" : "{artist} - {title}";
}

function filenameTemplate(style) {
  if (settings.filename_template?.trim()) return settings.filename_template;
  return styleTemplate(style);
}

// The sample is a single, and singles stay out of album folders, so the only folder it can get
// is its artist's.
function fileName(rendered, track, style) {
  const values = {
    artist: rendered.artist,
    title: rendered.title,
    album: singlesAlbum(track),
    album_artist: track.main[0] ?? "",
    year: String(SAMPLE.year),
    track: "",
  };
  const template = filenameTemplate(style).replace(/\{track:0\d\}/g, "{track}");
  const stem = safeName(fill(template, values)) || "Untitled";
  const extension = settings.audio_format === "m4a" ? ".m4a" : ".mp3";
  const name = `${stem}${extension}`;
  return settings.artist_folders ? `${artistFolder(values.album_artist)}/${name}` : name;
}

export function snapshotPart() {
  return { settings, first_run: !settings.first_run_done };
}

export function update_settings(patch) {
  if (patch === null || typeof patch !== "object" || Array.isArray(patch)) {
    return { error: "invalid_settings", fields: {} };
  }
  const fields = {};
  for (const [key, value] of Object.entries(patch)) {
    const problem = problemOf(key, value);
    if (problem) fields[key] = problem;
  }
  if (Object.keys(fields).length > 0) return { error: "invalid_settings", fields };
  const cleaned = { ...patch };
  // An empty template, or the style's own, is saved as none, so the name follows the style.
  const template = cleaned.filename_template;
  const style = cleaned.title_style ?? settings.title_style;
  if (typeof template === "string" && (!template.trim() || template === styleTemplate(style))) {
    cleaned.filename_template = null;
  }
  settings = { ...settings, ...cleaned };
  return { settings };
}

export function preview_render(sample = null, style = null) {
  const chosen = style ?? settings.title_style;
  if (!CHOICES.title_style.includes(chosen)) return { error: "invalid_style" };
  const track = sampleTrack(sample);
  const rendered = render(track, chosen);
  return {
    ...rendered,
    filename: fileName(rendered, track, chosen),
    filename_template: filenameTemplate(chosen),
  };
}
