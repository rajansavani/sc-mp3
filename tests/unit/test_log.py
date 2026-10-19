"""Tests for the log file and for masking tokens in it."""

import base64
import json
import logging
import secrets
from collections.abc import Callable, Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from scmp3.core import log, paths
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.log import MASK, redact, register_secret, setup_logging


def token_shaped() -> str:
    """A made-up value in SoundCloud's token shape, different every time."""
    return f"2-{secrets.randbelow(10**6):06d}-{secrets.randbelow(10**9)}-{secrets.token_hex(8)}"


@pytest.fixture
def log_file(scmp3_home: Path) -> Iterator[Path]:
    """Logging set up under a temp SCMP3_HOME, and taken down again afterwards."""
    root = logging.getLogger()
    level = root.level
    path = setup_logging()
    yield path
    log.close_logging()
    root.setLevel(level)


def read_log(path: Path) -> str:
    """The log file's text, after flushing what the handlers hold."""
    for handler in logging.getLogger().handlers:
        handler.flush()
    return path.read_text(encoding="utf-8")


def jwt_shaped() -> str:
    """A made-up JWT about as long as SoundCloud's, different every time."""
    header = base64url({"alg": "RS256", "typ": "JWT", "kid": secrets.token_hex(8)})
    payload = base64url({"sub": "soundcloud:users:1", "scope": "*", "pad": secrets.token_hex(250)})
    return f"{header}.{payload}.{secrets.token_urlsafe(256)}"


def base64url(data: dict[str, str]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def test_a_made_up_jwt_has_the_real_shape() -> None:
    token = jwt_shaped()
    assert token.startswith("eyJ")
    assert token.count(".") == 2
    assert len(token) > 800


@pytest.mark.parametrize("make_token", [token_shaped, jwt_shaped], ids=["2- shape", "JWT"])
@pytest.mark.parametrize(
    "template",
    [
        "OAuth {token}",
        "OAuth%20{token}",
        "OAuth+{token}",
        "Bearer {token}",
        "sent {token} to /me",
        "token_{token}",
        "Authorization=OAuth%20{token}&x=1",
        '{{"oauth_token": "{token}"}}',
        "{{'headers': {{'Authorization': 'OAuth {token}'}}}}",
    ],
)
def test_both_token_shapes_are_masked_wherever_they_appear(
    template: str, make_token: Callable[[], str]
) -> None:
    token = make_token()
    masked = redact(template.format(token=token))
    assert MASK in masked
    for part in token.split("."):
        assert part not in masked


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ("Authorization: OAuth abc.def", f"Authorization: {MASK}"),
        ("authorization: Basic dXNlcjpwYXNz", f"authorization: {MASK}"),
        (
            "{'Authorization': 'OAuth abc', 'Accept': '*/*'}",
            f"{{'Authorization': '{MASK}', 'Accept': '*/*'}}",
        ),
        ("Cookie: oauth_refresh_token=rt-9f8e7d6c5b4a; sc_anonymous_id=1", f"Cookie: {MASK}"),
        ("Set-Cookie: oauth_token=abc; Path=/; Secure", f"Set-Cookie: {MASK}"),
    ],
)
def test_auth_and_cookie_headers_are_masked_whole(text: str, masked: str) -> None:
    assert redact(text) == masked


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ("/tracks/123?secret_token=s-AbCdEfGhIjK&x=1", f"/tracks/123?secret_token={MASK}&x=1"),
        ('{"access_token": "abcdef123456"}', f'{{"access_token": "{MASK}"}}'),
        ("oauth_refresh_token=rt-9f8e7d6c5b4a", f"oauth_refresh_token={MASK}"),
        (
            "https://soundcloud.com/?oauth_token=abc123xyz&x=1",
            f"https://soundcloud.com/?oauth_token={MASK}&x=1",
        ),
        ("x-auth-token: abc123xyz", f"x-auth-token: {MASK}"),
        ("url=a%3Fsecret_token%3Ds-AbCdEfGhIjK", f"url=a%3Fsecret_token%3D{MASK}"),
        (
            "{'username': 'oauth', 'password': 'hunter2hunter2'}",
            f"{{'username': 'oauth', 'password': '{MASK}'}}",
        ),
    ],
)
def test_values_of_token_and_password_keys_are_masked(text: str, masked: str) -> None:
    assert redact(text) == masked


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        (
            "https://soundcloud.com/kaia/midnight-drive/s-AbCdEfGhIjK",
            f"https://soundcloud.com/kaia/midnight-drive/s-{MASK}",
        ),
        (
            "https://soundcloud.com/kaia/sets/lantern/s-AbCdEfGhIjK?si=1",
            f"https://soundcloud.com/kaia/sets/lantern/s-{MASK}?si=1",
        ),
        (
            "/resolve?url=https%3A%2F%2Fsoundcloud.com%2Fkaia%2Ft%2Fs-AbCdEfGhIjK",
            f"/resolve?url=https%3A%2F%2Fsoundcloud.com%2Fkaia%2Ft%2Fs-{MASK}",
        ),
    ],
)
def test_the_secret_part_of_a_share_link_is_masked(text: str, masked: str) -> None:
    assert redact(text) == masked


