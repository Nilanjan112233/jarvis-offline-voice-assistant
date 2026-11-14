"""Permission rules: harmless things run without asking, changes ask, dangerous things are blocked."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import policy as P

CMD = [
    (r"Get-ChildItem C:\ -Force | Sort-Object Length -Descending | Select-Object -First 10", "safe"),
    (r"Get-ChildItem D:\ -Recurse -Include *.jpg,*.png -ErrorAction SilentlyContinue | Measure-Object", "safe"),
    ("ipconfig", "safe"), ("Get-Volume | Select DriveLetter, SizeRemaining", "safe"),
    ("Get-Process | Sort CPU -desc | Select -First 5", "safe"), (r"gci D:\x | % { $_.Name }", "safe"),
    ("Get-CimInstance Win32_Battery | Format-List", "safe"),
    (r"Get-ChildItem C:\ > out.txt", "confirm"), (r"New-Item -ItemType Directory D:\test", "confirm"),
    ("Copy-Item a.txt b.txt", "confirm"), ("Stop-Process -Name notepad", "confirm"),
    ("Start-Process notepad", "confirm"), ("[IO.File]::Delete('a.txt')", "confirm"),
    (r"Remove-Item D:\x -Recurse", "block"), ("Invoke-WebRequest http://x", "block"), ("shutdown /s", "block"),
]
PY = [
    ("import os\nprint(sum(1 for r,d,f in os.walk('D:/') for x in f if x.endswith('.jpg')))", "safe"),
    ("import psutil; print(psutil.cpu_percent())", "safe"),
    ("import shutil; shutil.rmtree('D:/x')", "confirm"), ("open('a.txt','w').write('x')", "confirm"),
    ("import urllib.request", "confirm"), ("import os; os.remove('a')", "confirm"),
]
OTHER = [("list_files", {"path": "C:\\"}, "safe"), ("read_file", {"path": "C:/Windows/win.ini"}, "safe"),
         ("delete_file", {"path": "D:/work/x.txt"}, "confirm"), ("delete_file", {"path": "C:/Windows/x.dll"}, "block"),
         ("delete_file", {"path": "D:/"}, "block"), ("write_file", {"path": "D:/notes/a.txt"}, "confirm")]

fails = 0
for c, want in CMD:
    got = P.check("run_command", {"command": c}, "")[0]; fails += got != want
    print("PASS" if got == want else "FAIL", f"{got:8}", "cmd:", c)
for c, want in PY:
    got = P.check("run_python", {"code": c}, "")[0]; fails += got != want
    print("PASS" if got == want else "FAIL", f"{got:8}", "py:", c.replace("\n", " ")[:60])
for t, a, want in OTHER:
    got = P.check(t, a, "")[0]; fails += got != want
    print("PASS" if got == want else "FAIL", f"{got:8}", t, a)
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
