"""Settings are explicit. The loader does not open a dotenv file."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from peyda_pm_bot.__main__ import main
from peyda_pm_bot.config import Settings, settings_from_environ

_NAMES = ("PEYDA_BOT_TOKEN", "PEYDA_GROUP_ID", "PEYDA_TOPIC_ID", "PEYDA_ADMIN_IDS")


def test_settings_reject_an_empty_allowlist(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(
            bot_token="placeholder",
            group_id=0,
            topic_id=7,
            admin_ids=frozenset({5}),
            database_path=tmp_path / "phase-one.db",
        )
    with pytest.raises(ValidationError):
        Settings(
            bot_token="placeholder",
            group_id=-100,
            topic_id=7,
            admin_ids=frozenset({0}),
            database_path=tmp_path / "phase-one.db",
        )


def test_loader_ignores_the_old_database_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("PEYDA_DATABASE_PATH", "/tmp/old.db")
    cli = tmp_path / "agy-cli"
    cli.write_text("", encoding="utf-8")
    monkeypatch.setenv("PEYDA_AGY_CLI", str(cli))
    settings = settings_from_environ(tmp_path / "phase-one.db")
    assert settings.database_path == tmp_path / "phase-one.db"
    assert settings.group_id == -100
    assert settings.topic_id == 7
    assert settings.admin_ids == frozenset({5, 8})
    assert settings.agy_cli_path == cli


def test_loader_accepts_a_bracketed_admin_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("PEYDA_ADMIN_IDS", "[5, 8]")
    settings = settings_from_environ(tmp_path / "phase-one.db")
    assert settings.admin_ids == frozenset({5, 8})


def test_invalid_admin_list_does_not_echo_the_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("PEYDA_ADMIN_IDS", "[not-an-id]")
    with pytest.raises(ValueError, match="positive integers") as caught:
        settings_from_environ(tmp_path / "phase-one.db")
    assert "not-an-id" not in str(caught.value)


def test_missing_cli_is_recorded_without_starting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("PEYDA_AGY_CLI", str(tmp_path / "missing"))
    settings = settings_from_environ(tmp_path / "phase-one.db")
    assert settings.agy_cli_path is None


def test_loader_does_not_read_a_dotenv_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in _NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("PEYDA_BOT_TOKEN=from-file\n", encoding="utf-8")
    with pytest.raises(ValueError, match="PEYDA_BOT_TOKEN"):
        settings_from_environ(tmp_path / "phase-one.db")


def test_live_command_stops_before_the_network_when_settings_are_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in _NAMES:
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / "empty.env"
    env_file.write_text("# placeholder\n", encoding="utf-8")
    code = main(
        [
            "--live-telegram",
            "--env-file",
            str(env_file),
            "--database",
            str(tmp_path / "phase-one.db"),
        ]
    )
    assert code == 1
    assert "PEYDA_BOT_TOKEN" in capsys.readouterr().out
    assert not (tmp_path / "phase-one.db").exists()


def test_live_flag_is_required() -> None:
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == 2


def _set_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEYDA_BOT_TOKEN", "placeholder")
    monkeypatch.setenv("PEYDA_GROUP_ID", "-100")
    monkeypatch.setenv("PEYDA_TOPIC_ID", "7")
    monkeypatch.setenv("PEYDA_ADMIN_IDS", "5, 8")
