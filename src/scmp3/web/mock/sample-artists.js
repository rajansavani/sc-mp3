// Tracks with long artist lists, for ?mock&artists: the reported "gg fuckin ez", and a made-up
// cypher with 20 featured names.
import { horizon, PALETTES } from "./sample-art.js";
import { makeJob, makeSource, makeSuggestion } from "./sample-queue.js";

const CYPHER_NAMES = [
  "Ash Kito",
  "bleu.wav",
  "Corin Vale",
  "Dex Moreau",
  "eli sunday",
  "Fenn",
  "Gia Lark",
  "Halo Kid",
  "Ivo Strand",
  "Jade Okafor",
  "kxmi",
  "Lio Brandt",
  "Mika Ren",
  "Nyx Arden",
  "Oro",
  "Pax Hollow",
  "Quinn Ave",
  "Rue Calder",
  "Sol Mendez",
  "Tavi",
];

// SoundCloud's publisher field lists all three, and the uploader is the main artist (T-033).
function ggFuckinEz() {
  const url = "https://soundcloud.com/gunk/gg-fuckin-ez";
  const title = "gg fuckin ez ദ്ദി(˵ •̀ ᴗ - ˵ ) ✧";
  return makeJob({
    id: "gg-fuckin-ez",
    input: url,
    state: "needs_review",
    source: makeSource({
      id: "soundcloud:tracks:7001",
      url,
      title: "gg fuckin ez  ദ്ദി(˵ •̀ ᴗ - ˵ ) ✧",
      uploader: "gunk",
      artists: ["gunk, zayok, edyn "],
      duration: 142,
      artwork_url: horizon(PALETTES.dusk, 1000),
    }),
    suggestion: makeSuggestion({
      base_title: title,
      main_artists: ["gunk"],
      featured: ["zayok", "edyn"],
    }),
    staged_audio: "staging/gg-fuckin-ez/audio.m4a",
  });
}

function basementCypher() {
  const url = "https://soundcloud.com/nova-reyes/basement-cypher";
  return makeJob({
    id: "basement-cypher",
    input: url,
    state: "needs_review",
    source: makeSource({
      id: "soundcloud:tracks:7002",
      url,
      title: `Basement Cypher (feat. ${CYPHER_NAMES.join(", ")})`,
      uploader: "Nova Reyes",
      duration: 486,
      artwork_url: horizon(PALETTES.moss, 1000),
    }),
    suggestion: makeSuggestion({
      base_title: "Basement Cypher",
      main_artists: ["Nova Reyes"],
      featured: CYPHER_NAMES,
    }),
    staged_audio: "staging/basement-cypher/audio.m4a",
  });
}

export function artistSamples() {
  return [ggFuckinEz(), basementCypher()];
}
