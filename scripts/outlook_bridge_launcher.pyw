"""Graphical launcher for the local Outlook test-folder bridge."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from outlook_folder_bridge import FOLDER_NAME, select_folder, discover_designer_url

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "deptapps-flows-designer-container-28477-"
AGENT_CWD = "/usr/src/node-red/.node-red/python-envs/373027f335ebf4cc/src/c7e2cde01005ddac"


def matching_mailboxes(namespace):
    """List store names without opening archive or public-folder stores."""
    return [str(namespace.Stores.Item(i).DisplayName)
            for i in range(1, namespace.Stores.Count + 1)]


def preferred_mailbox(namespace, names, state_path):
    """Prefer the existing queue's mailbox, otherwise Outlook's default store."""
    if state_path.is_file():
        with closing(sqlite3.connect(state_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key='config'").fetchone()
        if not row:
            raise ValueError("Existing bridge state has no configuration.")
        config = json.loads(row[0])
        if not isinstance(config, list) or len(config) != 3 or config[1] != FOLDER_NAME:
            raise ValueError("Existing bridge state has an invalid configuration.")
        matches = [name for name in names if name.casefold() == config[0].casefold()]
        if len(matches) != 1:
            raise ValueError("The mailbox bound to existing bridge state is unavailable or ambiguous.")
        return matches[0]
    try:
        default = str(namespace.DefaultStore.DisplayName)
        matches = [name for name in names if name.casefold() == default.casefold()]
        return matches[0] if len(matches) == 1 else ""
    except Exception:
        return ""


class Launcher:
    def __init__(self, window):
        self.window = window
        self.process = None
        window.title("NemoGuard Outlook Bridge")
        window.geometry("560x310")
        window.protocol("WM_DELETE_WINDOW", self.close)
        self.status = tk.StringVar(value="Checking Outlook…")
        self.mailbox = tk.StringVar()
        ttk.Label(window, text="NemoGuard Outlook Bridge", font=("Segoe UI", 15, "bold")).pack(pady=12)
        ttk.Label(window, text=f"Folder: {FOLDER_NAME}").pack()
        self.selector = ttk.Combobox(window, textvariable=self.mailbox, state="readonly", width=55)
        self.selector.pack(pady=8)
        ttk.Label(window, textvariable=self.status, wraplength=520).pack(pady=8)
        buttons = ttk.Frame(window)
        buttons.pack(pady=5)
        self.start_button = ttk.Button(buttons, text="Start monitoring", command=self.start)
        self.start_button.pack(side="left", padx=5)
        self.stop_button = ttk.Button(buttons, text="Stop monitoring", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=5)
        ttk.Label(window, text="Keep this window and classic Outlook open. Attachments are excluded.", wraplength=520).pack(pady=10)
        window.after(100, self.detect)

    def detect(self):
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            try:
                namespace = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
                names = matching_mailboxes(namespace)
                state_path = Path(os.environ.get("LOCALAPPDATA", ".")) / "NemoGuard" / "outlook-bridge.sqlite"
                preferred = preferred_mailbox(namespace, names, state_path)
            finally:
                pythoncom.CoUninitialize()
            self.selector["values"] = names
            if preferred:
                self.mailbox.set(preferred)
                self.start(show_error=False)
            elif names:
                self.status.set("Choose the mailbox containing the test folder, then click Start monitoring.")
            else:
                self.status.set("No Outlook mailbox found. Open classic Outlook and restart the launcher.")
        except Exception as exc:
            self.status.set(f"Could not access classic Outlook ({type(exc).__name__}).")

    def start(self, show_error=True):
        if self.process and self.process.poll() is None:
            return
        name = self.mailbox.get()
        if not name:
            messagebox.showwarning("Select mailbox", "Choose the mailbox containing the test folder.")
            return
        script = ROOT / "scripts" / "outlook_folder_bridge.py"
        try:
            discover_designer_url(PREFIX)
        except Exception as exc:
            if isinstance(exc, RuntimeError) and str(exc) == "Designer backend lacks the required Outlook bridge contract.":
                reason = "Monitoring paused: the running Axet backend is missing the Outlook bridge update. Deploy the updated backend, then retry."
            else:
                reason = "Monitoring paused: Axet backend is unavailable, unhealthy, or incompatible. Check its status and retry."
            self.status.set(reason)
            if show_error:
                messagebox.showwarning("Monitoring cannot start", reason)
            return
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.process = subprocess.Popen(
                [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-u", str(script), "--mailbox", name,
                 "--axet-designer-prefix", PREFIX, "--secret-from-agent", AGENT_CWD],
                cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                creationflags=flags, env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
        except OSError as exc:
            self.status.set(f"Could not launch bridge ({type(exc).__name__}).")
            return
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("Starting bridge…")
        process = self.process

        def read_output():
            for line in process.stdout:
                text = line.strip()
                if text:
                    try:
                        self.window.after(0, self.status.set, text)
                    except RuntimeError:
                        return
            code = process.wait()
            try:
                self.window.after(0, self.exited, process, code)
            except RuntimeError:
                pass

        threading.Thread(target=read_output, daemon=True).start()

    def exited(self, process, code):
        if self.process is not process:
            return
        self.process = None
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status.set(f"Bridge stopped (exit {code}). Check Outlook and Axet, then retry.")

    def stop(self):
        process = self.process
        if process and process.poll() is None:
            # The Windows virtual-environment launcher can spawn a child
            # interpreter. Terminating only the parent leaves polling active.
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            self.status.set("Stopping bridge…")

    def close(self):
        if self.process and self.process.poll() is None:
            if not messagebox.askyesno("Stop monitoring?", "Closing this window stops the bridge. Continue?"):
                return
            self.stop()
        self.window.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    Launcher(root)
    root.mainloop()
