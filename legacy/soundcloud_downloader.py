"""
SoundCloud → MP3 Downloader with Auto Metadata + Go+ Quality
Requirements: pip install yt-dlp mutagen customtkinter Pillow
Also needs ffmpeg installed and on PATH (choco install ffmpeg)

Go+ auth: export cookies.txt from your browser after logging into SoundCloud
using the "Get cookies.txt LOCALLY" Chrome/Firefox extension.
"""

import os
import threading
import urllib.request
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk

# ── Optional deps ─────────────────────────────────────────────────────────────
try:
    import yt_dlp
except ImportError:
    yt_dlp = None

try:
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, COMM, APIC, TPE2, TCON, TRCK
except ImportError:
    ID3 = None

try:
    from PIL import Image
    import io
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# ── Theme ─────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BG      = "#0f0f13"
CARD    = "#1a1a24"
ACCENT  = "#ff5500"
ACCENT2 = "#1db954"
TEXT    = "#f0f0f0"
SUBTEXT = "#888899"
BORDER  = "#2a2a3a"
GOLD    = "#f5c542"

# ── Helpers ───────────────────────────────────────────────────────────────────

def check_dependencies():
    missing = []
    if yt_dlp is None:
        missing.append("yt-dlp")
    if ID3 is None:
        missing.append("mutagen")
    if not PIL_AVAILABLE:
        missing.append("Pillow")
    return missing


def fetch_artwork_bytes(url: str):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read()
    except Exception:
        return None


def describe_format(info: dict) -> str:
    """Return a human-readable quality string from yt-dlp info."""
    abr    = info.get("abr")
    acodec = info.get("acodec", "")
    asr    = info.get("asr")
    parts  = []
    if abr:
        parts.append(f"{int(abr)}kbps")
    if acodec and acodec != "none":
        parts.append(acodec.upper().split(".")[0])
    if asr:
        parts.append(f"{asr // 1000}kHz")
    return "  ".join(parts)


import re as _re

# Matches any "feat." intro keyword
_FEAT_KW = r"(?:feat(?:uring)?\.?|ft\.?|with|w/)"

# Matches a single artist name — stops at separators or closing bracket
_ARTIST   = r"[^,&()\[\]\n]+"

# Matches the full featured-artists block, with or without brackets:
#   (feat. A, B & C)  /  [ft. X and Y]  /  feat. Z
_FEAT_BLOCK = (
    r"[\(\[]?\s*"           # optional opening bracket
    + _FEAT_KW +
    r"\s+"                  # whitespace after keyword
    r"(?:" + _ARTIST + r")" # first artist
    r"(?:"                  # additional artists
        r"\s*(?:,|&|\band\b|\+)\s*"
        r"(?:" + _ARTIST + r")"
    r")*"
    r"\s*[\)\]]?"           # optional closing bracket
)

def _split_artists(raw: str) -> list[str]:
    """Split 'A, B & C and D' into ['A', 'B', 'C', 'D']."""
    parts = _re.split(r"\s*(?:,|&|\band\b|\+)\s*", raw)
    return [p.strip().strip("()[]") for p in parts if p.strip()]


def _extract_features(text: str) -> list[str]:
    """
    Find the first feat. block in `text` and return the featured artist names.
    Returns [] if none found.
    """
    m = _re.search(_FEAT_BLOCK, text, flags=_re.IGNORECASE)
    if not m:
        return []
    block = m.group(0)
    # Strip the keyword itself to get just the names
    names_raw = _re.split(_FEAT_KW, block, maxsplit=1, flags=_re.IGNORECASE)[-1]
    return _split_artists(names_raw)


