#!/usr/bin/env bash
# Build sc-mp3's small LGPL ffmpeg from pinned source tarballs.
#
# Usage:
#   build.sh fetch <sources-dir>                       download missing tarballs, check every SHA-256
#   build.sh check-signature <sources-dir>             check FFmpeg's tarball against its signature
#   build.sh build <target> <sources-dir> <work-dir>   build into <work-dir>/dist
#
# Targets: windows-x64, cross-compiled on Linux with MinGW-w64 (apt: mingw-w64 nasm pkg-config),
# and macos-arm64 or macos-x64, each built on a Mac of that architecture (brew: pkgconf, and nasm
# for Intel). LAME's configure stops without pkg-config, though nothing here uses a .pc file.
set -euo pipefail

FFMPEG_VERSION=9.0.2
LAME_VERSION=4.0
# The oldest macOS the app supports (TECH_SPEC §19).
MACOS_MIN=12.0
# FFmpeg's release signing key, kept next to this script as ffmpeg-release-key.asc.
FFMPEG_KEY_FINGERPRINT=FCF986EA15E6E293A5644F10B4322F04D67658D8
# The only DLLs ffmpeg.exe may import, all part of Windows. bcrypt.dll was in a build without
# HTTPS. Any other DLL fails the build until it's checked and added here.
WINDOWS_DLLS=(bcrypt.dll CRYPT32.dll KERNEL32.dll msvcrt.dll ncrypt.dll Secur32.dll SHELL32.dll WS2_32.dll)

# Every tarball the build uses: file name, SHA-256 and where to download it.
SOURCES=(
  "ffmpeg-$FFMPEG_VERSION.tar.xz 8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz"
  "lame-$LAME_VERSION.tar.gz 3df5124d5ad3a98312ffd7ba6a9b36230e4f8a3e66d3ce0f425e336c32d216eb https://downloads.sourceforge.net/project/lame/lame/$LAME_VERSION/lame-$LAME_VERSION.tar.gz"
)

# Everything off, then only what sc-mp3 runs ffmpeg for (TECH_SPEC §9). Without --enable-gpl
# and --enable-version3 the result is LGPL-2.1-or-later.
CONFIGURE_FLAGS=(
  --disable-everything
  # Autodetection would link whatever the build machine has, so every library is named here.
  --disable-autodetect
  --disable-programs --enable-ffmpeg
  --disable-doc --disable-debug
  --disable-avdevice --disable-swscale
  --extra-version=sc-mp3
  --enable-libmp3lame
  --enable-encoder=libmp3lame
  # What a download can hold: AAC or ALAC in M4A, Opus in WebM or Ogg, Vorbis, FLAC, MP3, WAV, AIFF.
  --enable-decoder=aac,alac,flac,mp3float,opus,vorbis
  --enable-decoder=pcm_s16le,pcm_s24le,pcm_s32le,pcm_f32le,pcm_f64le,pcm_u8
  --enable-decoder=pcm_s16be,pcm_s24be,pcm_s32be,pcm_f32be
  --enable-demuxer=aac,aiff,flac,matroska,mov,mp3,ogg,wav
  --enable-parser=aac,flac,mpegaudio,opus,vorbis
  # yt-dlp's HLS fixup copies MPEG-TS audio into MP4 with aac_adtstoasc.
  --enable-demuxer=mpegts
  --enable-bsf=aac_adtstoasc
  # ffmpeg picks the ipod muxer for .m4a, and yt-dlp asks for mp4 by name.
  --enable-muxer=mp3,mp4,ipod
  # ffmpeg inserts aresample when a decoder's sample format isn't one LAME takes.
  --enable-filter=aresample
  --enable-protocol=file,pipe
  # yt-dlp hands ffmpeg the HLS playlists its own downloader can't handle, AES-128 ones included.
  # HTTPS uses the system's TLS, which set_target turns on.
  --enable-demuxer=hls
  --enable-protocol=http,https,httpproxy,tcp,tls,crypto,data
)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

fail() {
  echo "error: $*" >&2
  exit 1
}

