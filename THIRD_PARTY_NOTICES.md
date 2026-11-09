# Third-party notices

sc-mp3 is licensed under GPL-3.0-or-later. Its license is in LICENSE, which each release zip holds as LICENSE.txt.

The release zips for Windows and Mac also hold the software below, each under its own license. The list and the versions come from the files in the release builds. Paths are inside the unzipped `sc-mp3` folder, as in the Windows zip. In the Mac zips, what the Windows zip keeps in `_internal` is inside `sc-mp3.app/Contents`, in `Frameworks` and `Resources`.

The `licenses` folder, next to `sc-mp3.exe` or `sc-mp3.app`, holds the license files that each bundled package, Python, PyInstaller and ffmpeg ship, one folder each.

## ffmpeg

sc-mp3 runs ffmpeg as a separate program to convert audio. The Windows and Mac releases bundle an ffmpeg that sc-mp3 builds itself, from FFmpeg 9.0.2 and LAME 4.0, with only what sc-mp3 runs it for. On Windows it's `_internal\ffmpeg.exe`, and on a Mac `sc-mp3.app/Contents/Frameworks/ffmpeg`. It reports its version as `9.0.2-sc-mp3`.

- License: LGPL-2.1-or-later. FFmpeg is under the GNU Lesser General Public License version 2.1 or later, and LAME under the GNU Library General Public License version 2 or later. The build leaves out every part of FFmpeg that the GPL, version 3 of the LGPL or a nonfree license covers.
- `licenses\ffmpeg` holds the build's README.txt, with its full configure options, and the license texts: FFmpeg's `COPYING.LGPLv2.1` and `LICENSE.md` in `FFmpeg`, and LAME's `COPYING` and `LICENSE` in `LAME`.
- On Windows, `ffmpeg.exe` also holds parts of the MinGW-w64 runtime and of GCC's libgcc, from the Ubuntu 24.04 packages it was cross-compiled with. Their notices from those packages are in `licenses\ffmpeg\MinGW-w64` and `licenses\ffmpeg\GCC`. The MinGW-w64 runtime is mostly under the Zope Public License 2.1, and its notice lists the permissive terms of the few files that differ. libgcc is under GPL-3.0-or-later with the GCC Runtime Library Exception 3.1, which lets it be part of programs under any license.

The source is in the sc-mp3 release ffmpeg-9.0.2-1, https://github.com/rajansavani/sc-mp3/releases/tag/ffmpeg-9.0.2-1. It holds:

- FFmpeg 9.0.2's source, `ffmpeg-9.0.2.tar.xz`, SHA-256 `8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e`, with FFmpeg's signature for it and FFmpeg's release key. It's the tarball from https://ffmpeg.org/releases/, unchanged.
- LAME 4.0's source, `lame-4.0.tar.gz`, SHA-256 `3df5124d5ad3a98312ffd7ba6a9b36230e4f8a3e66d3ce0f425e336c32d216eb`. It's the tarball LAME publishes on SourceForge, unchanged. LAME's site is https://lame.sourceforge.io.
- `build.sh`, the script that built each ffmpeg from those two tarballs. The release's notes say how to run it.
- The zips the app releases take ffmpeg from. Each holds the program, the README.txt and the license texts above.
  - Windows: `ffmpeg-9.0.2-sc-mp3-windows-x64.zip`, SHA-256 `26c0727c28fe66b183b1583ba40f131eda988d6a01cafadbdaf78d479fb6744e`
  - Apple silicon: `ffmpeg-9.0.2-sc-mp3-macos-arm64.zip`, SHA-256 `2f11f1686348ba54a8aef101b5a74ee2e19e7c9cba207caba5d813af4bfcd46e`
  - Intel Macs: `ffmpeg-9.0.2-sc-mp3-macos-x64.zip`, SHA-256 `24028bcc7515235aa328f4a7a1d30217215d04c5aa2f127a185643ef91074880`

Each app release on GitHub also has copies of the two source tarballs and `build.sh` attached.

## Python

- Python 3.13.7: `_internal\python313.dll`, `_internal\python3.dll` and the standard library. PSF-2.0. https://docs.python.org/3.13/license.html
- Libraries that come with Python: OpenSSL 3.5.3 (Apache-2.0), SQLite 3.50.4 (public domain), zlib 1.3.1 (Zlib), Expat 2.7.1 (MIT), mpdecimal 4.0.0 (BSD-2-Clause), bzip2 1.0.8 (bzip2-1.0.6), libffi (MIT), liblzma from xz (public domain) and `HACL*` (MIT). Python's own license file is in `licenses\python`.
- On a Mac, Python 3.13.7 is `sc-mp3.app/Contents/Frameworks/libpython3.13.dylib` and the standard library. It has OpenSSL 3.5.3, SQLite 3.50.4, Expat 2.6.3, mpdecimal 4.0.0, bzip2 1.0.8, libffi, liblzma from xz 5.2.12 and `HACL*` built in, under the licenses above, and uses the zlib that comes with macOS.
- PyInstaller 6.22.3's bootloader, which is `sc-mp3.exe` itself, or `sc-mp3.app/Contents/MacOS/sc-mp3` on a Mac. GPL-2.0-or-later with PyInstaller's bootloader exception, which allows it to ship with programs under any license.
- In the Windows release, the Microsoft Visual C++ runtime 14.44, `_internal\VCRUNTIME140.dll` and `_internal\VCRUNTIME140_1.dll`. Microsoft's redistributable code, under the Visual Studio license terms. https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files

