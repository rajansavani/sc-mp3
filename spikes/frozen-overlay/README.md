# Spike: a frozen app with an updatable yt-dlp (Windows)

This spike checks, on Windows 11, the packaging assumptions behind decision D3: a PyInstaller onedir `--windowed` build of a pywebview window, with a downloaded yt-dlp zipapp put first on `sys.path` so the engine can be updated without a new app release. It's throwaway code. The real overlay goes into `src/` in T-071, and the release build in T-030.

## Files

| File | What it does |
|---|---|
| `spike_app.py` | The tiny app. It opens a pywebview window that closes itself, runs the yt-dlp checks it's asked for, and writes a JSON report. It also checks a new overlay in a child process (`--install-overlay`), and starts itself again when an overlay fails. |
| `spike_engine.py` | Loads yt-dlp from the overlay at `sys.path[0]`, or from the bundle when the overlay can't load. It imports yt-dlp once per process. |
| `spike_checks.py` | The yt-dlp checks: engine details, importing every submodule, `extract_info` with a captured verbose log, an impersonated request, and requests made after a fallback. |
| `spike_console.py` | Attaches a windowed app to the console it was started from (question 6). |
| `frozen_overlay.spec` | The PyInstaller spec: onedir, windowed, the unused pywebview backends excluded. |
| `build.py` | Runs PyInstaller on the spec and prints the bundle size and the build warnings. |
| `fetch.py` | Downloads the yt-dlp zipapp, Deno and yt-dlp-ejs 0.7.0 into `downloads/`, checks each SHA-256, and writes `downloads/manifest.json`. |
| `run_spike.py` | Starts the frozen app in every scenario, each with a timeout, and saves the reports under `results/`. |
| `launchers.py` | The ways `run_spike.py` starts the app: as a double-click would, with pipes, from cmd or from PowerShell. |
| `read_console.py` | Saves what a console window showed, for the `--version` tests. |

`downloads/`, `build/`, `dist/` and `results/` are gitignored.

## Run it

You need Windows 11 x64, uv, a network connection and a desktop session. The windows close by themselves after a few seconds, and nothing waits for a click. From the repo root of a clean checkout:

```
uv sync --locked
uv run --locked python spikes/frozen-overlay/fetch.py
uv run --locked python spikes/frozen-overlay/build.py
uv run --locked python spikes/frozen-overlay/build.py --variant no-copy-metadata
uv run --locked python spikes/frozen-overlay/build.py --variant no-metadata
uv run --locked python spikes/frozen-overlay/build.py --variant no-excludes
uv run --locked python spikes/frozen-overlay/run_spike.py
```

- `fetch.py` pins the versions used below, with their full SHA-256:
  - `yt-dlp` (nightly 2026.10.10.233141): `f40b6a9e4c5786360953911cab5d2f87507626b8fb7bfee3f8f1c2e5cf341fae`
  - `deno-x86_64-pc-windows-msvc.zip` (v2.9.7): `a0c3101b4158d1dfb7d6a78a7bf0f3de80c96bb423c152beec8beb22786f2238`
  - `yt_dlp_ejs-0.7.0-py3-none-any.whl`: `967e9cbe114ddfd046ff4668af18b1827b4597e2e47a83deea668a355828c798`
- Every run of `fetch.py` checks every file again, including ones already downloaded. A download goes into a temporary folder that's moved into place only after its hash matched, so an interrupted run leaves nothing half done.
- Pass `--yt-dlp-repo`, `--yt-dlp-tag`, `--deno-tag` or `--old-ejs-version` to try other versions. A version without a pin is checked only against its release's own checksum, and `fetch.py` says so. `run_spike.py` reads `downloads/manifest.json`, so it uses whatever `fetch.py` fetched last.
- Each `build.py` run takes about 30 s. The variants are for questions 5 and 7.
- `run_spike.py` takes about 4 minutes and opens about 30 windows and 4 console windows. It writes `results/<time>/summary.json`, with this machine's paths replaced by `<spike>` and `<home>`, plus one full report per scenario. `--only window,overlay` runs a subset.
- The broken and mismatched overlays are made fresh in `results/<time>/overlays/` on each run.
- Keep the checkout's path short. The `fresh-copy` and `path-*` scenarios copy the bundle into `results/<time>/copies/`, which works when the checkout's own path is at most about 80 characters. From a deeper checkout they're skipped, and the summary says so. Past about 125 characters, curl_cffi fails to load in the build itself (see "Install path").

