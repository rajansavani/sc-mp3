# Vendored files

The UI has no build step, so these files are copied unchanged from the npm packages below. Each tarball was checked against the sha512 integrity value the npm registry publishes before anything was taken out of it. `tests/unit/test_web.py` checks the SHA-256 of every file listed here, and that nothing else is in `vendor/` or `fonts/`.

## JavaScript

`index.html` maps the bare names `preact`, `preact/hooks`, `htm` and `htm/preact` to these files with an import map, so they import each other exactly as they do on npm.

| Package | Version | License | Published |
|---|---|---|---|
| [preact](https://www.npmjs.com/package/preact) | 10.29.8 | MIT, in `LICENSE-preact.txt` | 2026-08-01 |
| [htm](https://www.npmjs.com/package/htm) | 3.1.1 | Apache-2.0, in `LICENSE-htm.txt` | 2022-04-26 |

Preact 11.0.0 came out on 2026-09-30, so the last 10.x release is used until 11 has been out for a while.

| File | Taken from | SHA-256 |
|---|---|---|
| `vendor/preact.module.js` | preact `dist/preact.module.js` | `c30e721ebfdc6e2ad4c18c14d2dfb82667829c8aec27de1207774e3fc16858a8` |
| `vendor/preact.module.js.map` | preact `dist/preact.module.js.map` | `23c9b5e6e405d2883bad8414d9483ad3ecdb85a94be52655db980cfc83460c29` |
| `vendor/hooks.module.js` | preact `hooks/dist/hooks.module.js` | `a6ee626f2d01570592dd569a792e3f050154aa02890eead8c223fa3ed5aa3d5a` |
| `vendor/hooks.module.js.map` | preact `hooks/dist/hooks.module.js.map` | `a13936803e904e19f2f154e953541c20dbbd0667c881f446e7aefafcfe487a97` |
| `vendor/LICENSE-preact.txt` | preact `LICENSE` | `1fe6958409c8c257a70c587a18b6f7f412b179b456630790d30b2ec9a8e4b7d4` |
| `vendor/htm.module.js` | htm `dist/htm.module.js` | `ab33dd3f38059b9be4d5f5350128eefb2356639c4e0bbe9d9e8b3ba75847e9e4` |
| `vendor/htm-preact.module.js` | htm `preact/index.module.js` | `b3f51db71c12eabba61819feb74ace7cadc2744af41866c3fcad2ab960415746` |
| `vendor/LICENSE-htm.txt` | htm `LICENSE` | `740725f7252e750af735d0028cc534970772f513331e9f68150fede8fb3ce00f` |

## Fonts

Fontsource's variable builds of the Google Fonts files: one weight axis per font, and the latin and latin-ext subsets. `styles/base.css` declares them with the same `unicode-range` values Fontsource uses, so latin-ext only loads when a title needs it.

| Package | Version | License | Published |
|---|---|---|---|
| [@fontsource-variable/instrument-sans](https://www.npmjs.com/package/@fontsource-variable/instrument-sans) | 5.3.0 | OFL-1.1, in `fonts/OFL-InstrumentSans.txt` | 2026-07-19 |
| [@fontsource-variable/jetbrains-mono](https://www.npmjs.com/package/@fontsource-variable/jetbrains-mono) | 5.3.0 | OFL-1.1, in `fonts/OFL-JetBrainsMono.txt` | 2026-07-19 |

Instrument Sans covers weights 400 to 700, and JetBrains Mono 100 to 800, of which the UI uses 400 and 500.

| File | Taken from | SHA-256 |
|---|---|---|
| `fonts/instrument-sans-latin-wght-normal.woff2` | instrument-sans `files/` | `2ee17598a98d8a59e4df8152d015bec9ab8e4d5672cc0ab42bef806b568e3971` |
| `fonts/instrument-sans-latin-ext-wght-normal.woff2` | instrument-sans `files/` | `c4fcfea41f2c1cfeea9211fa43679845454a1d0e0d7e95e069c7e73c4ae302d2` |
| `fonts/OFL-InstrumentSans.txt` | instrument-sans `LICENSE` | `c27a3c53c3beed7f5c26853afa15991478ff7145d3754a36b0382f84e10c0d03` |
| `fonts/jetbrains-mono-latin-wght-normal.woff2` | jetbrains-mono `files/` | `18be452724bfdc236c074ca94a249a7f41a86752c7d04ab258ce9ed5651f6a7e` |
| `fonts/jetbrains-mono-latin-ext-wght-normal.woff2` | jetbrains-mono `files/` | `79bfdab9ba467e26eea4122e6f2567e188dd8a09a8c730d501fc487c4ab99c6e` |
| `fonts/OFL-JetBrainsMono.txt` | jetbrains-mono `LICENSE` | `403581b69dac5cff4079205e01c6b467e56af449ecbd7247693ddb1baafa005b` |

## Updating

1. Download the new tarball into an empty folder, and check it against the registry's `dist.integrity` value.
2. Copy the files listed above, unchanged.
3. Update the versions and checksums here.
