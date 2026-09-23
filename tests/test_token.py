"""Where the token comes from, and what happens when it does not.

A token is a secret, so the only thing these tests assert about its value is
that the right one is chosen.  Nothing here writes a real token anywhere.
"""

import json

import pytest

from bazaar import config
from bazaar.network.transport import ConnectionFailed, load_token, resolve_token


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Keep the developer's real environment and .env out of these tests."""
    monkeypatch.delenv(config.TOKEN_ENV, raising=False)
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "absent.env")
    return tmp_path


def credentials_file(tmp_path, station="P01", token="file-token"):
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps({"players": [{"station_id": station, "token": token}]})
    )
    return path


def test_the_environment_variable_wins(monkeypatch, isolated_env):
    monkeypatch.setenv(config.TOKEN_ENV, "env-token")
    path = credentials_file(isolated_env)
    token, source = resolve_token(path, "P01")
    assert token == "env-token"
    assert source == f"${config.TOKEN_ENV}"


def test_a_dotenv_file_is_read_when_the_variable_is_unset(monkeypatch, isolated_env):
    env_file = isolated_env / "dot.env"
    env_file.write_text(f"# a comment\n{config.TOKEN_ENV}=dotenv-token\n")
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    token, source = resolve_token(None, "P01")
    assert token == "dotenv-token"
    assert source == f"${config.TOKEN_ENV}"


def test_dotenv_quotes_and_whitespace_are_stripped(monkeypatch, isolated_env):
    env_file = isolated_env / "dot.env"
    env_file.write_text(f'{config.TOKEN_ENV}= "quoted-token"  \n')
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    assert resolve_token(None, "P01")[0] == "quoted-token"


def test_the_credentials_file_is_the_fallback(isolated_env):
    path = credentials_file(isolated_env, token="file-token")
    token, source = resolve_token(path, "P01")
    assert token == "file-token"
    assert source == str(path)


def test_an_empty_environment_variable_does_not_count(monkeypatch, isolated_env):
    monkeypatch.setenv(config.TOKEN_ENV, "   ")
    path = credentials_file(isolated_env, token="file-token")
    assert resolve_token(path, "P01")[0] == "file-token"


def test_no_token_anywhere_raises_something_actionable(isolated_env):
    with pytest.raises(ConnectionFailed, match=config.TOKEN_ENV):
        resolve_token(isolated_env / "missing.json", "P01")


def test_an_unknown_station_in_the_credentials_file_is_reported(isolated_env):
    path = credentials_file(isolated_env, station="P01")
    with pytest.raises(KeyError, match="P99"):
        load_token(path, "P99")


def test_the_live_url_is_wss_and_the_practice_url_stays_local():
    assert config.DEFAULT_URL.startswith("wss://")
    assert config.PRACTICE_URL.startswith("ws://127.0.0.1")