def _strip_feat_from_title(title: str) -> str:
    """
    Remove the feat. block from a title string and clean up leftover brackets/whitespace.
    'Song Title (feat. X & Y)' → 'Song Title'
    'Song Title ft. X'         → 'Song Title'
    """
    cleaned = _re.sub(_FEAT_BLOCK, "", title, flags=_re.IGNORECASE)
    # Remove empty brackets left behind: "Title ()" → "Title"
    cleaned = _re.sub(r"[\(\[]\s*[\)\]]", "", cleaned)
    return cleaned.strip(" -–—")


def _normalise(name: str) -> str:
    """Lowercase + strip for deduplication comparisons."""
    return name.lower().strip()


def build_display_title(info: dict, artist_in_title: bool) -> str:
    """
    Returns the title string for both the ID3 TIT2 tag and the filename.

    Logic:
      1. Pull main artist from uploader / artist field (before any feat. keyword)
      2. Extract features from the artist field (if present)
      3. Extract features from the raw title (if present)
      4. Merge feature lists, deduplicate (case-insensitive), preserve order
      5. Strip the feat. block from the title so it doesn't appear twice
      6. Build: "Clean Title (MainArtist, Feat1, Feat2)"  or just "Clean Title"
    """
    raw_title    = (info.get("title") or "Unknown").strip()
    artist_field = (info.get("artist") or "").strip()
    uploader     = (info.get("uploader") or info.get("creator") or "").strip()

    if not artist_in_title:
        return raw_title

    # ── Main artist ───────────────────────────────────────────────────────────
    # Strip any feat. block from the artist field to get just the main name
    main_artist = _strip_feat_from_title(
        _re.split(_FEAT_KW, artist_field, maxsplit=1, flags=_re.IGNORECASE)[0]
        if artist_field else uploader
    ).strip() or uploader

    # ── Collect features from artist field ───────────────────────────────────
    feats_from_artist = _extract_features(artist_field) if artist_field else []

    # ── Collect features from title ───────────────────────────────────────────
    feats_from_title = _extract_features(raw_title)

    # ── Merge, deduplicate (preserve order, case-insensitive) ─────────────────
    seen     = {_normalise(main_artist)}
    features = []
    for name in feats_from_artist + feats_from_title:
        key = _normalise(name)
        if key and key not in seen:
            seen.add(key)
            features.append(name)

    # ── Clean base title (remove feat. block so it doesn't double up) ─────────
    base_title = _strip_feat_from_title(raw_title)

    # ── Assemble ──────────────────────────────────────────────────────────────
    all_artists = [main_artist] + features
    artist_str  = ", ".join(a for a in all_artists if a)

    if artist_str:
        return f"{base_title} ({artist_str})"
    return base_title


def sanitize_filename(name: str) -> str:
    """Remove characters that are illegal in Windows filenames."""
    import re
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def embed_metadata(mp3_path: str, info: dict, display_title: str, log):
    if ID3 is None:
        log("⚠  mutagen not installed — skipping metadata")
        return
    try:
        try:
            tags = ID3(mp3_path)
        except Exception:
            tags = ID3()

        artist  = info.get("uploader") or info.get("artist", "")
        album   = info.get("album") or info.get("playlist_title") or display_title
        year    = str(info.get("upload_date", ""))[:4]
        genre   = info.get("genre", "")
        track   = str(info.get("track_number", ""))
        comment = (info.get("description") or "")[:200]

        # Use display_title (possibly with artist appended) as the ID3 title
        tags["TIT2"]      = TIT2(encoding=3, text=display_title)
        if artist:  tags["TPE1"]      = TPE1(encoding=3, text=artist)
        if artist:  tags["TPE2"]      = TPE2(encoding=3, text=artist)
        if album:   tags["TALB"]      = TALB(encoding=3, text=album)
        if year:    tags["TDRC"]      = TDRC(encoding=3, text=year)
        if genre:   tags["TCON"]      = TCON(encoding=3, text=genre)
        if track:   tags["TRCK"]      = TRCK(encoding=3, text=track)
        if comment: tags["COMM::eng"] = COMM(encoding=3, lang="eng", desc="", text=comment)

        # Artwork
        art_url = info.get("thumbnail") or (
            (info.get("thumbnails") or [{}])[-1].get("url")
        )
        if art_url:
            log("🎨  Fetching artwork …")
            art_bytes = fetch_artwork_bytes(art_url)
            if art_bytes:
                if PIL_AVAILABLE:
                    img = Image.open(io.BytesIO(art_bytes)).convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=95)
                    art_bytes = buf.getvalue()
                tags["APIC:"] = APIC(
                    encoding=3, mime="image/jpeg",
                    type=3, desc="Cover", data=art_bytes,
                )
                log("✅  Artwork embedded")
            else:
                log("⚠  Could not fetch artwork")

        tags.save(mp3_path, v2_version=3)
        log(f"✅  Metadata saved → {os.path.basename(mp3_path)}")

    except Exception as e:
        log(f"⚠  Metadata error: {e}")


