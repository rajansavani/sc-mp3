# CLAUDE.md

sc-mp3 is a desktop app (Python and pywebview) for Windows, macOS and Linux. You paste a SoundCloud or YouTube link, review and fix the metadata, and save a tagged MP3 for Spotify Local Files.

It replaces a single old script, `legacy/soundcloud_downloader.py`. That script is kept for reference only; don't extend it.

## How work happens

- **Tickets.** Work is split into tickets. A coding session gets one ticket through a handoff file, which gives the absolute paths to everything it needs. Follow the handoff.
- **Planning folder.** The plan, spec, tickets and handoffs live in `docs/planning/` in the main checkout.
  - That folder is gitignored, so it isn't in worktrees or other clones.
  - Never commit it.
- **Branches.** Each session works on one ticket, on its own branch.
  - Don't merge, push or change `main`. The planning session does the merging.
- **Commit messages.** The subject is one plain line saying what the commit adds, for example `Add the producer-credit rule`. Don't put the ticket number in it.
- **Settled decisions.** The decisions in the plan are settled. If the code or the outside world contradicts one, stop and ask instead of changing course.
- **Files.** Only touch the files your ticket lists, and explain any exception in your report.
  - Don't change `pyproject.toml`, `uv.lock` or `tests/conftest.py` unless your ticket says so.
- **Checks.** Before any handoff, these must pass:
  - `uv run ruff check`
  - `uv run ruff format --check`
  - `uv run pytest`

## Code style

- Write for the next reader.
  - Use clear names, and small functions that each do one thing.
  - Use early returns instead of deep nesting.
  - Avoid clever one-liners.
- Keep modules small and focused.
  - `core/` never imports from `app/` or the UI.
  - Pass collaborators in (the yt-dlp factory, the HTTP opener, the clock) instead of reaching for globals. That keeps code easy to test and to replace.
- Make features easy to add.
  - A new source, cleanup rule, setting or UI panel should mean adding code in one place, not editing many.
  - Prefer data tables and registries, like `wordlists.toml`, over long if/elif chains.
- Comments are short and frequent.
  - One sentence each, inline or on the line above the code it explains.
  - Explain why, or what isn't obvious from the code. Don't restate the code.
  - No large comment blocks and no banner comments.
- Docstrings are one line for most functions, and a few lines at most for a module.
- Use type hints everywhere and dataclasses for data.
- Keep global state to the job manager and settings.
- Raise `ScmpError` with an error kind. Never swallow an exception silently.
- Delete dead code instead of commenting it out. A TODO needs a ticket id: `# TODO(T-061): Handle age-restricted videos.`
- Split a file when it grows past a few hundred lines.
- Give every module unit tests, named after the behavior they check.

## Writing user-facing text

These rules cover UI text, error messages, the README, release notes, docs and commit messages.

- Be plain and specific. Say what happened and what to do next: "Saved to Music/sc-mp3." rather than "Your track was successfully saved!"
- Skip marketing words and the usual AI tells: seamless, effortless, powerful, robust, intuitive, elevate, unlock, supercharge, leverage, empower, cutting-edge, game-changer, journey, dive in, delve, harness, streamline, comprehensive.
- Skip filler: "simply", "just", "easily", "it's worth noting", "whether you're X or Y", "not just X but Y", "say goodbye to".
- In the UI:
  - no emoji and no exclamation marks
  - no em dashes; use a period or a comma
- Use sentence case for buttons and headings: "Save to library", not "Save To Library".
- Keep sentences short, and address the reader as "you".
- Error messages say what went wrong and what to do about it. Never "Oops", and never a bare "Something went wrong".
- Keep formatting light.
  - Don't bold the start of every line.
  - Don't add headings to two-line sections.
  - Don't group things in threes for rhythm.
- Use exact names and numbers: "AAC 160 kbps", "Go+", "Spotify Local Files".
