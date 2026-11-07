"""Offline tests for the graphical Outlook bridge launcher."""
from contextlib import closing
import importlib.machinery
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
loader = importlib.machinery.SourceFileLoader(
    "outlook_bridge_launcher", str(ROOT / "scripts" / "outlook_bridge_launcher.pyw")
)
spec = importlib.util.spec_from_loader(loader.name, loader)
launcher = importlib.util.module_from_spec(spec)
loader.exec_module(launcher)


class Collection:
    def __init__(self, values):
        self.values = values
        self.Count = len(values)

    def Item(self, index):
        return self.values[index - 1]


class Variable:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class LauncherTests(unittest.TestCase):
    def test_prefers_existing_queue_mailbox_without_opening_stores(self):
        namespace = SimpleNamespace(
            Stores=Collection([SimpleNamespace(DisplayName="first"), SimpleNamespace(DisplayName="second")]),
            DefaultStore=SimpleNamespace(DisplayName="first"),
        )
        names = launcher.matching_mailboxes(namespace)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.sqlite"
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute(
                    "INSERT INTO settings VALUES (?, ?)",
                    ("config", '["second", "NemoGuard Test Alerts", "axet-designer:test"]'),
                )
                conn.commit()
            self.assertEqual(launcher.preferred_mailbox(namespace, names, path), "second")
            with self.assertRaises(ValueError):
                launcher.preferred_mailbox(namespace, ["first"], path)
            self.assertEqual(launcher.preferred_mailbox(namespace, names, path.parent / "missing"), "first")

    def test_preflight_failure_never_spawns_bridge(self):
        obj = launcher.Launcher.__new__(launcher.Launcher)
        obj.process = None
        obj.mailbox = Variable("selected")
        obj.status = Variable()
        with patch.object(launcher, "discover_designer_url", side_effect=RuntimeError(
                "Designer backend lacks the required Outlook bridge contract.")), \
                patch.object(launcher.subprocess, "Popen") as spawn, \
                patch.object(launcher.messagebox, "showwarning") as warning:
            obj.start()
            spawn.assert_not_called()
            self.assertIn("missing the Outlook bridge update", obj.status.get())
            warning.assert_called_once()

            warning.reset_mock()
            obj.start(show_error=False)
            warning.assert_not_called()
            spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