def download_track(url: str, out_dir: str, cookies_path: str,
                   artist_in_title: bool, log, on_done):
    if yt_dlp is None:
        log("❌  yt-dlp not installed — run: pip install yt-dlp")
        on_done(False, "", "")
        return

    info_holder = {}

    def progress_hook(d):
        if d["status"] == "downloading":
            pct   = d.get("_percent_str", "").strip()
            speed = d.get("_speed_str", "").strip()
            log(f"⬇  Downloading … {pct}  {speed}", replace_last=True)
        elif d["status"] == "finished":
            log("⬇  Download complete, converting …")

    ydl_opts = {
        # Prefer ≥200kbps (Go+ 256kbps AAC), fall back to free 128kbps
        "format": "bestaudio[abr>=200]/bestaudio",
        "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "0",   # VBR best — preserves source quality
        }],
        "progress_hooks": [progress_hook],
        "quiet": True,
        "no_warnings": True,
        "writethumbnail": False,
    }

    if cookies_path and os.path.isfile(cookies_path):
        ydl_opts["cookiefile"] = cookies_path
        log("🍪  Using cookies for Go+ auth")
    else:
        log("ℹ  No cookies — downloading free-tier quality (128kbps)")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            log("🔍  Fetching track info …")
            info = ydl.extract_info(url, download=False)
            info_holder["info"] = info

            display_title = build_display_title(info, artist_in_title)
            quality       = describe_format(info)

            log(f"🎵  {display_title}")
            if quality:
                log(f"📶  Source quality: {quality}")
            if artist_in_title:
                log(f"🏷  Title will include artist name")

            ydl.download([url])

        # Find newest .mp3 in output dir
        mp3_path = None
        for f in os.listdir(out_dir):
            if f.endswith(".mp3"):
                candidate = os.path.join(out_dir, f)
                if mp3_path is None or os.path.getmtime(candidate) > os.path.getmtime(mp3_path):
                    mp3_path = candidate

        if not mp3_path:
            log("❌  Could not find output MP3 file")
            on_done(False, "", "")
            return

        # Rename file to match display_title if artist_in_title is on
        if artist_in_title:
            safe_name = sanitize_filename(display_title) + ".mp3"
            new_path  = os.path.join(out_dir, safe_name)
            if mp3_path != new_path:
                os.replace(mp3_path, new_path)
                mp3_path = new_path
                log(f"✏  Renamed → {os.path.basename(mp3_path)}")

        log(f"💾  Saved: {os.path.basename(mp3_path)}")
        embed_metadata(mp3_path, info_holder["info"], display_title, log)
        on_done(True, mp3_path, quality)

    except Exception as e:
        log(f"❌  Error: {e}")
        on_done(False, "", "")


