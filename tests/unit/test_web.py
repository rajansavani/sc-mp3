"""Static checks of web/: imports and links resolve, vendored files match, and the mock is complete.

The UI itself is checked in a browser. These catch a broken path or a missing call without one.
"""

import base64
import hashlib
import json
import re
from pathlib import Path

import pytest

from scmp3.app.bridge.account import AccountApi
from scmp3.app.bridge.components import ComponentsApi
from scmp3.app.bridge.files import FilesApi
from scmp3.app.bridge.jobs import JobsApi
from scmp3.app.bridge.settings import SettingsApi
from scmp3.app.input_reasons import REASON_CODES
from scmp3.app.main import WEB_DIR

COMPONENTS = [
    "TopBar",
    "Queue",
    "TrackEditor",
    "ArtistEditor",
    "CoverPanel",
    "SetEditor",
    "Settings",
    "FirstRun",
    "SignInDialog",
    "SpotifyHelp",
    "Toasts",
]

# Each mock file and the Python mixin whose calls it has to match.
MOCK_AREAS = {
    "jobs.js": JobsApi,
    "files.js": FilesApi,
    "settings.js": SettingsApi,
    "account.js": AccountApi,
    "components.js": ComponentsApi,
}

# Every color token in the design's token table.
DESIGN_TOKENS = [
    "--bg",
    "--surface-1",
    "--surface-2",
    "--surface-3",
    "--surface-selected",
    "--line-1",
    "--line-2",
    "--line-3",
    "--line-4",
    "--line-dashed",
    "--text-1",
    "--text-2",
    "--text-3",
    "--accent",
    "--on-accent",
    "--accent-text",
    "--chip-on-bg",
    "--chip-on-text",
    "--warn",
    "--ok",
    "--ok-surface",
    "--ok-line",
]