def test_a_registered_secret_is_masked_exactly() -> None:
    secret = f"plain-{secrets.token_hex(6)}"
    register_secret(secret)
    assert redact(f"before {secret} after") == f"before {MASK} after"


def test_a_longer_secret_is_masked_whole_when_it_contains_a_shorter_one() -> None:
    short = f"s{secrets.token_hex(4)}"
    register_secret(short)
    register_secret(f"{short}-and-more")
    assert redact(f"x {short}-and-more y") == f"x {MASK} y"


def test_a_registered_secret_is_stripped_and_blank_ones_are_ignored() -> None:
    secret = secrets.token_hex(6)
    register_secret(f"  {secret}\n")
    register_secret("   ")
    assert redact(f"[{secret}] keeps spaces") == f"[{MASK}] keeps spaces"


@pytest.mark.parametrize("junk", ["a", "Kaia", "1234567"])
def test_a_secret_too_short_to_be_a_token_is_ignored(junk: str) -> None:
    register_secret(junk)
    text = "Saved Kaia - Midnight Drive.mp3 at 1234567"
    assert redact(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "Saved Kaia - Midnight Drive (feat. Lou V) at 2-10 o'clock, 320 kbps",
        "[soundcloud] Provided authorization token is invalid. Continuing as guest",
        "Using cookiesfrombrowser=firefox for 3 tracks",
        "Saved to Music/sc-mp3/Kaia - Midnight Drive.mp3",
        "https://soundcloud.com/kaia/midnight-drive?si=abc",
    ],
)
def test_ordinary_text_is_left_alone(text: str) -> None:
    assert redact(text) == text


@pytest.mark.parametrize("make_token", [token_shaped, jwt_shaped])
def test_redacting_twice_changes_nothing_more(make_token: Callable[[], str]) -> None:
    once = redact(f"Authorization: OAuth {make_token()} and OAuth {make_token()}")
    assert redact(once) == once


def test_setup_logging_writes_to_the_logs_folder(log_file: Path) -> None:
    assert log_file == paths.log_dir() / "sc-mp3.log"
    logging.getLogger("scmp3.test").info("hello from the test")
    assert "INFO scmp3.test: hello from the test" in read_log(log_file)


def test_the_file_rotates_at_five_files_of_one_megabyte(log_file: Path) -> None:
    [handler] = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]
    assert handler.maxBytes == 1_000_000
    assert handler.backupCount == 4


def test_setting_up_twice_doesnt_duplicate_lines(log_file: Path) -> None:
    setup_logging()
    logging.getLogger("scmp3.test").warning("only once")
    assert read_log(log_file).count("only once") == 1


