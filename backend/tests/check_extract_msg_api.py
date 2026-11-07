"""Inspect the MSG parser in an isolated temporary installation."""
import importlib
import inspect
import subprocess
import sys
import tempfile

with tempfile.TemporaryDirectory() as directory:
    result = subprocess.run(
        ["uv", "pip", "install", "--quiet", "--python", sys.executable,
         "--target", directory, "extract-msg"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode:
        print("Temporary installation failed:", result.stderr[-1200:])
        raise SystemExit(result.returncode)
    sys.path.insert(0, directory)
    extract_msg = importlib.import_module("extract_msg")
    from importlib.metadata import version
    print("Parser version:", version("extract-msg"))
    print("Message signature:", inspect.signature(extract_msg.Message))
    print("Context manager:", hasattr(extract_msg.Message, "__enter__"))
    print("Close method:", hasattr(extract_msg.Message, "close"))
    from extract_msg import msg_classes
    cls = msg_classes.MSGFile
    print("MSGFile input documentation:", (inspect.getdoc(cls.__init__) or "")[:2500])
