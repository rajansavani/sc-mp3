# Third-party notices

sc-mp3 is licensed under GPL-3.0-or-later. Its license is in LICENSE, which the Windows release zip holds as LICENSE.txt.

The Windows release zip also holds the software below, each under its own license. The list and the versions come from the files in the 0.1.0 build. Paths are inside the unzipped `sc-mp3` folder.

The `licenses` folder, next to `sc-mp3.exe`, holds the license files that each bundled package, Python, PyInstaller and ffmpeg ship, one folder each.

## ffmpeg

sc-mp3 runs ffmpeg as a separate program, `_internal\ffmpeg.exe`, to convert audio.

- Build: FFmpeg 9.0.2, the "essentials" static build for 64-bit Windows from gyan.dev. It reports its version as `9.0.2-essentials_build-www.gyan.dev`.
- License: GPL-3.0-or-later.
- FFmpeg 9.0.2 source: https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz, SHA-256 `8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e`. Each release on GitHub has a copy attached.
- gyan.dev's builds page: https://www.gyan.dev/ffmpeg/builds/
- The archive the release takes `ffmpeg.exe` from: https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip, SHA-256 `60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba`. Its LICENSE (the GPL) and its README.txt (the full build configuration) are in `licenses\ffmpeg`.

The build's configuration, from `ffmpeg -buildconf`, turns on these libraries and interfaces: AMF, aom, AviSynth+, bzip2, cairo, CUDA, D3D11VA, D3D12VA, DXVA2, ffnvcodec, fontconfig, FreeType, FriBidi, GMP, GnuTLS, gsm, HarfBuzz, iconv, LAME, libass, libgme, libopencore-amrnb, libopencore-amrwb, libopenmpt, libssh, libtheora, libvorbis, libvpx, libwebp, libxml2, liblzma, Media Foundation, NVDEC, NVENC, OpenAL Soft, OpenJPEG, Opus, Rubber Band, SDL, Speex, SRT, VAAPI, vid.stab, VMAF, vo-amrwbenc, VPL, x264, x265, Xvid, ZeroMQ, zimg and zlib.

The versions the build's README.txt lists:

```
AMF v1.5.2-2-gc35f613
aom v3.15.0-25-g023743a7d7
AviSynthPlus v3.7.5-383-g5c82777b
cairo 1.18.5
ffnvcodec n13.1.15.0-1-geddcea9
freetype VER-2-14-3
fribidi v1.0.16-19-gc928e4c
gsm 1.0.24
harfbuzz 14.4.0-108-g64e2b532
lame 3.100
libass 0.17.5-8-gb2fe9d8
libgme 0.6.6
libopencore-amrnb 0.1.6
libopencore-amrwb 0.1.6
libssh 0.12.2
libtheora v1.2.0
libwebp v1.6.0-249-g1f34635
openal-soft 1.25.2-48-g7532541
openjpeg2 2.5.4
openmpt libopenmpt-0.6.30-3-g6c0a06bc
opus v1.6.1-68-g503d81b1
rubberband v4.0.0
SDL release-2.32.0-234-gb90ac9502
speex Speex-1.2.1-51-g0589522
srt v1.5.7-7-gcae8f62
VAAPI 2.25.0.
vidstab v1.1.2-214-ge2445c4
vmaf v3.2.0-13-gf85a8536
vo-amrwbenc 0.1.3
vorbis v1.3.7-37-g1b75110b
VPL 2.17
vpx v1.17.0-57-gd2413e2ca
x264 v0.165.3223
x265 4.3-45-g116b875
xvid v1.3.7
zeromq 4.3.5
zimg release-3.0.6-253-g67e0603
```

gyan.dev doesn't publish its build scripts or the sources of these libraries. A later release of sc-mp3 will ship an ffmpeg it builds itself, with the full source of FFmpeg and every library published.

## Python

- Python 3.13.7: `_internal\python313.dll`, `_internal\python3.dll` and the standard library. PSF-2.0. https://docs.python.org/3.13/license.html
- Libraries that come with Python: OpenSSL 3.5.3 (Apache-2.0), SQLite 3.50.4 (public domain), zlib 1.3.1 (Zlib), Expat 2.7.1 (MIT), mpdecimal 4.0.0 (BSD-2-Clause), bzip2 1.0.8 (bzip2-1.0.6), libffi (MIT), liblzma from xz (public domain) and `HACL*` (MIT). Python's own license file is in `licenses\python`.
- PyInstaller 6.22.3's bootloader, which is `sc-mp3.exe` itself. GPL-2.0-or-later with PyInstaller's bootloader exception, which allows it to ship with programs under any license.
- The Microsoft Visual C++ runtime 14.44, `_internal\VCRUNTIME140.dll` and `_internal\VCRUNTIME140_1.dll`. Microsoft's redistributable code, under the Visual Studio license terms. https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files

## Python packages

Each package's page, with its source and license, is at `https://pypi.org/project/<package>/<version>/`. Its license files are in `licenses\<package>`.