# The module names in `import ... from "x"`, `export ... from "x"`, `import "x"` and `import("x")`.
IMPORT = re.compile(
    r"""
    \b(?:import|export)\b[^"';]*?\bfrom\s*["']([^"']+)["']
    | \bimport\s*\(\s*["']([^"']+)["']\s*\)
    | ^\s*import\s*["']([^"']+)["']
    """,
    re.MULTILINE | re.VERBOSE,
)
EXPORTED_FUNCTION = re.compile(r"^export\s+(?:async\s+)?function\s+(\w+)", re.MULTILINE)
STRING_LITERAL = re.compile(r"""'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`""")
LINE_COMMENT = re.compile(r"^\s*//.*$", re.MULTILINE)
EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")
CSP = re.compile(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">')


def our_scripts() -> list[Path]:
    """The UI's own JavaScript, without the vendored files."""
    return [path for path in WEB_DIR.rglob("*.js") if "vendor" not in path.parts]


def import_map() -> dict[str, str]:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    match = re.search(r'<script type="importmap">(.*?)</script>', html, re.DOTALL)
    assert match, "index.html has no import map"
    return json.loads(match.group(1))["imports"]


def imports_of(script: Path) -> list[str]:
    text = script.read_text(encoding="utf-8")
    return [next(group for group in match.groups() if group) for match in IMPORT.finditer(text)]


def versions_table() -> dict[str, str]:
    """Each file VERSIONS.md lists, relative to web/, with its SHA-256."""
    text = (WEB_DIR / "vendor" / "VERSIONS.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| `([^`]+)` \| [^|]+ \| `([0-9a-f]{64})` \|$", text, re.MULTILINE)
    return dict(rows)


@pytest.mark.parametrize(
    "script", our_scripts(), ids=lambda path: path.relative_to(WEB_DIR).as_posix()
)
def test_every_import_resolves(script: Path) -> None:
    bare_names = import_map()
    for specifier in imports_of(script):
        if specifier.startswith("."):
            assert (script.parent / specifier).is_file(), specifier
        else:
            assert specifier in bare_names, f"{specifier} isn't in the import map"


def test_the_import_map_points_at_vendored_files() -> None:
    for specifier, target in import_map().items():
        assert (WEB_DIR / target).is_file(), specifier


def test_every_file_index_html_links_exists() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    links = re.findall(r'(?:href|src)="([^"]+)"', html)
    # A data: URL is inline, like the empty favicon, so there's no file to find.
    files = [link for link in links if not link.startswith("data:")]
    assert files
    for link in files:
        assert (WEB_DIR / link).is_file(), link


def test_every_url_in_the_stylesheets_exists() -> None:
    for stylesheet in (WEB_DIR / "styles").glob("*.css"):
        for url in re.findall(r'url\("?([^")]+)"?\)', stylesheet.read_text(encoding="utf-8")):
            assert (stylesheet.parent / url).is_file(), f"{stylesheet.name}: {url}"


@pytest.mark.parametrize("name", COMPONENTS)
def test_each_component_has_its_file_export_and_stylesheet(name: str) -> None:
    script = (WEB_DIR / "components" / f"{name}.js").read_text(encoding="utf-8")
    assert f"export function {name}(" in script
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert f'href="styles/{name}.css"' in html


def test_the_tokens_define_every_design_color() -> None:
    tokens = (WEB_DIR / "styles" / "tokens.css").read_text(encoding="utf-8")
    defined = set(re.findall(r"(--[\w-]+)\s*:", tokens))
    assert set(DESIGN_TOKENS) <= defined


@pytest.mark.parametrize("file_name", list(MOCK_AREAS))
def test_each_mock_area_answers_every_call_of_its_mixin(file_name: str) -> None:
    script = (WEB_DIR / "mock" / file_name).read_text(encoding="utf-8")
    exported = set(EXPORTED_FUNCTION.findall(script)) - {"snapshotPart"}
    calls = {name for name in dir(MOCK_AREAS[file_name]) if not name.startswith("_")}
    assert exported == calls


def test_the_mock_index_includes_every_area() -> None:
    script = (WEB_DIR / "mock" / "index.js").read_text(encoding="utf-8")
    for file_name in MOCK_AREAS:
        assert f'from "./{file_name}"' in script


def test_vendored_files_match_their_recorded_checksums() -> None:
    table = versions_table()
    assert table
    for relative, expected in table.items():
        digest = hashlib.sha256((WEB_DIR / relative).read_bytes()).hexdigest()
        assert digest == expected, relative


def test_every_vendored_file_is_recorded() -> None:
    present = {
        path.relative_to(WEB_DIR).as_posix()
        for folder in ("vendor", "fonts")
        for path in (WEB_DIR / folder).iterdir()
        if path.name != "VERSIONS.md"
    }
    assert present == set(versions_table())


@pytest.mark.parametrize(
    "strings_file", sorted((WEB_DIR / "strings").glob("*.js")), ids=lambda p: p.name
)
def test_ui_text_has_no_exclamation_marks_em_dashes_or_emoji(strings_file: Path) -> None:
    # Comments go first, so an apostrophe in one can't start a string.
    code = LINE_COMMENT.sub("", strings_file.read_text(encoding="utf-8"))
    literals = STRING_LITERAL.findall(code)
    assert literals
    for literal in literals:
        assert "!" not in literal, literal
        assert "—" not in literal, literal
        assert not EMOJI.search(literal), literal


def test_the_string_check_sees_every_kind_of_quote() -> None:
    code = "a: 'one!', b: \"two\", c: `three`"
    assert STRING_LITERAL.findall(code) == ["'one!'", '"two"', "`three`"]


def test_every_reason_code_the_mock_returns_has_text() -> None:
    mock = (WEB_DIR / "mock" / "jobs.js").read_text(encoding="utf-8")
    # Reason codes are the only all-caps strings in the mock.
    codes = set(re.findall(r'"([A-Z][A-Z_]+)"', mock))
    strings = (WEB_DIR / "strings" / "main.js").read_text(encoding="utf-8")
    block = strings[
        strings.index("rejectReasons: {") : strings.index("},", strings.index("rejectReasons"))
    ]
    texts = set(re.findall(r"^\s+([A-Z_]+):", block, re.MULTILINE))
    assert codes >= {"NOT_SUPPORTED", "YOUTUBE_PLAYLIST", "SOUNDCLOUD_NOT_TRACK"}
    assert codes <= texts
    # The codes the real bridge sends: the reason constants in core, by name.
    assert set(REASON_CODES.values()) <= texts


def content_security_policy() -> dict[str, list[str]]:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    match = CSP.search(html)
    assert match, "index.html has no Content-Security-Policy"
    directives = [part.split() for part in match.group(1).split(";") if part.strip()]
    return {name: values for name, *values in directives}


def test_the_csp_allows_the_import_map_by_its_hash() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    import_map_text = re.search(r'<script type="importmap">(.*?)</script>', html, re.DOTALL)
    assert import_map_text
    digest = hashlib.sha256(import_map_text.group(1).encode("utf-8")).digest()
    expected = "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"
    assert content_security_policy()["script-src"] == ["'self'", expected]


def test_the_csp_allows_nothing_unsafe() -> None:
    policy = content_security_policy()
    assert policy["default-src"] == ["'self'"]
    assert policy["object-src"] == ["'none'"]
    assert policy["base-uri"] == ["'none'"]
    values = [value for values in policy.values() for value in values]
    assert "'unsafe-inline'" not in values
    assert "'unsafe-eval'" not in values


def test_the_import_map_is_the_only_inline_code() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    inline_scripts = re.findall(r"<script(?![^>]*\bsrc=)([^>]*)>", html)
    assert inline_scripts == [' type="importmap"']
    assert "<style" not in html
    assert " style=" not in html