def test_console_logging_adds_a_stderr_handler(
    scmp3_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = logging.getLogger()
    level = root.level
    try:
        setup_logging(console=True)
        logging.getLogger("scmp3.test").warning("to the console OAuth secret-value")
        err = capsys.readouterr().err
    finally:
        log.close_logging()
        root.setLevel(level)
    assert f"to the console OAuth {MASK}" in err
    assert "secret-value" not in err


def test_a_token_never_reaches_the_log_file(log_file: Path) -> None:
    token = token_shaped()
    jwt = jwt_shaped()
    registered = f"registered-{secrets.token_hex(8)}"
    register_secret(registered)
    logger = logging.getLogger("scmp3.core.auth")

    logger.info("Authorization: OAuth %s", token)
    logger.warning("validating %s", registered)
    logger.error(f"in the message itself: {token} and {registered}")
    logger.info("as a dict %s", {"headers": {"Authorization": f"OAuth {registered}"}})
    logger.info("signed in with %s", jwt)
    logger.debug("not written at INFO level, but masked anyway: %s", jwt)
    try:
        raise RuntimeError(f"request with {token} and {registered} failed: {jwt}")
    except RuntimeError:
        logger.exception("the sign-in check failed")
    try:
        raise ScmpError(ErrorKind.auth_expired, f"401 for OAuth {registered}")
    except ScmpError as error:
        logger.warning("expired: %s", error, exc_info=True)
    text = read_log(log_file)
    assert token not in text
    assert registered not in text
    for part in jwt.split("."):
        assert part not in text
    assert f"signed in with {MASK}" in text
    assert "the sign-in check failed" in text
    assert "Traceback" in text
    assert f"RuntimeError: request with {MASK} and {MASK} failed: {MASK}" in text
    assert f"ScmpError: auth_expired: 401 for OAuth {MASK}" in text


def test_the_filter_masks_stack_text() -> None:
    token = token_shaped()
    record = logging.LogRecord(
        "scmp3.test", logging.WARNING, __file__, 1, "look", None, None, sinfo=f"  call({token})"
    )
    log.RedactFilter().filter(record)
    assert record.stack_info == f"  call({MASK})"


def test_the_filter_masks_exception_text_another_handler_already_formatted() -> None:
    token = token_shaped()
    record = logging.LogRecord("scmp3.test", logging.ERROR, __file__, 1, "failed", None, None)
    record.exc_text = f"Traceback ...\nRuntimeError: {token}"
    log.RedactFilter().filter(record)
    assert record.exc_text == f"Traceback ...\nRuntimeError: {MASK}"


def test_records_from_third_party_loggers_are_masked_too(log_file: Path) -> None:
    token = token_shaped()
    logging.getLogger("urllib3.connectionpool").warning("sent %s", token)
    assert token not in read_log(log_file)


def test_a_bad_format_string_still_logs_its_text_masked(log_file: Path) -> None:
    token = token_shaped()
    logging.getLogger("scmp3.test").warning("two values %s %s", token)
    text = read_log(log_file)
    assert "two values" in text
    assert token not in text


def test_arguments_that_dont_fit_a_named_format_dont_raise(log_file: Path) -> None:
    token = token_shaped()
    # Without the fallback, this raises KeyError into the code that logged.
    logging.getLogger("scmp3.test").warning("value: %(name)s", {"other": token})
    text = read_log(log_file)
    assert "value: %(name)s" in text
    assert token not in text


class Unprintable:
    """An argument whose str() and repr() both fail."""

    def __str__(self) -> str:
        raise AttributeError("no name")

    def __repr__(self) -> str:
        raise AttributeError("no name")


def test_an_argument_that_cant_be_printed_doesnt_raise(log_file: Path) -> None:
    logging.getLogger("scmp3.test").warning("track: %s", Unprintable())
    assert "a log message that couldn't be printed, from test_log:" in read_log(log_file)


def test_an_unwritable_log_folder_is_a_disk_error(
    scmp3_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self: Path, *args: object, **kwargs: object) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "mkdir", refuse)
    with pytest.raises(ScmpError) as error_info:
        setup_logging()
    assert error_info.value.kind is ErrorKind.disk


@pytest.mark.parametrize(
    ("url", "safe"),
    [
        (
            "https://api-v2.soundcloud.com/resolve?url=x&client_id=y",
            "api-v2.soundcloud.com/resolve",
        ),
        ("https://me:hunter22@img.example.com/a.jpg", "img.example.com/a.jpg"),
        ("http://me@img.example.com:8080/a.jpg#part", "img.example.com:8080/a.jpg"),
        # A port that isn't a number is dropped rather than raising.
        ("https://img.example.com:abc/a.jpg", "img.example.com/a.jpg"),
        ("not a link", "not a link"),
    ],
    ids=["query", "user and password", "user, port and fragment", "bad port", "not a link"],
)
def test_safe_url_keeps_only_the_host_port_and_path(url: str, safe: str) -> None:
    assert log.safe_url(url) == safe
