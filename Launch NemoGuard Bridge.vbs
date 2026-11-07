Option Explicit
Dim fso, shell, root, python, script
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
python = fso.BuildPath(root, ".venv\Scripts\pythonw.exe")
script = fso.BuildPath(root, "scripts\outlook_bridge_launcher.pyw")
If Not fso.FileExists(python) Or Not fso.FileExists(script) Then
    MsgBox "NemoGuard's Python environment or launcher is missing. See docs\OUTLOOK_FOLDER_BRIDGE.md.", vbExclamation, "NemoGuard Outlook Bridge"
    WScript.Quit 1
End If
shell.CurrentDirectory = root
shell.Run """" & python & """ """ & script & """", 0, False
