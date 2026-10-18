# sc-mp3

sc-mp3 is a desktop app for Windows, macOS and Linux. You paste a SoundCloud or YouTube link, check the title, artist and cover it suggests, and save a tagged MP3 that Spotify Local Files can play. It's a work in progress and can't save tracks yet.

sc-mp3 is for personal use. Respect the artists and the terms of each service you download from. Tracks protected by DRM aren't supported.

## Developer setup

You need [uv](https://docs.astral.sh/uv/). It downloads Python 3.13 if you don't have it.

```
uv sync
uv run pytest
```

Some tests need ffmpeg. They skip when ffmpeg isn't on your PATH.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