sha256_of() {
  if command -v sha256sum >/dev/null; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

# Download any missing tarball, then check every one against its pinned SHA-256.
fetch_sources() {
  local sources="$1" entry name sha url actual
  mkdir -p "$sources"
  for entry in "${SOURCES[@]}"; do
    read -r name sha url <<<"$entry"
    if [ ! -f "$sources/$name" ]; then
      echo "Downloading $url"
      curl --fail --location --silent --show-error --retry 3 --output "$sources/$name.partial" "$url"
      mv "$sources/$name.partial" "$sources/$name"
    fi
    actual="$(sha256_of "$sources/$name")"
    if [ "$actual" != "$sha" ]; then
      fail "$name has SHA-256 $actual, not the pinned $sha. Delete it and fetch again, or check the pin in build.sh."
    fi
    echo "$name: SHA-256 matches"
  done
}

# Check the FFmpeg tarball against FFmpeg's detached signature, made with the pinned key.
check_signature() {
  local sources="$1" tarball="ffmpeg-$FFMPEG_VERSION.tar.xz" keyring status
  curl --fail --location --silent --show-error --retry 3 \
    --output "$sources/$tarball.asc" "https://ffmpeg.org/releases/$tarball.asc"
  keyring="$(mktemp -d)"
  gpg --homedir "$keyring" --quiet --import "$SCRIPT_DIR/ffmpeg-release-key.asc"
  status="$(gpg --homedir "$keyring" --status-fd 1 --verify "$sources/$tarball.asc" "$sources/$tarball" 2>/dev/null || true)"
  rm -rf "$keyring"
  # VALIDSIG's last field is the primary key's fingerprint.
  if ! grep -Eq "^\[GNUPG:\] VALIDSIG .* $FFMPEG_KEY_FINGERPRINT\$" <<<"$status"; then
    fail "$tarball isn't signed by FFmpeg's release key $FFMPEG_KEY_FINGERPRINT."
  fi
  echo "$tarball: signed by FFmpeg's release key $FFMPEG_KEY_FINGERPRINT"
}

cpu_count() {
  getconf _NPROCESSORS_ONLN 2>/dev/null || sysctl -n hw.ncpu
}

# Settings for one target: LAME's configure options, FFmpeg's, the TLS library configure must
# find, and the binary's name. macOS ships bash 3.2, where an empty array breaks set -u, so
# every list here has at least one entry.
set_target() {
  case "$1" in
    windows-x64)
      local cross=x86_64-w64-mingw32
      LAME_TARGET_FLAGS=(--host="$cross")
      # -static keeps libgcc and the C runtime helpers out of separate DLLs.
      FFMPEG_TARGET_FLAGS=(
        --target-os=mingw32 --arch=x86_64 --enable-cross-compile --cross-prefix="$cross-"
        --enable-w32threads --enable-schannel --extra-ldflags=-static
      )
      TLS_LIBRARY=SCHANNEL
      BINARY=ffmpeg.exe
      ;;
    macos-arm64 | macos-x64)
      local machine
      machine="$([ "$1" = macos-arm64 ] && echo arm64 || echo x86_64)"
      [ "$(uname -s)" = Darwin ] || fail "$1 builds only on a Mac."
      [ "$(uname -m)" = "$machine" ] || fail "$1 builds only on a Mac with an $machine processor."
      # clang reads this for every compile and link, LAME's included.
      export MACOSX_DEPLOYMENT_TARGET="$MACOS_MIN"
      # LAME's config.sub is from 2018 and knows Apple silicon only as aarch64.
      LAME_TARGET_FLAGS=(--build="$([ "$machine" = arm64 ] && echo aarch64 || echo x86_64)-apple-darwin")
      FFMPEG_TARGET_FLAGS=(
        --cc=clang --enable-pthreads --enable-securetransport
        --extra-cflags="-mmacosx-version-min=$MACOS_MIN"
        --extra-ldflags="-mmacosx-version-min=$MACOS_MIN"
      )
      TLS_LIBRARY=SECURETRANSPORT
      BINARY=ffmpeg
      ;;
    *)
      fail "Unknown target $1. Use windows-x64, macos-arm64 or macos-x64."
      ;;
  esac
}

build_lame() {
  local work="$1" sources="$2" prefix="$3"
  tar -xzf "$sources/lame-$LAME_VERSION.tar.gz" -C "$work"
  (
    cd "$work/lame-$LAME_VERSION"
    # Only the static library: no lame program, and no MP3 decoder, which ffmpeg has its own of.
    ./configure "${LAME_TARGET_FLAGS[@]}" --prefix="$prefix" \
      --enable-static --disable-shared --disable-frontend --disable-decoder \
      --disable-dependency-tracking
    make -j"$(cpu_count)"
    make install
  )
}

build_ffmpeg() {
  local work="$1" sources="$2" prefix="$3"
  tar -xJf "$sources/ffmpeg-$FFMPEG_VERSION.tar.xz" -C "$work"
  (
    cd "$work/ffmpeg-$FFMPEG_VERSION"
    ./configure "${CONFIGURE_FLAGS[@]}" "${FFMPEG_TARGET_FLAGS[@]}" \
      --extra-cflags="-I$prefix/include" --extra-ldflags="-L$prefix/lib"
    # configure turns these on only from the flags above. A GPL, LGPL 3 or nonfree build must stop
    # here, since everything else calls this one LGPL-2.1-or-later.
    grep -qx "#define CONFIG_GPL 0" config.h || fail "The configuration turned on GPL code."
    grep -qx "#define CONFIG_VERSION3 0" config.h || fail "The configuration turned on (L)GPL 3."
    grep -qx "#define CONFIG_NONFREE 0" config.h || fail "The configuration turned on nonfree code."
    grep -qx "#define CONFIG_$TLS_LIBRARY 1" config.h || fail "configure didn't find $TLS_LIBRARY."
    check_components
    make -j"$(cpu_count)"
  )
}

