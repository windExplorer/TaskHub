' TaskHub 真·后台启动器：隐藏窗口启动 pythonw，进程不附加任何控制台。
' 由 start_daemon.bat 调用；也可双击本文件直接后台启动（需先 uv sync）。
Option Explicit
Dim fso, ws, root, pyw, mainpy, logfile, cmd

Set fso = CreateObject("Scripting.FileSystemObject")
Set ws = CreateObject("WScript.Shell")

root = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = root & "\.venv\Scripts\pythonw.exe"
mainpy = root & "\main.py"
logfile = root & "\logs\server.log"

If Not fso.FileExists(pyw) Then
    MsgBox "pythonw.exe not found. Run 'uv sync' first.", 16, "TaskHub"
    WScript.Quit 1
End If

' 确保 logs 目录存在
If Not fso.FolderExists(root & "\logs") Then
    fso.CreateFolder root & "\logs"
End If

' 引号包裹路径（防空格）。不用 shell 重定向（WScript.Run 不解析 >>），
' 日志由 main.py 的 FileHandler 直接写 logs\server.log。0=隐藏窗口, False=不等待
cmd = """" & pyw & """ """ & mainpy & """ start --tray --wait-free"
ws.Run cmd, 0, False
