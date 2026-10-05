import sys
import types

from loki import windows


def test_frozen_startup_command_quotes_path_and_starts_in_tray(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\Loki\Loki.exe")
    assert windows.startup_command() == '"C:\\Program Files\\Loki\\Loki.exe" --tray --monitor'


def test_startup_uses_only_normal_current_user_run_entry(monkeypatch):
    operations = []

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    def create_key(root, path):
        operations.append((root, path))
        return Key()

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER="current-user",
        REG_SZ=1,
        CreateKey=create_key,
        SetValueEx=lambda _key, name, _reserved, kind, value: operations.append(
            ("set", name, kind, value)
        ),
        DeleteValue=lambda _key, name: operations.append(("delete", name)),
    )
    monkeypatch.setattr(windows, "IS_WINDOWS", True)
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(windows, "startup_command", lambda: '"C:\\Loki\\Loki.exe" --tray --monitor')
    windows.set_startup(True)
    windows.set_startup(False)
    assert operations[0] == ("current-user", r"Software\Microsoft\Windows\CurrentVersion\Run")
    assert operations[1] == ("set", "Loki", 1, '"C:\\Loki\\Loki.exe" --tray --monitor')
    assert operations[3] == ("delete", "Loki")