# configure quietly drops a component when something it needs is missing, so check each one.
check_components() {
  local flag kind names name missing=""
  for flag in "${CONFIGURE_FLAGS[@]}"; do
    [[ "$flag" =~ ^--enable-(encoder|decoder|demuxer|muxer|parser|bsf|filter|protocol)=(.+)$ ]] || continue
    kind="$(tr '[:lower:]' '[:upper:]' <<<"${BASH_REMATCH[1]}")"
    IFS=, read -ra names <<<"${BASH_REMATCH[2]}"
    for name in "${names[@]}"; do
      name="$(tr '[:lower:]' '[:upper:]' <<<"$name")"
      grep -qx "#define CONFIG_${name}_$kind 1" config_components.h || missing="$missing ${name}_$kind"
    done
  done
  [ -z "$missing" ] || fail "configure left out:$missing. ffbuild/config.log says why."
}

# Fail on any DLL in the list that isn't in WINDOWS_DLLS, ignoring case as Windows does. Anything
# else, like a MinGW runtime DLL or a shared zlib1.dll, wouldn't be on a user's PC.
check_windows_dlls() {
  local allowed dll extra=""
  allowed=" $(printf '%s ' "${WINDOWS_DLLS[@]}" | tr '[:upper:]' '[:lower:]')"
  while read -r dll; do
    case "$allowed" in
      *" $(tr '[:upper:]' '[:lower:]' <<<"$dll") "*) ;;
      *) extra="$extra $dll" ;;
    esac
  done <"$1"
  [ -z "$extra" ] || fail "ffmpeg.exe needs DLLs that aren't part of Windows:$extra"
}

# Fail when the binary needs a library the system doesn't have, and keep the list for the report.
check_linking() {
  local target="$1" binary="$2" report="$3"
  case "$target" in
    windows-x64)
      x86_64-w64-mingw32-objdump -p "$binary" | sed -n 's/^\s*DLL Name: //p' >"$report"
      # An empty list would mean objdump's output changed, not that ffmpeg.exe needs nothing.
      [ -s "$report" ] || fail "objdump listed no DLLs for ffmpeg.exe."
      check_windows_dlls "$report"
      ;;
    macos-*)
      otool -L "$binary" | tail -n +2 | sed 's/^[[:space:]]*//' >"$report"
      if grep -Ev '^/usr/lib/|^/System/' "$report"; then
        fail "ffmpeg links a library outside macOS itself."
      fi
      codesign --force --sign - "$binary"
      local minos
      minos="$(otool -l "$binary" | awk '/LC_BUILD_VERSION/ { found = 1 } found && $1 == "minos" { print $2; exit }')"
      [ "$minos" = "$MACOS_MIN" ] || fail "ffmpeg needs macOS $minos, not $MACOS_MIN."
      echo "minos $minos, $(lipo -archs "$binary")" >>"$report"
      ;;
  esac
}

build() {
  local target="$1" sources="$2" work="$3"
  set_target "$target"
  fetch_sources "$sources"
  sources="$(cd "$sources" && pwd)"
  mkdir -p "$work"
  work="$(cd "$work" && pwd)"
  local prefix="$work/prefix" dist="$work/dist"
  rm -rf "$work/lame-$LAME_VERSION" "$work/ffmpeg-$FFMPEG_VERSION" "$prefix" "$dist"
  mkdir -p "$prefix" "$dist"
  # pkg-config sees only what this build installs, never the build machine's own libraries.
  export PKG_CONFIG_LIBDIR="$prefix/lib/pkgconfig"
  unset PKG_CONFIG_PATH

  build_lame "$work" "$sources" "$prefix"
  build_ffmpeg "$work" "$sources" "$prefix"

  # The Makefile strips ffmpeg_g's symbols into ffmpeg.
  cp "$work/ffmpeg-$FFMPEG_VERSION/$BINARY" "$dist/$BINARY"
  check_linking "$target" "$dist/$BINARY" "$dist/linking.txt"
  echo "Built $dist/$BINARY, $(wc -c <"$dist/$BINARY" | tr -d ' ') bytes, linking:"
  cat "$dist/linking.txt"
}

main() {
  case "${1:-}" in
    fetch)
      [ $# -eq 2 ] || fail "Usage: build.sh fetch <sources-dir>"
      fetch_sources "$2"
      ;;
    check-signature)
      [ $# -eq 2 ] || fail "Usage: build.sh check-signature <sources-dir>"
      check_signature "$2"
      ;;
    build)
      [ $# -eq 4 ] || fail "Usage: build.sh build <target> <sources-dir> <work-dir>"
      build "$2" "$3" "$4"
      ;;
    *)
      fail "Usage: build.sh fetch|check-signature|build ... (see the top of build.sh)"
      ;;
  esac
}

main "$@"