# ── GUI ───────────────────────────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("SC → MP3")
        self.geometry("660x720")
        self.minsize(560, 620)
        self.configure(fg_color=BG)
        self._log_lines = []
        self._build_ui()
        self._check_deps_on_start()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        # ── Header ────────────────────────────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color=CARD, corner_radius=0, height=64)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(header, text="●", font=("Courier", 22),
                     text_color=ACCENT).grid(row=0, column=0, padx=(20, 4), pady=16)
        ctk.CTkLabel(header, text="●", font=("Courier", 22),
                     text_color=ACCENT2).grid(row=0, column=1, pady=16, sticky="w")
        ctk.CTkLabel(header, text="SoundCloud  →  Spotify MP3",
                     font=ctk.CTkFont(family="Trebuchet MS", size=18, weight="bold"),
                     text_color=TEXT).grid(row=0, column=2, padx=20, pady=16, sticky="e")

        # ── URL + folder card ─────────────────────────────────────────────────
        url_card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=12)
        url_card.grid(row=1, column=0, padx=20, pady=(14, 6), sticky="ew")
        url_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(url_card, text="SoundCloud or YouTube URL",
                     font=ctk.CTkFont(size=12), text_color=SUBTEXT
                     ).grid(row=0, column=0, columnspan=3, padx=16, pady=(12, 3), sticky="w")

        self.url_entry = ctk.CTkEntry(
            url_card,
            placeholder_text="https://soundcloud.com/artist/track  or  https://youtube.com/watch?v=...",
            font=ctk.CTkFont(family="Courier New", size=13),
            fg_color="#12121a", border_color=BORDER, text_color=TEXT, height=40,
        )
        self.url_entry.grid(row=1, column=0, columnspan=3, padx=16, pady=(0, 10), sticky="ew")

        ctk.CTkLabel(url_card, text="Save to",
                     font=ctk.CTkFont(size=12), text_color=SUBTEXT
                     ).grid(row=2, column=0, padx=16, pady=(0, 3), sticky="w")

        self.folder_var = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Music"))
        ctk.CTkEntry(url_card, textvariable=self.folder_var,
                     font=ctk.CTkFont(size=12), fg_color="#12121a",
                     border_color=BORDER, text_color=TEXT, height=36,
                     ).grid(row=3, column=0, columnspan=2, padx=(16, 6), pady=(0, 12), sticky="ew")
        ctk.CTkButton(url_card, text="Browse", width=80, height=36,
                      fg_color=BORDER, hover_color="#3a3a4a", text_color=TEXT,
                      command=self._browse_folder,
                      ).grid(row=3, column=2, padx=(0, 16), pady=(0, 12))

        # ── Options card ──────────────────────────────────────────────────────
        opt_card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=12)
        opt_card.grid(row=2, column=0, padx=20, pady=(0, 6), sticky="ew")
        opt_card.grid_columnconfigure(0, weight=1)

        # Artist-in-title toggle
        row_artist = ctk.CTkFrame(opt_card, fg_color="transparent")
        row_artist.grid(row=0, column=0, padx=16, pady=(12, 4), sticky="ew")
        row_artist.grid_columnconfigure(0, weight=1)

        artist_info = ctk.CTkFrame(row_artist, fg_color="transparent")
        artist_info.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(artist_info,
                     text="Artist name in title",
                     font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT,
                     ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(artist_info,
                     text='e.g.  "Song Title (Artist, Feature1, Feature2)"',
                     font=ctk.CTkFont(size=11), text_color=SUBTEXT,
                     ).grid(row=1, column=0, sticky="w")

        self.artist_title_var = tk.BooleanVar(value=False)
        ctk.CTkSwitch(
            row_artist, text="", variable=self.artist_title_var,
            width=44, button_color=ACCENT2, progress_color="#0f7a30",
        ).grid(row=0, column=1, sticky="e")

        # Divider
        ctk.CTkFrame(opt_card, fg_color=BORDER, height=1
                     ).grid(row=1, column=0, padx=16, pady=4, sticky="ew")

        # Go+ toggle
        row_goplus = ctk.CTkFrame(opt_card, fg_color="transparent")
        row_goplus.grid(row=2, column=0, padx=16, pady=(4, 4), sticky="ew")
        row_goplus.grid_columnconfigure(0, weight=1)

        goplus_info = ctk.CTkFrame(row_goplus, fg_color="transparent")
        goplus_info.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(goplus_info,
                     text="🎧  SoundCloud Go+",
                     font=ctk.CTkFont(size=13, weight="bold"), text_color=GOLD,
                     ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(goplus_info,
                     text="Unlocks 256kbps AAC  (requires cookies.txt from your browser)",
                     font=ctk.CTkFont(size=11), text_color=SUBTEXT,
                     ).grid(row=1, column=0, sticky="w")

        self.go_plus_var = tk.BooleanVar(value=False)
        ctk.CTkSwitch(
            row_goplus, text="", variable=self.go_plus_var,
            width=44, button_color=GOLD, progress_color="#8a7020",
            command=self._toggle_goplus,
        ).grid(row=0, column=1, sticky="e")

        # Cookies row (hidden until Go+ toggled on)
        self.cookies_frame = ctk.CTkFrame(opt_card, fg_color="transparent")
        self.cookies_frame.grid(row=3, column=0, padx=0, pady=(0, 12), sticky="ew")
        self.cookies_frame.grid_columnconfigure(0, weight=1)
        self.cookies_frame.grid_remove()

        ctk.CTkLabel(self.cookies_frame, text="cookies.txt path",
                     font=ctk.CTkFont(size=11), text_color=SUBTEXT,
                     ).grid(row=0, column=0, padx=16, pady=(0, 3), sticky="w")

        self.cookies_var = tk.StringVar()
        ctk.CTkEntry(self.cookies_frame, textvariable=self.cookies_var,
                     placeholder_text="C:\\path\\to\\cookies.txt",
                     font=ctk.CTkFont(family="Courier New", size=12),
                     fg_color="#12121a", border_color=BORDER, text_color=TEXT, height=34,
                     ).grid(row=1, column=0, columnspan=2, padx=(16, 6), sticky="ew")
        ctk.CTkButton(self.cookies_frame, text="Browse", width=80, height=34,
                      fg_color=BORDER, hover_color="#3a3a4a", text_color=TEXT,
                      command=self._browse_cookies,
                      ).grid(row=1, column=2, padx=(0, 16))
        ctk.CTkLabel(self.cookies_frame,
                     text='Install "Get cookies.txt LOCALLY" in Chrome/Firefox → log into SoundCloud → export',
                     font=ctk.CTkFont(size=10), text_color=SUBTEXT,
                     ).grid(row=2, column=0, columnspan=3, padx=16, pady=(5, 0), sticky="w")

        # ── Download button ───────────────────────────────────────────────────
        self.dl_btn = ctk.CTkButton(
            self,
            text="⬇  Download & Tag",
            font=ctk.CTkFont(family="Trebuchet MS", size=15, weight="bold"),
            fg_color=ACCENT, hover_color="#cc4400", text_color="white",
            height=48, corner_radius=10,
            command=self._start_download,
        )
        self.dl_btn.grid(row=3, column=0, padx=20, pady=6, sticky="ew")

        # ── Spotify hint (shown after success) ────────────────────────────────
        self.hint_var = tk.StringVar(value="")
        self.hint_lbl = ctk.CTkLabel(
            self, textvariable=self.hint_var,
            font=ctk.CTkFont(size=11), text_color="#aaffaa",
            wraplength=580, justify="left",
        )
        self.hint_lbl.grid(row=4, column=0, padx=20, pady=(0, 4), sticky="w")

        # ── Log ───────────────────────────────────────────────────────────────
        log_frame = ctk.CTkFrame(self, fg_color=CARD, corner_radius=12)
        log_frame.grid(row=5, column=0, padx=20, pady=(0, 14), sticky="nsew")
        log_frame.grid_rowconfigure(1, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(log_frame, text="Activity Log",
                     font=ctk.CTkFont(size=12), text_color=SUBTEXT,
                     ).grid(row=0, column=0, padx=16, pady=(10, 4), sticky="w")

        self.log_box = ctk.CTkTextbox(
            log_frame,
            font=ctk.CTkFont(family="Courier New", size=12),
            fg_color="#0b0b10", text_color="#c8ffc8", wrap="word", state="disabled",
        )
        self.log_box.grid(row=1, column=0, padx=12, pady=(0, 12), sticky="nsew")

        # ── Status bar ────────────────────────────────────────────────────────
        self.status_var = tk.StringVar(value="Ready")
        ctk.CTkLabel(self, textvariable=self.status_var,
                     font=ctk.CTkFont(size=11), text_color=SUBTEXT,
                     ).grid(row=6, column=0, pady=(0, 8))

    # ── Actions ───────────────────────────────────────────────────────────────

    def _toggle_goplus(self):
        if self.go_plus_var.get():
            self.cookies_frame.grid()
        else:
            self.cookies_frame.grid_remove()

    def _browse_folder(self):
        d = filedialog.askdirectory(initialdir=self.folder_var.get())
        if d:
            self.folder_var.set(d)

    def _browse_cookies(self):
        f = filedialog.askopenfilename(
            title="Select cookies.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if f:
            self.cookies_var.set(f)

    def _check_deps_on_start(self):
        missing = check_dependencies()
        if missing:
            self._log("⚠  Missing packages: " + ", ".join(missing))
            self._log("    Run: pip install " + " ".join(missing))
        else:
            self._log("✅  All dependencies found. Paste a SoundCloud URL and go!")

    def _start_download(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("No URL", "Please paste a SoundCloud URL first.")
            return


        cookies_path = ""
        if self.go_plus_var.get():
            cookies_path = self.cookies_var.get().strip()
            if not os.path.isfile(cookies_path):
                messagebox.showwarning(
                    "Cookies not found",
                    "Go+ is enabled but the cookies.txt file wasn't found.\n"
                    "Point to a valid file or disable the Go+ toggle."
                )
                return

        out_dir = self.folder_var.get().strip()
        os.makedirs(out_dir, exist_ok=True)

        artist_in_title = self.artist_title_var.get()

        self.dl_btn.configure(state="disabled", text="Downloading …")
        self.status_var.set("Working …")
        self.hint_var.set("")
        self._log("─" * 50)

        threading.Thread(
            target=download_track,
            args=(url, out_dir, cookies_path, artist_in_title, self._log, self._on_done),
            daemon=True,
        ).start()

    def _on_done(self, success: bool, path: str = "", quality: str = ""):
        self.after(0, self._finish_ui, success, path, quality)

    def _finish_ui(self, success, path, quality):
        self.dl_btn.configure(state="normal", text="⬇  Download & Tag")
        if success:
            q = f"  [{quality}]" if quality else ""
            self.status_var.set(f"✅  Done!{q}  →  {os.path.basename(path)}")
            self._log("🎉  Done!")
            self.hint_var.set(
                "To refresh in Spotify: remove the track from your playlist → "
                "Settings → Local Files → toggle folder off & on → re-add the track."
            )
        else:
            self.status_var.set("❌  Failed — see log above")

    # ── Logging ───────────────────────────────────────────────────────────────

    def _log(self, msg: str, replace_last: bool = False):
        self.after(0, self._write_log, msg, replace_last)

    def _write_log(self, msg: str, replace_last: bool):
        self.log_box.configure(state="normal")
        if replace_last and self._log_lines:
            self.log_box.delete("end-2l", "end-1c")
            self._log_lines[-1] = msg
        else:
            self._log_lines.append(msg)
        if not replace_last:
            self.log_box.insert("end", msg + "\n")
        else:
            self.log_box.insert("end-1c", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")


if __name__ == "__main__":
    app = App()
    app.mainloop()
