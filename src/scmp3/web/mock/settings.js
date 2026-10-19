// The settings calls, starting from the defaults of Settings in core/models.py.

const DEFAULTS = {
  library_dir: "~/Music/sc-mp3",
  filename_template: null,
  when_file_exists: "ask",
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

// The track the previews render, from the design's sample data.
const SAMPLE = { base_title: "Midnight Drive", main_artists: ["Kaia"], featured: ["Lou V"] };

// Where each title style puts featured artists, as the design's segmented control shows.
const STYLES = {
  artist_field: (track, separator) => ({
    title: track.base_title,
    artist: [...track.main_artists, ...track.featured].join(separator),
  }),
  feat_in_title: (track, separator, wording) => ({
    title: track.featured.length
      ? `${track.base_title} (${wording} ${track.featured.join(separator)})`
      : track.base_title,
    artist: track.main_artists.join(separator),
  }),
  all_in_title: (track, separator) => ({
    title: `${track.base_title} (${[...track.main_artists, ...track.featured].join(separator)})`,
    artist: track.main_artists.join(separator),
  }),
};

// ?mock&first_run shows first-run setup, the way a clean profile does.
const startsWithSetup = new URLSearchParams(window.location.search).has("first_run");

let settings = { ...DEFAULTS, first_run_done: !startsWithSetup };

function fileName(rendered, style) {
  const template = settings.filename_template ?? (style === "all_in_title" ? "{title}" : "{artist} - {title}");
  const extension = settings.audio_format === "m4a" ? ".m4a" : ".mp3";
  return template.replace("{artist}", rendered.artist).replace("{title}", rendered.title) + extension;
}

export function snapshotPart() {
  return { settings, first_run: !settings.first_run_done };
}

export function update_settings(patch) {
  const unknown = Object.keys(patch).filter((key) => !(key in DEFAULTS));
  if (unknown.length > 0) return { error: "invalid_settings", keys: unknown };
  settings = { ...settings, ...patch };
  return { settings };
}

export function preview_render(sample = null, style = null) {
  const track = { ...SAMPLE, ...sample };
  const chosen = style ?? settings.title_style;
  const render = STYLES[chosen] ?? STYLES.artist_field;
  const rendered = render(track, settings.artist_separator, settings.feat_wording);
  return { ...rendered, filename: fileName(rendered, chosen) };
}