| Package | Version | License |
|---|---|---|
| bottle | 0.13.4 | MIT |
| brotli | 1.2.0 | MIT |
| certifi | 2026.10.18 | MPL-2.0 |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| clr_loader | 0.3.1 | MIT |
| curl_cffi | 0.16.3 | MIT |
| idna | 3.20 | BSD-3-Clause |
| jaraco.context | 6.1.2 | MIT |
| jaraco.functools | 4.6.0 | MIT |
| more-itertools | 11.2.1 | MIT |
| mutagen | 1.48.1 | GPL-2.0-or-later |
| packaging | 26.3 | Apache-2.0 or BSD-2-Clause |
| pillow | 13.0.0 | MIT-CMU |
| platformdirs | 4.13.4 | MIT |
| proxy_tools | 0.1.0 | BSD-2-Clause, from its repository's LICENSE.txt; its package metadata says MIT |
| pycparser | 3.11 | BSD-3-Clause |
| pycryptodomex | 3.24.1 | BSD-2-Clause, and public domain |
| pythonnet | 3.2.1 | MIT |
| pywebview | 6.2.1 | BSD-3-Clause |
| requests | 2.34.2 | Apache-2.0 |
| setuptools | 84.0.0 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| urllib3 | 2.8.0 | MIT |
| websockets | 17.2 | BSD-3-Clause |
| yt-dlp | 2026.8.19 | Unlicense |
| yt-dlp-ejs | 0.8.0 | Unlicense, MIT and ISC |

Some of these packages include other libraries:

- Pillow: libjpeg-turbo 3.2.0 (IJG and BSD-3-Clause), OpenJPEG 2.5.4 (BSD-2-Clause), libtiff 4.7.2 (libtiff), liblzma from xz 5.8.4 (0BSD), zlib-ng 2.3.3 (Zlib), Little CMS 2.19.1 (MIT), libwebp 1.6.0 (BSD-3-Clause), libavif 1.4.2 (BSD-2-Clause), dav1d 1.5.3 (BSD-2-Clause), aom 3.14.1 (BSD-2-Clause, with the Alliance for Open Media Patent License 1.0) and libyuv (BSD-3-Clause). Pillow's wheel has their license texts in its LICENSE file.
- curl_cffi, in `_internal\curl_cffi.libs`: libcurl-impersonate, which is curl 8.21.0 (curl) with curl-impersonate's changes (MIT), BoringSSL (Apache-2.0), nghttp2 1.63.0, ngtcp2 1.20.0 and nghttp3 1.15.0 (MIT), brotli 1.2.0 (MIT), zstd 1.5.7 (BSD-3-Clause or GPL-2.0-only) and zlib 1.3.1 (Zlib).
- pywebview, in `_internal\webview\lib`: Microsoft's WebView2 SDK 1.0.3856.49 (`Microsoft.Web.WebView2.Core.dll`, `Microsoft.Web.WebView2.WinForms.dll` and `WebView2Loader.dll`), under Microsoft's BSD-3-Clause-style license. https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.3856.49/License
- setuptools: backports.tarfile 1.2.0, jaraco.text 4.0.0, tomli 2.4.0 and wheel 0.46.3 (MIT), and packaging 26.0 (Apache-2.0 or BSD-2-Clause).
- certifi: Mozilla's list of certificate authorities, `_internal\certifi\cacert.pem` (MPL-2.0).

## The interface

- Preact 10.29.8 (MIT) and htm 3.1.1 (Apache-2.0). Their licenses are in `_internal\scmp3\web\vendor`.
- The fonts Instrument Sans and JetBrains Mono, from Fontsource's 5.3.0 packages (OFL-1.1). Their licenses are in `_internal\scmp3\web\fonts`.

## Unicode emoji data

sc-mp3 tells emoji from other symbols with Unicode's emoji data, `_internal\scmp3\core\suggest\emoji-data.txt`. It's Emoji 17.0's `emoji-data.txt`, copied unchanged from https://www.unicode.org/Public/17.0.0/ucd/emoji/emoji-data.txt. It's under the Unicode License v3:

```
UNICODE LICENSE V3

COPYRIGHT AND PERMISSION NOTICE

Copyright © 1991-2026 Unicode, Inc.

NOTICE TO USER: Carefully read the following legal agreement. BY
DOWNLOADING, INSTALLING, COPYING OR OTHERWISE USING DATA FILES, AND/OR
SOFTWARE, YOU UNEQUIVOCALLY ACCEPT, AND AGREE TO BE BOUND BY, ALL OF THE
TERMS AND CONDITIONS OF THIS AGREEMENT. IF YOU DO NOT AGREE, DO NOT
DOWNLOAD, INSTALL, COPY, DISTRIBUTE OR USE THE DATA FILES OR SOFTWARE.

Permission is hereby granted, free of charge, to any person obtaining a
copy of data files and any associated documentation (the "Data Files") or
software and any associated documentation (the "Software") to deal in the
Data Files or Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, and/or sell
copies of the Data Files or Software, and to permit persons to whom the
Data Files or Software are furnished to do so, provided that either (a)
this copyright and permission notice appear with all copies of the Data
Files or Software, or (b) this copyright and permission notice appear in
associated Documentation.

THE DATA FILES AND SOFTWARE ARE PROVIDED "AS IS", WITHOUT WARRANTY OF ANY
KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT OF
THIRD PARTY RIGHTS.

IN NO EVENT SHALL THE COPYRIGHT HOLDER OR HOLDERS INCLUDED IN THIS NOTICE
BE LIABLE FOR ANY CLAIM, OR ANY SPECIAL INDIRECT OR CONSEQUENTIAL DAMAGES,
OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS,
WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION,
ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THE DATA
FILES OR SOFTWARE.

Except as contained in this notice, the name of a copyright holder shall
not be used in advertising or otherwise to promote the sale, use or other
dealings in these Data Files or Software without prior written
authorization of the copyright holder.
```

## Downloaded later, not in the zip

- Deno (MIT), https://github.com/denoland/deno. It's the YouTube support that sc-mp3 offers to download, about 40 MB, when you add your first YouTube link. yt-dlp uses it to read YouTube pages.
- yt-dlp (Unlicense). Settings › Engine and updates can download a newer release than the one in the zip.