## Results

Run on 2026-10-19 on Windows 11 Pro 10.0.26200 (x64), with Python 3.13.7 from uv. The bundle has yt-dlp 2026.08.19 with yt-dlp-ejs 0.8.0, pywebview 6.2.1, pythonnet 3.2.1, clr-loader 0.3.1 and curl_cffi 0.16.3. The overlay is the nightly zipapp 2026.10.10.233141. The latest stable, 2026.08.19, is the bundled version, so the nightly is the only newer engine. Deno is 2.9.7 and the WebView2 runtime is 155.0.4283.62.

The numbers come from the last full run of `run_spike.py`. Earlier runs gave the same results, with timings within about 0.2 s.

### 1. Frozen window

Yes. The onedir `--windowed` build opens its window on Windows 11, with pythonnet and WebView2 working.

- pywebview's renderer is `edgechromium`, and the page sees `window.chrome.webview`. The user agent ends in `Edg/155.0.0.0`, and `CoreWebView2Environment.GetAvailableBrowserVersionString()` returns `155.0.4283.62`.
- pythonnet 3.2.1 loaded the .NET Framework runtime (`System.Environment.Version` 4.0.30319.42000) on the first try. "Failed to create a default .NET runtime" (pywebview #1735) didn't happen, and pywebview's CoreCLR fallback wasn't needed. A similar error does appear when the app sits in a very deep folder (see "Install path").
- Bundle size: 40.4 MB in 125 files, 22.4 MB zipped. The exe holds the Python modules and is 11.4 MB. The largest files are `libcrypto-3-x64.dll` (7.6 MB), `python313.dll` (5.8 MB), `curl_cffi.libs` (3.7 MB), `Cryptodome` (1.6 MB), `sqlite3.dll` (1.5 MB) and `libssl-3-x64.dll` (1.5 MB). ffmpeg and the web assets aren't included.
- Start time, measured from process creation (`GetProcessTimes`) to pywebview's events, over five launches:

  | | Fastest | Slowest |
  |---|---|---|
  | `main()` reached | 0.15 s | 0.16 s |
  | window shown | 0.54 s | 0.56 s |
  | page loaded | 0.78 s | 0.85 s |

  This isn't a cold boot: the files were in the disk cache.

  A copy of the bundle made just before launching, like a release that was just unzipped, was slower the first time. `main()` came at 0.49 s, the window at 1.05 s and the page at 1.34 s. The second launch of the same copy was back to 0.55 s and 0.79 s. Antivirus scanning of the new files is the likely cause, but that wasn't checked.

  Loading yt-dlp before opening the window delays it. The window showed at 0.70 to 0.76 s with the bundled copy, and at 1.35 to 1.54 s with the overlay (see question 2).
- The app's own deadline closes a run that takes too long, in both cases tested. A shown window is closed through `destroy()` (`deadline-shown`). If the window hasn't shown yet, the process ends with `os._exit` (`deadline-not-shown`), because pywebview's `destroy()` first waits 20 s for the window to show and then raises. Both runs exit with 5 and keep their report.

### 2. Overlay

Yes, the overlay's version is the one that runs. Falling back cleanly needs the overlay checked before any of its code runs, and a restart when it fails later.

With the nightly zipapp inserted at `sys.path[0]` before the first `import yt_dlp`:

- `yt_dlp.version.__version__` is `2026.10.10.233141`, and `yt_dlp.__file__` is inside the zip. All 76 loaded `yt_dlp*` modules came from the zip, `yt_dlp_ejs` included. PyInstaller 6.22.3's frozen importer is a `sys.path` hook, so `sys.path` order decides.
- All 1,042 `yt_dlp` submodules import from the overlay without an error, the same count as the bundled copy. So the bundle has every standard-library module this nightly needs.
- The zip holds only `.py` files, which get compiled on every launch. Importing yt-dlp took 0.52 to 0.57 s from the overlay, against 0.15 to 0.17 s for the bundled copy. Importing every submodule took 1.4 s against 0.22 s.
- yt-dlp's updater sees the overlay as `win_exe`, so it would try to replace the app's exe. The bundled copy reports `pip`. This confirms the rule in TECH_SPEC §14: never run yt-dlp's updater in-process.

#### Why there's no in-process fallback

yt-dlp changes other packages when it's imported. `yt_dlp/networking/_requests.py` wraps `urllib3.util.url._PERCENT_RE` in a `Urllib3PercentREOverride`, and it replaces `requests.adapters.select_proxy` and `SOCKSProxyManager`. Deleting `yt_dlp*` from `sys.modules` doesn't undo any of that, and a second import wraps the regex a second time. The outer wrapper can't reach the inner one's methods, so urllib3's URL parsing breaks.

The spike's first loader worked that way: on any failure it deleted the `yt_dlp*` modules and imported the bundled copy again. Its run at commit `d76c769` (`results/20261018-205456`) failed after all four fallbacks:
- `requests.Request("GET", url).prepare()` and `requests.get` raised `AttributeError: 'Urllib3PercentREOverride' object has no attribute 'sub'`.
- yt-dlp's own request logged `[requests] Unexpected error`, and it only got through by retrying with urllib.

The fallback seemed clean, because yt-dlp hid the failure.

#### How the loader works now

`spike_engine.py` imports yt-dlp exactly once per process:

1. **Before importing anything**, insert the zip at `sys.path[0]` and call `importlib.util.find_spec("yt_dlp")`. If it raises, or the spec doesn't come from the zip, take the zip off `sys.path` and out of `sys.path_importer_cache` and import the bundled copy. None of the overlay's code has run.
2. **A newly downloaded overlay** is checked in a child process before it's marked good. `--install-overlay <zip>` starts the same exe with `--check-overlay <zip>`. The child imports the overlay with no fallback, makes a `YoutubeDL` and looks up one extractor, then prints the version and exits with 0. The parent never imports yt-dlp. It writes `manifest.json` next to the zip, with the status `good` or `bad`.
3. **If an overlay still fails after its code ran**, the app marks it `bad` in `manifest.json` and starts itself again. The new process skips an overlay marked `bad` without importing it. The spike waits for the new process so that its driver sees one exit code; the real app would exit instead.

After each change of plan, the spike made the same requests again. `requests.Request().prepare()` worked, `requests.get` returned 200, and yt-dlp's request was answered by `RequestsResponseAdapter` with no error logged:

| Overlay | What happened | Engine used |
|---|---|---|
| Deleted | `find_spec` found the bundled copy, so the overlay was skipped before any import. `zipimport.zipimporter()` on this path raises `ZipImportError: not a Zip file`, and Python's import system skips such a `sys.path` entry without an error. | 2026.08.19, 73 modules, all from the bundle |
| Random bytes, same size | Same as deleted. | Same |
| First half of the zip | Same as deleted. | Same |
| Valid zip, `yt_dlp/YoutubeDL.py` raises at import | `find_spec` points into the zip, so the import starts, and it raises `RuntimeError` partway through. The app marked the overlay `bad` and started again. | The new process: 2026.08.19, all from the bundle |
| Checked with `--install-overlay`: the official zip | `good`, version `2026.10.10.233141`. A later start used it. | The overlay, 76 modules from the zip |
| Checked with `--install-overlay`: random bytes | `bad`: "yt_dlp would load from" the bundle. | none, nothing was imported |
| Checked with `--install-overlay`: the broken zip | `bad`: `RuntimeError: Simulated corruption in the overlay`. A later start skipped it as marked bad. | The bundle, imported once |

The `yt_dlp.__file__` check after the import stays as the last guard.

Importing `yt_dlp` also adds two plugin finders to `sys.meta_path`, one from `yt_dlp/extractor/__init__.py` and one from `yt_dlp/postprocessor/__init__.py`. With the old loader they didn't need removing. Each finder answers only for its own `yt_dlp_plugins.*` names, and the second import put new ones in front. The new loader never imports twice, so it doesn't touch them.

#### Plugins

yt-dlp loads plugins on the first `YoutubeDL()`. It looks in `yt-dlp-plugins` folders next to the exe and in the user and system config folders, and in every `sys.path` entry. So plugins someone installed for their own yt-dlp would run inside the app. `plugin_dirs` is not a `YoutubeDL` option. It's the global `yt_dlp.globals.plugin_dirs`, which only the command line sets (`--no-plugin-dirs`).

Two switches turn plugins off. Both were tested with a plugin on `sys.path`: by default `SpikeTestPluginIE` loaded, and with either switch nothing did.
- the environment variable `YTDLP_NO_PLUGINS`, set to any value before the first `YoutubeDL()`. `load_plugins()` checks it, so it works whichever copy of yt-dlp loads, and it needs no import first. The spike sets it at startup.
- `yt_dlp.globals.plugin_dirs.value = []` after the import, which is what `--no-plugin-dirs` does.

### 3. EJS mismatch

With the official zipapp there is no mismatch, and YouTube works without `remote_components`. The zipapp carries its own `yt_dlp_ejs` next to `yt_dlp`: its 1,053 entries are `__main__.py`, `yt_dlp/` and `yt_dlp_ejs/`. With the zip first on `sys.path`, its copy hides the bundled one, so the scripts always match the engine.

Today nothing newer exists to test. yt-dlp-ejs 0.8.0 (2026-03-17) is the latest release, and every yt-dlp since 2026.03.17 expects it, the nightly included. To test a real mismatch, `run_spike.py` builds a `stale_ejs` overlay: the nightly zip with its `yt_dlp_ejs` swapped for the released 0.7.0 package. yt-dlp requires the same major.minor exactly (`jsc/_builtin/ejs.py`), so an older yt-dlp-ejs is rejected the same way a newer one would be.

The default clients (`visionos` and `web`) extracted the test video without any JS challenge. So these runs force the `mweb` and `web_embedded` clients, whose formats (format 18, for example) need the n challenge solved. Deno is passed as `js_runtimes={"deno": {"path": ...}}`, and the header shows `JS runtimes: deno-2.9.7`. Each run used a fresh `DENO_DIR` and no yt-dlp cache.

| Engine | yt-dlp-ejs it imported | `remote_components` | Result |
|---|---|---|---|
| Bundled 2026.08.19 | 0.8.0, from the bundle | none | Works. Scripts v0.8.0 from the Python package. |
| Nightly zipapp | 0.8.0, from the zip | none | Works. Scripts v0.8.0 from the Python package. |
| Nightly, its `yt_dlp_ejs` removed | 0.8.0, from the bundle | none | Works. Scripts v0.8.0 from the Python package. |
| Nightly with 0.7.0 | 0.7.0, from the zip | none | Fails. "Challenge solver lib script version 0.7.0 is not supported", then "n challenge solving failed", then "No video formats found!" |
| Nightly with 0.7.0 | 0.7.0, from the zip | `["ejs:github"]` | Works. The lib script v0.8.0 came from `https://github.com/yt-dlp/ejs/releases/download/0.8.0/yt.solver.lib.min.js`, and the core script v0.8.0 from the copy vendored inside yt-dlp. |

Every run that worked listed format 18. The total number of formats changed from run to run (4 to 18), along with YouTube's answers. Most runs warned that mweb formats need a PO token, and one run warned instead that "YouTube may have enabled the SABR-only streaming experiment".

So `remote_components=["ejs:github"]` does fix a real mismatch, but the official zipapp never needs it. It works as a guard: set it only when the yt-dlp-ejs that loaded isn't the major.minor that yt-dlp requires (`vendor.VERSION`).

### 4. SoundCloud

Yes. `extract_info(url, download=False)` on `https://soundcloud.com/ethmusic/lostin-powers-she-so-heavy` (id 62986583) worked from the frozen app with both engines, in 1.3 to 1.4 s. It returned one format, `hls_aac_96k`.

- The verbose header lists `Request Handlers: urllib, requests, websockets, curl_cffi` and `curl_cffi-0.16.3` among the optional libraries.
- yt-dlp 2026.08.19's SoundCloud extractor asks for impersonation only on set pages, not single tracks. So the spike also made its own request through `ydl.urlopen` with `ImpersonateTarget("chrome")`. It returned status 200 through `yt_dlp.networking._curlcffi.CurlCFFIResponseAdapter`, with 38 impersonation targets available.

curl_cffi breaks in a deep folder, and in a folder whose name has characters outside the ANSI code page, and yt-dlp says nothing either way. See "Install path" below.

### 5. Build details

- PyInstaller 6.22.3 with pyinstaller-hooks-contrib 2026.8. No custom hooks and no hidden imports were needed.
- Hooks that ran:
  - shipped with the packages: `webview/__pyinstaller/hook-webview.py` (the WebView2 DLLs and pywebview's JS), `pythonnet/_pyinstaller/hook-clr.py` (`Python.Runtime.dll`) and `yt_dlp/__pyinstaller/hook-yt_dlp.py` (hidden imports, curl_cffi's `cacert.pem` and the yt-dlp-ejs scripts)
  - from hooks-contrib: `clr_loader`, `Cryptodome`, `certifi`, `charset_normalizer`, `pycparser`, `urllib3`, `websockets`
  - runtime hooks: `pyi_rth_inspect`, `pyi_rth_multiprocessing`, `pyi_rth_pkgutil`, `pyi_rth_setuptools`
- Excludes: the pywebview backends `webview.platforms.android`, `.cef`, `.cocoa`, `.gtk` and `.qt`, plus `cefpython3`, `gi`, `PyQt5`, `PyQt6`, `PySide2`, `PySide6` and `qtpy`. Qt, CEF and GTK aren't installed on Windows, so naming the toolkits changes nothing here. The excludes keep 19 backend modules (128 KB compressed) out. The `no-excludes` build is 40.5 MB, and its window works the same.
- The build log has three warnings, none of which matter:
  - `Failed to collect submodules for 'urllib3.contrib.emscripten' ... No module named 'js'`. yt-dlp's hook collects every urllib3 submodule, and this one only runs under Pyodide.
  - `Hidden import "pycparser.lextab" not found!` and the same for `pycparser.yacctab`. The contrib hook still lists modules that pycparser no longer has.
- `warn-frozen_overlay.txt` has the usual 155 lines of missing modules: POSIX-only modules, and names that yt-dlp imports from `yt_dlp.utils`, which are functions rather than modules.
- setuptools ends up in the bundle (0.70 MB compressed), because cffi's build helpers import it. Excluding it could save space, but that's untested.

### 6. `--version` in a windowed build

`sys.stdout` is `None` only when the parent passes no handles, as with a double-click or a command typed in a terminal. When stdout is redirected to a pipe or a file, as on a CI runner, `print` works with no extra code. `AttachConsole(ATTACH_PARENT_PROCESS)` followed by opening `CONOUT$` (`spike_console.py`) makes `--version` show up in a terminal too. The stripped build from question 7 exits with 3, which shows whether each way of starting the app passes the exit code on.

| Started with | Output | Exit code seen |
|---|---|---|
| Python `subprocess` with pipes | `0.0.1` | 0, and 3 for the stripped build |
| a `.cmd` file in a new console | nothing | 0, and 3 for the stripped build, so cmd waits |
| a `.cmd` file in a new console, with `--attach-console` | `0.0.1` | 0 |
| Windows PowerShell 5.1 in a new console, `& exe --version --attach-console` | `0.0.1`, printed after PowerShell had moved on | empty |
| Windows PowerShell 5.1 with its stdout piped, `& exe --version` | `0.0.1`, printed after PowerShell had moved on | empty, also for the stripped build |
| Windows PowerShell 5.1, `$out = & exe --version` | nothing. The app's write failed with `OSError: [Errno 22] Invalid argument` | empty |
| `Start-Process -Wait -PassThru` | not captured | 0, and 3 for the stripped build |
| `Start-Process -Wait -PassThru -RedirectStandardOutput <file>` | `0.0.1` in the file | 0 |

Windows PowerShell doesn't wait for a windowed exe, so `$LASTEXITCODE` stays empty. PowerShell 7, the default shell on GitHub's Windows runners, and Git Bash weren't tested; T-030 checks them on the real build.

Recommendation: the smoke tests rely on the exit code, started with `Start-Process -Wait -PassThru`. With `-RedirectStandardOutput` they can also check the printed version, with no console code in the app. `AttachConsole` works, but only helps someone typing `--version` in cmd. In PowerShell the text still lands after the prompt.

### 7. Package metadata

| Build | `sc_mp3-0.0.1.dist-info` in the bundle | `import scmp3` | `--version` |
|---|---|---|---|
| `copy_metadata("sc-mp3")` | yes | works | `0.0.1`, exit 0 |
| without `copy_metadata` (`no-copy-metadata`) | yes | works | `0.0.1`, exit 0 |
| dist-info removed (`no-metadata`) | no | `PackageNotFoundError: No package metadata was found for sc-mp3` | nothing printed, exit 3 |

The frozen app needs the metadata, and `import scmp3` works with it. With PyInstaller 6.22.3, `copy_metadata` turns out to be optional. PyInstaller scans the bytecode for `importlib.metadata.version("<literal>")` calls and collects that metadata itself (`metadata_required()` in `PyInstaller/depend/analysis.py`). That only works while the name stays a literal, so the release spec should keep `copy_metadata("sc-mp3")` as the explicit guarantee.

The metadata of an editable install also carries files that don't belong in a release. `direct_url.json` holds the builder's checkout as a `file:///C:/Users/<name>/...` URL, so a build made on a dev machine would ship the username. `uv_cache.json` holds build timestamps. Either way of collecting the metadata copies both. The spec now drops them from `a.datas` after the analysis, and `--version` still works without them. The other way out is to build from a non-editable install.

### Install path

This wasn't one of the questions. It turned up when the first clean-clone run, made inside a deep temporary folder, lost curl_cffi.

#### Length

The `path-*` scenarios copy the bundle so that its longest file path, curl_cffi's DLL, has an exact length. A one-off sweep filled in between them. Lengths are counted the way Windows counts them, in UTF-16 units of the real path: a character outside the BMP, such as an emoji, counts as two. The limits are on full file paths:

| File | Works up to | Fails from | What fails |
|---|---|---|---|
| `_internal\curl_cffi.libs\libcurl-impersonate-1172405423dc93b7aeed557f2a2ca325.dll` | 250 units | 251 | `import curl_cffi`: `DLL load failed while importing _wrapper: The filename or extension is too long.` yt-dlp leaves curl_cffi out of `Request Handlers` without a warning, and requests that need impersonation fail with `Impersonate target "chrome" is not available`. |
| `_internal\webview\lib\Microsoft.Web.WebView2.WinForms.dll` | 259 units | 260 | The window never opens: `FileNotFoundException: Unable to find assembly`. |
| not identified | | exe folder of 207 units | pythonnet can't start .NET at all: `Failed to create a .NET runtime (coreclr)`, after pywebview tried CoreCLR because .NET Framework failed. |

- For this bundle, that puts the exe's folder at 168 units at most for curl_cffi, 201 for the window, and 206 for .NET. curl_cffi's DLL name holds a hash that changes with each wheel, so the 168 shouldn't be hard-coded. A startup check can work the limit out from the bundle's own files: `len(str(path).encode("utf-16-le")) // 2`.
- An emoji in the folder name confirmed the unit: with the DLL at 250 units (249 Python characters), curl_cffi loaded, and at 251 units it didn't.
- The 250-unit limit for curl_cffi is 9 short of MAX_PATH and isn't explained. It was measured, not worked out.
- `LongPathsEnabled` is 1 on this machine, and it doesn't help.
- pywebview finds its DLLs through `os.path.realpath`, so a mapped drive (`subst`) or a junction doesn't shorten the path it uses.
- A release unzipped into `Downloads` sits far below these limits, but a deep synced folder might not.

#### Characters outside the ANSI code page

With a short path, curl_cffi still breaks when a folder name has characters outside the system's ANSI code page (Windows-1252 here). In a folder named `жук`, curl_cffi loaded, but every request through it failed with `HTTP Error 0: OK`. The same happened with an emoji in the name. A folder named `café` worked, since `é` is in the code page. The single-track SoundCloud extraction worked every time, because it doesn't use curl_cffi.

That would hit a user whose Windows username uses another script and who unzips the app into `Downloads`. curl_cffi passes certifi's `cacert.pem` to libcurl as a file path (`CAINFO`), and libcurl most likely opens it with an ANSI API. Passing the certificates as bytes (`CAINFO_BLOB`) might avoid it, but that's untested. The `name-in-code-page` and `name-outside-code-page` scenarios show both cases.

## Not tested

- A cold boot, Windows 10, a machine without WebView2 (pywebview's MSHTML fallback), Windows on Arm, PowerShell 7 and Git Bash.
- A release downloaded from the internet: SmartScreen, and antivirus scanning on other machines. Here a fresh copy's first launch was about 0.5 s slower.
- Downloading through the overlay: the checks stop at `extract_info`.