## Python packages

Each package's page, with its source and license, is at `https://pypi.org/project/<package>/<version>/`. Its license files are in `licenses\<package>`.

| Package | Version | License |
|---|---|---|
| bottle | 0.13.4 | MIT |
| brotli | 1.2.0 | MIT |
| certifi | 2026.10.23 | MPL-2.0 |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| clr_loader | 0.3.1 | MIT |
| curl_cffi | 0.16.3 | MIT |
| idna | 3.20 | BSD-3-Clause |
| jaraco.classes | 3.4.0 | MIT |
| jaraco.context | 6.1.2 | MIT |
| jaraco.functools | 4.6.0 | MIT |
| keyring | 25.7.0 | MIT |
| more-itertools | 11.2.1 | MIT |
| mutagen | 1.48.1 | GPL-2.0-or-later |
| packaging | 26.3 | Apache-2.0 or BSD-2-Clause |
| pillow | 13.0.0 | MIT-CMU |
| platformdirs | 4.13.4 | MIT |
| proxy_tools | 0.1.0 | BSD-2-Clause, from its repository's LICENSE.txt; its package metadata says MIT |
| pycparser | 3.11 | BSD-3-Clause |
| pycryptodomex | 3.24.1 | BSD-2-Clause, and public domain |
| pyobjc-core | 12.2.2 | MIT |
| pyobjc-framework-Cocoa | 12.2.2 | MIT |
| pyobjc-framework-UniformTypeIdentifiers | 12.2.2 | MIT |
| pyobjc-framework-WebKit | 12.2.2 | MIT |
| pythonnet | 3.2.1 | MIT |
| pywebview | 6.2.1 | BSD-3-Clause |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause |
| requests | 2.34.2 | Apache-2.0 |
| setuptools | 84.0.0 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| urllib3 | 2.8.0 | MIT |
| websockets | 17.2 | BSD-3-Clause |
| yt-dlp | 2026.10.26 | Unlicense |
| yt-dlp-ejs | 0.8.0 | Unlicense, MIT and ISC |

clr_loader and pythonnet are in the Windows release only, and the four pyobjc packages in the Mac releases only. pyobjc-core and pyobjc-framework-UniformTypeIdentifiers ship no license file, so the files in their `licenses` folders come from their 12.2.2 source releases.

Some of these packages include other libraries:

- Pillow: libjpeg-turbo 3.2.0 (IJG and BSD-3-Clause), OpenJPEG 2.5.4 (BSD-2-Clause), libtiff 4.7.2 (libtiff), liblzma from xz 5.8.4 (0BSD), zlib-ng 2.3.3 (Zlib), Little CMS 2.19.1 (MIT), libwebp 1.6.0 (BSD-3-Clause), libavif 1.4.2 (BSD-2-Clause), dav1d 1.5.3 (BSD-2-Clause), aom 3.14.1 (BSD-2-Clause, with the Alliance for Open Media Patent License 1.0) and libyuv (BSD-3-Clause). On a Mac it also has libxcb and libXau (MIT). Pillow's wheel has their license texts in its LICENSE file.
- curl_cffi, in `_internal\curl_cffi.libs`: libcurl-impersonate, which is curl 8.21.0 (curl) with curl-impersonate's changes (MIT), BoringSSL (Apache-2.0), nghttp2 1.63.0, ngtcp2 1.20.0 and nghttp3 1.15.0 (MIT), brotli 1.2.0 (MIT), zstd 1.5.7 (BSD-3-Clause or GPL-2.0-only) and zlib 1.3.1 (Zlib). On a Mac they're built into `curl_cffi/_wrapper.abi3.so`.
- pywebview, in `_internal\webview\lib`: Microsoft's WebView2 SDK 1.0.3856.49 (`Microsoft.Web.WebView2.Core.dll`, `Microsoft.Web.WebView2.WinForms.dll` and `WebView2Loader.dll`), under Microsoft's BSD-3-Clause-style license. https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.3856.49/License. On a Mac, pywebview uses the WebKit that comes with macOS instead.
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
