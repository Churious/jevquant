import importlib.util
from pathlib import Path
import sys

import pytest


def load_script():
    source = Path(__file__).resolve().parents[1] / "app" / "configure_kiwoom.py"
    spec = importlib.util.spec_from_file_location("kiwoom_keys", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_key_writer_refuses_noninteractive_input(monkeypatch):
    script = load_script()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(script.getpass, "getpass", lambda _: pytest.fail("prompt"))
    with pytest.raises(SystemExit, match="SSH"):
        script.main()


def test_key_writer_preserves_other_settings_and_redacts_output(
    tmp_path, monkeypatch, capsys
):
    script = load_script()
    monkeypatch.setattr(
        script, "__file__", str(tmp_path / "backend" / "app" / "configure_kiwoom.py")
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    values = iter(["app${LITERAL}'key", "secret#value"])
    monkeypatch.setattr(script.getpass, "getpass", lambda _: next(values))
    target = tmp_path / ".env"
    target.write_text(
        "POSTGRES_PASSWORD=preserve-db\nLOCAL_TOURNAMENT_ID=keep-id\nKIWOOM_APP_KEY=\nKIWOOM_APP_KEY=duplicate\n"
    )
    script.main()
    text = target.read_text()
    assert (
        "POSTGRES_PASSWORD=preserve-db" in text
        and "LOCAL_TOURNAMENT_ID=keep-id" in text
    )
    assert text.count("KIWOOM_APP_KEY=") == 1
    assert "KIWOOM_APP_KEY='app${LITERAL}\\'key'" in text
    assert "KIWOOM_SECRET_KEY='secret#value'" in text
    output = capsys.readouterr().out
    assert "app${LITERAL}" not in output and "secret#value" not in output
    assert not list(tmp_path.glob(".env-kiwoom-*"))
