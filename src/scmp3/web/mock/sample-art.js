// Made-up cover art for the sample queue, as SVG data: URLs, which the page's CSP allows.
// The width and height become the image's size in pixels, so the size warnings can be tried.

// Each drawing's size, by its URL, so the mock can send cover_size without loading it.
const sizes = new Map();

export function artSize(url) {
  return sizes.get(url) ?? null;
}

function svgUrl(width, height, viewBox, shapes) {
  const size = `width="${width}" height="${height}" viewBox="${viewBox}"`;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" ${size}>${shapes}</svg>`;
  const url = `data:image/svg+xml,${encodeURIComponent(svg)}`;
  sizes.set(url, { width, height });
  return url;
}

// The design's cover for "Midnight Drive": a moon over a night road.
export function nightRoad(size = 1000) {
  return svgUrl(
    size,
    size,
    "0 0 248 248",
    '<rect width="248" height="248" fill="#1D2A4A"/>' +
      '<circle cx="170" cy="86" r="44" fill="#F2E6C9"/>' +
      '<rect y="168" width="248" height="80" fill="#121A30"/>' +
      '<path d="M124 168L52 248M124 168L196 248" fill="none" stroke="#FF7A30" stroke-width="4"/>' +
      '<path d="M124 182v10M124 204v14M124 230v18" fill="none" stroke="#F2E6C9" stroke-width="3"/>',
  );
}

// The design's simpler covers: a sky, a sun and a band of ground, drawn on a 44-unit square.
export function horizon({ sky, sun, ground, x, y, r, groundTop }, size = 1000) {
  return svgUrl(
    size,
    size,
    "0 0 44 44",
    `<rect width="44" height="44" fill="${sky}"/>` +
      `<circle cx="${x}" cy="${y}" r="${r}" fill="${sun}"/>` +
      `<rect y="${groundTop}" width="44" height="${44 - groundTop}" fill="${ground}"/>`,
  );
}

// A wide video thumbnail, as YouTube gives them, so the crop has something to do.
export function videoFrame(width = 1280, height = 720) {
  return svgUrl(
    width,
    height,
    "0 0 160 90",
    '<rect width="160" height="90" fill="#3B2346"/>' +
      '<circle cx="80" cy="45" r="24" fill="#E58FB5"/>' +
      '<rect y="70" width="160" height="20" fill="#2A1833"/>' +
      '<circle cx="28" cy="20" r="6" fill="#F2E6C9"/>',
  );
}

// A 16:9 video frame letterboxed into YouTube's 480 × 360 thumbnail, with black bands.
export function letterboxedFrame() {
  return svgUrl(
    480,
    360,
    "0 0 160 120",
    '<rect width="160" height="120" fill="#000"/>' +
      '<rect y="15" width="160" height="90" fill="#16304A"/>' +
      '<circle cx="80" cy="60" r="24" fill="#BFD9F2"/>',
  );
}

// The square Python cuts from letterboxedFrame(): the middle of its picture.
export const LETTERBOXED_CROP = { left: 0.21875, top: 0.125, width: 0.5625, height: 0.75 };

// A round profile picture, the kind SoundCloud falls back to when a track has no cover.
export function avatar(size = 500) {
  return svgUrl(
    size,
    size,
    "0 0 248 248",
    '<rect width="248" height="248" fill="#22201D"/>' +
      '<circle cx="124" cy="96" r="44" fill="#4A4540"/>' +
      '<path d="M54 248c10-44 38-70 70-70s60 26 70 70" fill="#4A4540"/>',
  );
}

// The palettes of the design's queue thumbnails.
export const PALETTES = {
  lantern: { sky: "#1F3B2E", sun: "#F0B44C", ground: "#163024", x: 22, y: 19, r: 9, groundTop: 33 },
  nightBus: { sky: "#123A3F", sun: "#F2D25C", ground: "#0C2A2E", x: 12, y: 24, r: 5, groundTop: 30 },
  oldFavorite: { sky: "#33312E", sun: "#8A847B", ground: "#33312E", x: 22, y: 22, r: 7, groundTop: 44 },
  dusk: { sky: "#3A2A1E", sun: "#FF9A5C", ground: "#22180F", x: 30, y: 20, r: 8, groundTop: 32 },
  tide: { sky: "#16304A", sun: "#BFD9F2", ground: "#0E2236", x: 14, y: 16, r: 6, groundTop: 31 },
  moss: { sky: "#25331F", sun: "#D8E39A", ground: "#18230F", x: 26, y: 15, r: 7, groundTop: 34 },
};
