"""Tools Jarvis can use. Each tool = description for the model + Python function.

Most tools work in the background and return text for the model to read.
On-screen tools (open_app, open_url, play_music, media keys, screenshot, lock) act on the PC directly.
"""
import ctypes, html, json, os, re, ssl, subprocess, sys, time, urllib.parse, urllib.request, webbrowser
from ctypes import wintypes
from datetime import datetime, timedelta
from pathlib import Path

import psutil
from timeparse import parse_time

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())   # Windows store had an expired cert for some sites
except Exception:
    SSL_CTX = ssl.create_default_context()

DATA = Path(__file__).resolve().parent.parent / "data"
SKILLS = DATA / "skills"
SKILLS.mkdir(parents=True, exist_ok=True)
NOTES, REMINDERS, MEMORY, TASKS = DATA / "notes.md", DATA / "reminders.json", DATA / "memory.json", DATA / "tasks.json"
DOWNLOADS = Path.home() / "Downloads"
SCREENSHOTS = Path.home() / "Pictures" / "Screenshots"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
MAX_OUT = 2500


def _get(url, data=None, timeout=15):
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        return r.read(2_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace")


def _text(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def size_str(n):
    for unit, div in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= div:
            return f"{n/div:.1f} {unit}"
    return f"{n} bytes"


def load_json(p, default):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def save_json(p, data):
    json.dump(data, open(p, "w", encoding="utf-8"), indent=2, ensure_ascii=False)


def _path(p):
    return Path(os.path.expandvars(os.path.expanduser(str(p).strip().strip('"'))))


# ======================================================== web (background)
SEARCH_BLOCKED = re.compile(r"anomaly|challenge-form", re.I)   # DuckDuckGo's "are you a bot?" page


def _wiki_search(query, n=3):
    """Wikipedia's own search API (no key). Fallback when DuckDuckGo refuses automated searches."""
    words = re.sub(r"\b(who|what|when|where|which|why|how|is|are|was|were|the|a|an|in|of|and|did|does|do|current|"
                   r"currently|latest|today|now|right|please|tell|me|search|find|year|old)\b|[?,.!]", " ", query, flags=re.I)
    words = " ".join(words.split()) or query
    q = urllib.parse.urlencode({"action": "query", "list": "search", "srsearch": words, "srlimit": n, "format": "json"})
    hits = json.loads(_get(f"https://en.wikipedia.org/w/api.php?{q}"))["query"]["search"]
    out = []
    for i, h in enumerate(hits):
        url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(h["title"].replace(" ", "_"))
        line = f"- {h['title']}\n  {url}\n  {_text(h['snippet'])}"
        if i == 0:                                   # the top article's intro: usually holds the answer
            try:
                s = json.loads(_get("https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(h["title"].replace(" ", "_"))))
                line += "\n  " + s.get("extract", "")[:900]
            except Exception:
                pass
        out.append(line)
    return "\n".join(out)


def web_search(query):
    try:
        page = _get("https://html.duckduckgo.com/html/", urllib.parse.urlencode({"q": query}).encode())
    except Exception:                                # timeout / refused: treat like a blocked search
        page = "challenge-form"
    links = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.S)
    snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)
    out, urls = [], []
    for (u, t), s in list(zip(links, snips))[:6]:
        u = urllib.parse.unquote(re.sub(r"^.*?uddg=", "", u).split("&rut=")[0])
        urls.append(u)
        out.append(f"- {_text(t)}\n  {u}\n  {_text(s)}")
    if not out:
        # Before: a bot-check page came back as "No results found." and the assistant told the user the facts
        # didn't exist. Now: say search was blocked, and try Wikipedia instead.
        blocked = bool(SEARCH_BLOCKED.search(page))
        try:
            wiki = _wiki_search(query)
        except Exception:
            wiki = ""
        if wiki:
            return (("Web search is blocked right now (DuckDuckGo asked for a bot check). " if blocked else "")
                    + "Wikipedia results (may not have live prices or today's news):\n" + wiki)
        if blocked:
            return ("Error: web search is unavailable right now (DuckDuckGo blocked automated searches). Tell the user you "
                    "couldn't search; don't say the information doesn't exist.")
        return "No results found."
    extracts = []
    for u in [u for u in urls if "duckduckgo.com" not in u][:2]:
        try:
            extracts.append(f"FROM {urllib.parse.urlparse(u).netloc}: " + read_webpage(u)[:900])
        except Exception:
            pass
    return "\n".join(out) + ("\n\nTOP PAGES (already read for you):\n" + "\n\n".join(extracts) if extracts else "")


def read_webpage(url):
    page = _get(url)
    page = re.sub(r"(?is)<(script|style|nav|footer|header|noscript|svg)[^>]*>.*?</\1>", " ", page)
    return re.sub(r"\s+", " ", _text(page))[:MAX_OUT] or "Page had no readable text."


def get_weather(place):
    d = json.loads(_get(f"https://wttr.in/{urllib.parse.quote(place)}?format=j1"))
    c, today, tom = d["current_condition"][0], d["weather"][0], d["weather"][1]
    rain = max(int(h.get("chanceofrain", 0)) for h in tom["hourly"])
    return (f"{place} now: {c['weatherDesc'][0]['value']}, {c['temp_C']}°C (feels {c['FeelsLikeC']}°C), humidity {c['humidity']}%, "
            f"wind {c['windspeedKmph']} km/h. Today: {today['mintempC']} to {today['maxtempC']}°C. "
            f"Tomorrow: {tom['mintempC']} to {tom['maxtempC']}°C, {tom['hourly'][4]['weatherDesc'][0]['value']}, "
            f"chance of rain up to {rain}%.")


def convert_currency(amount, from_currency, to_currency):
    f, t = from_currency.strip().upper()[:3], to_currency.strip().upper()[:3]
    d = json.loads(_get(f"https://open.er-api.com/v6/latest/{f}"))
    rate = d["rates"][t]
    amt = float(str(amount).replace(",", ""))
    return f"{amt:g} {f} = {amt * rate:,.2f} {t} (1 {f} = {rate:.4f} {t}, rates of {d.get('time_last_update_utc', '')[:16]})"


def get_news(topic="India"):
    rss = _get(f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(topic)}&hl=en-IN&gl=IN&ceid=IN:en")
    items = re.findall(r"<item>.*?<title>(.*?)</title>.*?<pubDate>(.*?)</pubDate>.*?</item>", rss, re.S)
    return "\n".join(f"- {_text(t)} ({d[:16]})" for t, d in items[:10]) or "No news found."


CITY_TZ = {"india": "Asia/Kolkata", "kolkata": "Asia/Kolkata", "delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata",
           "bangalore": "Asia/Kolkata", "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo", "london": "Europe/London",
           "uk": "Europe/London", "paris": "Europe/Paris", "berlin": "Europe/Berlin", "dubai": "Asia/Dubai",
           "singapore": "Asia/Singapore", "sydney": "Australia/Sydney", "new york": "America/New_York",
           "usa": "America/New_York", "los angeles": "America/Los_Angeles", "san francisco": "America/Los_Angeles",
           "california": "America/Los_Angeles", "chicago": "America/Chicago", "toronto": "America/Toronto",
           "beijing": "Asia/Shanghai", "china": "Asia/Shanghai", "hong kong": "Asia/Hong_Kong", "moscow": "Europe/Moscow",
           "seoul": "Asia/Seoul", "bangkok": "Asia/Bangkok", "dhaka": "Asia/Dhaka", "karachi": "Asia/Karachi",
           "kathmandu": "Asia/Kathmandu", "riyadh": "Asia/Riyadh", "cairo": "Africa/Cairo", "nairobi": "Africa/Nairobi",
           "sao paulo": "America/Sao_Paulo", "mexico city": "America/Mexico_City", "auckland": "Pacific/Auckland"}


def world_time(place):
    from zoneinfo import ZoneInfo, available_timezones
    key = place.strip().lower()
    tz = CITY_TZ.get(key) or next((z for z in sorted(available_timezones())
                                    if z.split("/")[-1].replace("_", " ").lower() == key), None)
    if not tz:
        return f"I don't know the time zone of '{place}'. Try a big city or country name."
    now = datetime.now(ZoneInfo(tz))
    return f"Time in {place}: {now:%I:%M %p, %A %d %B} ({tz}, UTC{now:%z})."


# ======================================================== this PC
def _screen_resolution():
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        w, h = ctypes.windll.gdi32.GetDeviceCaps(hdc, 118), ctypes.windll.gdi32.GetDeviceCaps(hdc, 117)  # DESKTOPHORZRES/VERTRES
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return f"{w}x{h}"
    except Exception:
        return "unknown"


def _windows_name():
    # sys.getwindowsversion() asks the kernel. platform.version() goes through WMI on Python 3.12+, and when the WMI
    # service is stuck it never returns (seen 20 Nov: "which Windows version" froze the router).
    try:
        build = sys.getwindowsversion().build
        return f"Windows {'11' if build >= 22000 else '10'} (build {build})"
    except Exception:
        return "Windows (version unknown)"


def system_info():
    disks = []
    for p in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(p.mountpoint)
            disks.append(f"{p.device} {u.free/1e9:.1f} GB free of {u.total/1e9:.0f} GB")
        except Exception:
            pass
    vm = psutil.virtual_memory()
    bat = psutil.sensors_battery()
    if bat:
        left = "" if bat.power_plugged or bat.secsleft in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN) \
            else f", about {bat.secsleft // 3600} h {bat.secsleft % 3600 // 60} min left"
        b = f"{bat.percent:.0f}% {'plugged in and charging' if bat.power_plugged else 'NOT charging, running on battery'}{left}"
    else:
        b = "no battery"
    apps = {}
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            n = (p.info["name"] or "?").removesuffix(".exe")
            apps[n] = apps.get(n, 0) + p.info["memory_info"].rss
        except Exception:
            pass
    top = sorted(apps.items(), key=lambda x: -x[1])[:6]
    up = timedelta(seconds=int(time.time() - psutil.boot_time()))
    return (f"Time: {datetime.now():%A %d %B %Y, %I:%M %p}\nOS: {_windows_name()}\n"
            f"CPU: {psutil.cpu_count(False)} cores, {psutil.cpu_percent(0.3):.0f}% busy\n"
            f"RAM: {vm.available/1e9:.1f} GB free of {vm.total/1e9:.1f} GB\n"
            f"Apps using most RAM: " + ", ".join(f"{n} {m/1e9:.2f} GB" for n, m in top) +
            f"\nRunning processes: {len(psutil.pids())}\nDisks: " + "; ".join(disks) +
            f"\nBattery: {b}\nScreen: {_screen_resolution()}\n"
            f"PC on for: {up.days} days {up.seconds // 3600} hours (since {datetime.fromtimestamp(psutil.boot_time()):%d %b %I:%M %p})")


def network_info():
    import socket
    ips = [f"{n}: {a.address}" for n, addrs in psutil.net_if_addrs().items() for a in addrs
           if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254."))]
    try:
        out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True, timeout=10,
                             creationflags=subprocess.CREATE_NO_WINDOW).stdout
        ssid = re.search(r"^\s*SSID\s*:\s*(.+)$", out, re.M)
        sig = re.search(r"^\s*Signal\s*:\s*(.+)$", out, re.M)
        wifi = (f'connected to the network named "{ssid.group(1).strip()}" (signal {sig.group(1).strip() if sig else "?"})'
                if ssid else "not connected to Wi-Fi")
    except Exception:
        wifi = "unknown"
    try:
        public = _get("https://api.ipify.org", timeout=8).strip()
    except Exception:
        public = "unknown (offline?)"
    return f"Wi-Fi: {wifi}\nLocal IP addresses: " + "; ".join(ips) + f"\nPublic IP: {public}"


def installed_apps(name_contains=""):
    import winreg
    names = set()
    for root, path in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                       (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                       (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")):
        try:
            with winreg.OpenKey(root, path) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    try:
                        with winreg.OpenKey(k, winreg.EnumKey(k, i)) as sub:
                            n = winreg.QueryValueEx(sub, "DisplayName")[0]
                            try:
                                v = winreg.QueryValueEx(sub, "DisplayVersion")[0]
                            except OSError:
                                v = ""
                            names.add(f"{n} {v}".strip())
                    except OSError:
                        pass
        except OSError:
            pass
    CATEGORIES = {"browser": ["chrome", "edge", "firefox", "brave", "opera", "vivaldi", "tor browser", "arc"],
                  "office": ["office", "word", "excel", "powerpoint", "libreoffice", "onlyoffice"],
                  "antivirus": ["defender", "avast", "avg", "norton", "mcafee", "kaspersky", "bitdefender", "eset", "malwarebytes"],
                  "editor": ["visual studio code", "notepad++", "sublime", "pycharm", "intellij", "vim"],
                  "media player": ["vlc", "media player", "potplayer", "mpc"], "python": ["python"]}
    key = name_contains.lower().strip().rstrip("s")
    words = next((v for k, v in CATEGORIES.items() if k in key or key in k), None) if key else None
    if words:
        hits = sorted(n for n in names if any(w in n.lower() for w in words))
        # Edge ships with Windows and often isn't in the uninstall list
        if key.startswith("browser") and not any("edge" in h.lower() for h in hits) and \
                Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe").exists():
            hits.append("Microsoft Edge (built into Windows)")
    else:
        hits = sorted(n for n in names if name_contains.lower() in n.lower())
    if name_contains and not hits:
        return f"No installed app matching '{name_contains}'."
    return f"{len(hits)} installed apps" + (f" matching '{name_contains}'" if name_contains else "") + ":\n" + "\n".join(hits[:80])


# ======================================================== files
def list_files(path):
    p = _path(path)
    if not p.exists():
        return f"{p} does not exist."
    if p.is_file():
        return f"{p} is a file ({size_str(p.stat().st_size)})."
    rows = []
    for c in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))[:80]:
        try:
            rows.append(f"[folder] {c.name}" if c.is_dir() else f"{c.name}  ({size_str(c.stat().st_size)})")
        except OSError:
            pass
    return f"{p} ({len(rows)} items):\n" + "\n".join(rows) if rows else f"{p} is empty (no files or folders)."


def read_file(path):
    p = _path(path)
    if not p.exists():
        return f"{p} does not exist."
    if p.stat().st_size > 5_000_000:
        return "File is too big to read (over 5 MB)."
    if p.suffix.lower() in (".pdf", ".docx", ".xlsx", ".pptx", ".zip", ".exe", ".jpg", ".png", ".mp4"):
        return f"{p.name} is a {p.suffix} file ({size_str(p.stat().st_size)}); I can only read plain text files."
    return p.read_text(encoding="utf-8", errors="replace")[:MAX_OUT]


def write_file(path, content):
    p = _path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Saved {p} ({len(content)} characters)."


class _SHFILEOP(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR)]


def delete_file(path):
    p = _path(path).resolve()
    if not p.exists():
        return f"{p} does not exist."
    op = _SHFILEOP(None, 3, str(p) + "\0", None, 0x40 | 0x10 | 0x4 | 0x400, False, None, None)
    r = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return f"Moved {p} to the Recycle Bin." if r == 0 else f"Could not delete {p} (error {r})."


def move_file(source, destination):
    import shutil
    s, d = _path(source), _path(destination)
    if not s.exists():
        return f"{s} does not exist."
    if d.is_dir():
        d = d / s.name
    shutil.move(str(s), str(d))
    return f"Moved {s} to {d}."


def run_command(command):
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                       capture_output=True, text=True, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
    out = r.stdout.strip()
    if r.stderr.strip():
        out += "\nERROR (fix the command and try a DIFFERENT one, or use run_python):\n" + r.stderr.strip()[:800]
    return (out or f"(no output, exit code {r.returncode})")[:MAX_OUT]


# Runs before the model's Python code (unless the user pressed Allow). Python's audit hooks see every file open, delete,
# rename, program start, network connection and native-code load - whatever trick the code uses to reach them.
# Reading is fine; changing anything outside the temp folder is stopped and reported, so Jarvis can ask first.
PY_GUARD = r"""
import os as _o, sys as _s, tempfile as _t
def _jarvis_guard():
    tmp = _o.path.normcase(_o.path.realpath(_t.gettempdir()))
    W = _o.O_WRONLY | _o.O_RDWR | _o.O_CREAT | _o.O_APPEND | _o.O_TRUNC
    def in_tmp(p):
        try:
            p = _o.path.normcase(_o.path.abspath(_o.fsdecode(p)))
        except Exception:
            return False
        return p == tmp or p.startswith(tmp + _o.sep)
    PATH_EVENTS = {"os.remove", "os.rmdir", "os.rename", "os.mkdir", "os.chmod", "os.truncate", "os.link", "os.symlink",
                   "shutil.rmtree", "shutil.copyfile", "shutil.copymode", "shutil.copystat", "shutil.move", "shutil.chown"}
    BLOCK = ("subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.startfile", "os.kill", "os.fork",
             "ctypes.", "socket.connect", "socket.sendto", "socket.bind", "urllib.Request", "_winapi.", "winreg.Create",
             "winreg.Delete", "winreg.Set", "webbrowser.")
    def hook(ev, args):
        bad = None
        if ev == "open" and args and not isinstance(args[0], int):
            mode, flags = (args[1] if len(args) > 1 else None), (args[2] if len(args) > 2 else 0)
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (mode is None and (flags or 0) & W)
            if writing and not in_tmp(args[0]):
                bad = "write to " + _o.fsdecode(args[0])
        elif ev in PATH_EVENTS:
            target = args[1] if ev in ("os.rename", "shutil.copyfile", "shutil.move", "os.link", "os.symlink") and len(args) > 1 else args[0]
            if not in_tmp(target) or (ev in ("os.rename", "shutil.move") and not in_tmp(args[0])):
                bad = ev + " " + _o.fsdecode(str(target))
        elif ev.startswith(BLOCK):
            bad = ev
        if bad:
            _s.stderr.write("JARVIS_GUARD_BLOCKED: " + bad[:200] + "\n")
            raise PermissionError("Blocked by Jarvis safety guard: " + bad[:200])
    _s.addaudithook(hook)
_jarvis_guard(); del _jarvis_guard
"""


def run_python(code, guarded=True):
    """guarded: changes outside the temp folder, starting programs and the internet are stopped (see PY_GUARD).
    The agent re-runs unguarded only after the user pressed Allow."""
    r = subprocess.run([sys.executable, "-I", "-B", "-c", (PY_GUARD + "\n" if guarded else "") + code], capture_output=True, text=True,
                       timeout=180, creationflags=subprocess.CREATE_NO_WINDOW, cwd=str(Path.home()))
    out = r.stdout.strip()
    blocked = re.findall(r"JARVIS_GUARD_BLOCKED: (.+)", r.stderr or "")
    if blocked:
        return f"GUARD_BLOCKED: {blocked[0]}\n{out[:1500]}"
    if r.stderr.strip():
        err = r.stderr.strip()[-800:]
        hint = " Only the Python standard library and psutil are available - do NOT install packages." if "ModuleNotFound" in err else ""
        out += f"\nERROR (fix the code and try again).{hint}\n{err}"
    return (out or "(the code printed nothing - use print())")[:MAX_OUT]


IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".tif", ".tiff", ".raw", ".svg"}
KINDS = {"image": IMAGE_EXT, "photo": IMAGE_EXT, "picture": IMAGE_EXT,
         "video": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm"},
         "audio": {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}, "music": {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"},
         "document": {".pdf", ".doc", ".docx", ".txt", ".md", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".odt", ".rtf"},
         "pdf": {".pdf"}, "python": {".py"}, "code": {".py", ".js", ".ts", ".java", ".c", ".cpp", ".cs", ".go", ".rs", ".html", ".css"},
         "zip": {".zip", ".rar", ".7z"}, "program": {".exe", ".msi"}}
SKIP_DIRS = {"windows", "$recycle.bin", "system volume information", "program files", "program files (x86)",
             "programdata", "appdata", "node_modules", ".git", "__pycache__", ".venv", "venv"}


def _scan(root, deadline, on_file, skip=True):
    stack = [root]
    while stack:
        if time.time() > deadline:
            return False
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if not skip or (e.name.lower() not in SKIP_DIRS and not e.name.startswith("$")):
                                stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            on_file(e)
                    except OSError:
                        pass
        except OSError:
            pass
    return True


def _roots(path):
    return [p.mountpoint for p in psutil.disk_partitions()] if str(path).lower() in ("all", "everywhere", "pc", "", "system", "computer") else [str(_path(path))]


def find_files(path, kind="", name_contains=""):
    kinds = [k.strip() for k in re.split(r",|\band\b|/", kind) if k.strip()]
    if len(kinds) > 1:   # "image, document" -> one count per kind
        return "\n\n".join(find_files(path, k, name_contains) for k in kinds)
    k = kind.lower().strip().rstrip("s")
    exts = KINDS.get(k) or ({"." + k.lstrip(".")} if k else None)
    roots = _roots(path)
    if len(roots) == 1 and not Path(roots[0]).exists():
        return f"{roots[0]} does not exist."
    deadline, found, total = time.time() + 60, [], [0]

    def hit(e):
        if exts and os.path.splitext(e.name)[1].lower() not in exts:
            return
        if name_contains and name_contains.lower() not in e.name.lower():
            return
        found.append(e.path)
        total[0] += e.stat(follow_symlinks=False).st_size

    complete = all([_scan(r, deadline, hit) for r in roots])
    note = "" if complete else " (stopped after 60 s, so the real count is higher)"
    what = f"{kind} " if kind else ""
    what += f"files named *{name_contains}*" if name_contains else "files"
    return (f"Found {len(found)} {what} ({size_str(total[0])}) in {', '.join(roots)}{note}. Windows/app folders skipped."
            + ("\nFiles:\n" + "\n".join(found[:15]) if found else ""))


def largest_files(path, count="10"):
    roots = _roots(path)
    if len(roots) == 1 and not Path(roots[0]).exists():
        return f"{roots[0]} does not exist."
    import heapq
    n, heap, deadline = max(1, min(int(str(count).strip() or 10), 30)), [], time.time() + 60

    def hit(e):
        s = e.stat(follow_symlinks=False).st_size
        if len(heap) < n:
            heapq.heappush(heap, (s, e.path))
        elif s > heap[0][0]:
            heapq.heapreplace(heap, (s, e.path))

    complete = all([_scan(r, deadline, hit, skip=False) for r in roots])
    rows = sorted(heap, reverse=True)
    return (f"Largest files in {', '.join(roots)}{'' if complete else ' (time limit hit, partial)'}:\n"
            + "\n".join(f"{size_str(s):>9}  {p}" for s, p in rows))


def folder_sizes(path):
    root = _path(path)
    if not root.exists():
        return f"{root} does not exist."
    from concurrent.futures import ThreadPoolExecutor
    deadline = time.time() + 90

    def measure(child):
        try:
            if child.is_file():
                return child.stat().st_size, child.name, True
            size = [0]
            ok = _scan(str(child), deadline, lambda e: size.__setitem__(0, size[0] + e.stat(follow_symlinks=False).st_size), skip=False)
            return size[0], child.name + "\\", ok
        except OSError:
            return 0, child.name, True

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(measure, list(root.iterdir())))
    rows = sorted(((s, n) for s, n, _ in results), reverse=True)
    total = sum(s for s, _ in rows)
    note = "" if all(ok for *_, ok in results) else " (time limit hit, some sizes are partial)"
    return (f"Total size of {root}: {size_str(total)}{note}. Biggest items (folders end with \\):\n"
            + "\n".join(f"{size_str(s):>9}  {n}" for s, n in rows[:15]))


def download_file(url, filename=""):
    name = re.sub(r'[<>:"/\\|?*]', "_", filename or Path(urllib.parse.urlparse(url).path).name or "download")
    dest = DOWNLOADS / name
    req = urllib.request.Request(url, headers=UA)
    size = 0
    with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 16):
            size += len(chunk)
            if size > 1_000_000_000:
                raise RuntimeError("stopped: file is over 1 GB")
            f.write(chunk)
    return f"Downloaded {size_str(size)} to {dest}. (Not opened or run.)"


# ======================================================== on-screen / device control
KNOWN_APPS = {"notepad": "notepad.exe", "calculator": "calc.exe", "calc": "calc.exe", "paint": "mspaint.exe",
              "file explorer": "explorer.exe", "explorer": "explorer.exe", "files": "explorer.exe", "task manager": "taskmgr.exe",
              "settings": "ms-settings:", "camera": "microsoft.windows.camera:", "whatsapp": "whatsapp:",
              "spotify": "spotify:", "clock": "ms-clock:", "store": "ms-windows-store:", "photos": "ms-photos:",
              "terminal": "wt.exe", "cmd": "cmd.exe", "command prompt": "cmd.exe", "powershell": "powershell.exe",
              "snipping tool": "ms-screenclip:", "word": "winword", "excel": "excel", "powerpoint": "powerpnt",
              "chrome": "chrome", "google chrome": "chrome", "edge": "msedge", "microsoft edge": "msedge", "firefox": "firefox",
              "vs code": "code", "vscode": "code", "visual studio code": "code"}
WEB_APPS = {"youtube": "https://www.youtube.com", "gmail": "https://mail.google.com", "whatsapp": "https://web.whatsapp.com",
            "spotify": "https://open.spotify.com", "instagram": "https://www.instagram.com", "chatgpt": "https://chatgpt.com",
            "claude": "https://claude.ai", "github": "https://github.com", "maps": "https://maps.google.com",
            "google": "https://www.google.com", "netflix": "https://www.netflix.com", "facebook": "https://www.facebook.com",
            "twitter": "https://x.com", "x": "https://x.com", "linkedin": "https://www.linkedin.com", "amazon": "https://www.amazon.in",
            "google drive": "https://drive.google.com", "drive": "https://drive.google.com", "calendar": "https://calendar.google.com"}
START_MENUS = [Path(os.environ.get("ProgramData", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
               Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs"]


def find_app(name):
    n = name.strip().lower().removesuffix(" app").removeprefix("the ")
    if n in KNOWN_APPS:
        return ("known", KNOWN_APPS[n])
    best = None
    for root in START_MENUS:
        for lnk in root.rglob("*.lnk") if root.exists() else []:
            s = lnk.stem.lower()
            if "uninstall" not in s and n in s and (best is None or len(s) < len(best.stem)):
                best = lnk
    if best:
        return ("shortcut", str(best))
    if n in WEB_APPS:
        return ("web", WEB_APPS[n])
    import difflib   # typo-tolerant: "notepadd" -> notepad, "crome" -> chrome
    names = list(KNOWN_APPS) + list(WEB_APPS)
    for root in START_MENUS:
        names += [l.stem.lower() for l in root.rglob("*.lnk")] if root.exists() else []
    close = difflib.get_close_matches(n, names, n=1, cutoff=0.8)
    if close and close[0] != n:
        return find_app(close[0])
    return None


def open_app(name):
    hit = find_app(name)
    if not hit:
        return f"No app called '{name}' found on this PC."
    kind, target = hit
    if kind == "web":
        webbrowser.open(target); return f"Opened {name} in the browser."
    try:
        os.startfile(target)
    except OSError:
        n = name.strip().lower()
        if n in WEB_APPS:
            webbrowser.open(WEB_APPS[n]); return f"Opened {name} in the browser."
        return f"Couldn't open {name}."
    return f"Opened {Path(target).stem if kind == 'shortcut' else name}."


def close_app(name):
    n = name.strip().lower().removesuffix(".exe")
    procs = [p for p in psutil.process_iter(["name"]) if n in (p.info["name"] or "").lower()]
    if not procs:
        return f"No running app matching '{name}'."
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    return f"Closed {len(procs)} process(es) of {name}."


def open_url(url):
    if not re.match(r"https?://", url):
        url = "https://" + url.strip()
    webbrowser.open(url); return f"Opened {url}"


def _chrome_exe():
    for p in (Path(os.environ.get("ProgramFiles", "")) / r"Google\Chrome\Application\chrome.exe",
              Path(os.environ.get("ProgramFiles(x86)", "")) / r"Google\Chrome\Application\chrome.exe",
              Path(os.environ.get("LOCALAPPDATA", "")) / r"Google\Chrome\Application\chrome.exe"):
        if p.exists():
            return str(p)
    return None


def _yes(v):
    return str(v).strip().lower() in ("yes", "true", "1", "y", "incognito", "private", "on")


def _browse(url, incognito=False):
    """Open a web address; incognito -> Chrome --incognito (or Edge InPrivate)."""
    if incognito:
        exe = _chrome_exe()
        if exe:
            subprocess.Popen([exe, "--incognito", url], creationflags=subprocess.CREATE_NO_WINDOW)
            return "a Chrome incognito window"
        edge = next((p for p in (Path(os.environ.get("ProgramFiles(x86)", "")) / r"Microsoft\Edge\Application\msedge.exe",
                                 Path(os.environ.get("ProgramFiles", "")) / r"Microsoft\Edge\Application\msedge.exe") if p.exists()), None)
        if edge:
            subprocess.Popen([str(edge), "--inprivate", url], creationflags=subprocess.CREATE_NO_WINDOW)
            return "an Edge InPrivate window"
    webbrowser.open(url)
    return "the browser"


def _youtube_first(query):
    """First video for a search on YouTube -> (video_id, title) or (None, None). Retries, then asks DuckDuckGo."""
    page = ""
    for timeout in (8, 15):
        try:
            page = _get("https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query), timeout=timeout)
            if '"videoId"' in page:
                break
        except Exception:
            time.sleep(0.5)
    if '"videoId"' not in page:
        try:
            ddg = _get("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query + " site:youtube.com"), timeout=10)
            m = re.search(r"youtube\.com%2Fwatch%3Fv%3D([\w-]{11})|youtube\.com/watch\?v=([\w-]{11})", ddg)
            if m:
                return m.group(1) or m.group(2), None
        except Exception:
            pass
        return None, None
    m = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})".*?"title":\{"runs":\[\{"text":"(.*?)"\}', page)
    if m:
        return m.group(1), json.loads('"' + m.group(2) + '"')
    m = re.search(r'"videoId":"([\w-]{11})"', page)
    return (m.group(1), None) if m else (None, None)


def play_video(query, incognito="no"):
    """Find a video/song on YouTube and start playing it (optionally in a private window)."""
    q = (query or "").strip()
    if len(q.split()) > 9 or re.search(r"\b(tab|already|right now|currently|that was playing|was playing|opened|is playing)\b", q, re.I):
        return ("NOT DONE: that isn't a song name - the user means something ALREADY open. Use media_control (play / resume) "
                "for what's already open, or ask which song to play.")
    vid, title = _youtube_first(query)
    inc = _yes(incognito)
    if not vid:
        where = _browse("https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query), inc)
        return f"Couldn't read YouTube's results, so I opened the search for '{query}' in {where}. It is NOT playing yet."
    where = _browse(f"https://www.youtube.com/watch?v={vid}", inc)
    return f"Playing '{title or query}' on YouTube in {where}."


def open_incognito(url=""):
    """Private / incognito browser window, optionally at a web address."""
    u = url.strip()
    if u and not re.match(r"https?://", u):
        u = "https://" + u
    where = _browse(u or "about:blank", True)
    return f"Opened {where}" + (f" at {u}." if u else ".")


def play_music(query, incognito="no"):
    no_spotify = any("spotify" in f.lower() and re.search(r"(don'?t|do not|never|\bno\b|avoid|stop)", f.lower())
                     for f in load_json(MEMORY, []))
    generic = re.fullmatch(r"\s*(play\s+)?(a |the |some |any |background |nice |good |random |new )*(music|songs?|tunes|something)\s*",
                           query.lower() or "music")
    if generic:
        query = "relaxing background music" if "background" in query.lower() else "top hits songs"
    if _yes(incognito):
        return play_video(query, "yes")
    if not no_spotify and find_app("spotify"):
        try:
            os.startfile("spotify:search:" + urllib.parse.quote(query)); return f"Opened Spotify's search for {query} (press play there)."
        except OSError:
            pass
    return play_video(query)


def _key(vk, times=1):
    for _ in range(times):
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0); ctypes.windll.user32.keybd_event(vk, 0, 2, 0)


def _endpoint_volume():
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass
    from pycaw.pycaw import AudioUtilities
    return AudioUtilities.GetSpeakers().EndpointVolume


def set_volume(direction="", level=""):
    """Speaker volume: up / down / mute / unmute / exact level (0-100). Reports the real level."""
    d = f"{direction} {level}".lower()
    num = re.search(r"\d+", d)
    try:
        v = _endpoint_volume()
        cur = round(v.GetMasterVolumeLevelScalar() * 100)
    except Exception:
        v = None
    if v is None:                               # fallback: media keys (2% per press)
        if "mute" in d:
            _key(0xAD); return "Toggled mute."
        if any(w in d for w in ("down", "lower", "decrease", "quiet", "less")):
            _key(0xAE, 5); return "Volume down."
        _key(0xAF, 5); return "Volume up."
    if num:
        target = max(0, min(100, int(num.group())))
    elif "unmute" in d:
        v.SetMute(0, None); return f"Unmuted. Volume is {cur}%."
    elif "mute" in d:
        v.SetMute(1, None); return f"Muted (volume was {cur}%)."
    elif any(w in d for w in ("what", "current", "get", "level?")):
        return f"Volume is {cur}%" + (" (muted)." if v.GetMute() else ".")
    elif any(w in d for w in ("down", "lower", "decrease", "quiet", "less", "reduce")):
        target = max(0, cur - 10)
    else:
        target = min(100, cur + 10)
    v.SetMute(0, None)
    v.SetMasterVolumeLevelScalar(target / 100, None)
    return f"Volume set to {target}% (it was {cur}%)."


# ======================================================== Jarvis's own settings (the app plugs in APP["set"])
APP = {"set": None}
_THEME_WORDS = {"crimson": "crimson", "red": "crimson", "ember": "crimson", "emerald": "emerald", "green": "emerald", "jade": "emerald",
                "glass": "glass", "clear": "glass", "transparent": "glass", "silver": "glass", "white": "glass", "default": "glass"}


_NUM_WORDS = {"zero": 0, "half": 0.5, "one": 1, "once": 1, "single": 1, "normal": 1, "two": 2, "twice": 2, "double": 2,
              "three": 3, "thrice": 3, "triple": 3}
_DIGIT_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
                "eight": "8", "nine": "9"}


def speed_from_words(text):
    """The speaking speed the user actually said: 'two times' -> '2', 'one point five x' -> '1.5', 'slowest' -> '0.6',
    'normal' -> '1.0'. None if they gave no exact speed ('a bit faster' is left to the model)."""
    t = (text or "").lower().replace("-", " ")
    if re.search(r"\b(slowest|minimum speed|as slow as)", t):
        return "0.6"
    if re.search(r"\b(fastest|maximum speed|max speed|as fast as)", t):
        return "1.8"
    m = re.search(r"\b(zero|one|two|three)\s+point\s+(zero|one|two|three|four|five|six|seven|eight|nine)\b", t)
    if m:
        return f"{_NUM_WORDS[m.group(1)]:g}.{_DIGIT_WORDS[m.group(2)]}"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(x\b|times?\b|speed\b)", t) or re.search(r"\bspeed (?:to |at |of )?(\d+(?:\.\d+)?)\b", t)
    if m:
        return m.group(1)
    if re.search(r"\bone and a half\b", t):
        return "1.5"
    m = re.search(r"\b(half|once|one|single|two|twice|double|three|thrice|triple)\s*(x\b|times?\b|speed\b)", t) \
        or re.search(r"\b(twice|double|triple|half) (as )?(fast|speed)", t)
    if m:
        return f"{_NUM_WORDS[m.group(1)]:g}"
    if re.search(r"\b(normal|default|regular|original|usual)\b", t):
        return "1.0"
    return None


def jarvis_settings(setting, value):
    """Change Jarvis itself: theme/colour, speaking speed, spoken replies, wake word, follow-up listening."""
    if not APP["set"]:
        return "Error: settings are only available inside the Jarvis app."
    k, v = setting.lower(), str(value).lower().strip()
    if re.search(r"remind|alarm|timer|notification|ring", k + " " + v) and re.search(r"sound|tone|tune|melody|chime|bell|beep|silent|ring|music", k + " " + v):
        pick = ("melody" if re.search(r"melod|tune|song|music", v) else "bell" if "bell" in v else "beep" if "beep" in v
                else "none" if re.search(r"none|silent|no sound|off|mute", v) else "chime" if re.search(r"chime|default|normal", v) else None)
        if not pick:
            return "Error: reminder sounds are Chime, Melody, Bell, Beep or Silent."
        return APP["set"]("reminder_sound", pick)
    if re.search(r"colou?r|theme|look|skin", k):
        theme = next((t for w, t in _THEME_WORDS.items() if w in v), None)
        if not theme:
            return "Error: themes are Glass, Emerald (green) and Crimson (red)."
        return APP["set"]("theme", theme)
    if re.search(r"speed|fast|slow|pace|rate", k + " " + v):
        return APP["set"]("speech_speed", v)
    if re.search(r"accent|voice type|different voice|which voice|your voice|sound like|male|female|british|american|alan|ryan|lessac|northern", k + " " + v) \
            and not re.fullmatch(r"(on|off|yes|no|true|false|enable|disable|enabled|disabled|mute|unmute)", v):
        pick = ("en_GB-northern_english_male-medium" if "northern" in v else "en_US-ryan-high" if re.search(r"ryan|american male|us male|richer", v)
                else "en_GB-alan-medium" if re.search(r"alan|british|uk|english male|male", v)
                else "en_US-lessac-medium" if re.search(r"lessac|female|woman|default|original|american|us\b", v) else None)
        if not pick:
            return "Error: voices are Lessac (US), Alan (British male), Northern (British male), Ryan (US male)."
        return APP["set"]("speaker_voice", pick)
    onoff = not re.search(r"\b(off|no|false|disable|stop|mute)\b", v)
    if re.search(r"wake|hey jarvis", k):
        return APP["set"]("wake", onoff)
    if re.search(r"follow", k):
        return APP["set"]("followup", onoff)
    if re.search(r"voice|speak|spoken|talk|read aloud", k):
        return APP["set"]("voice", onoff)
    return ("Error: I can change: theme (glass/emerald/crimson), speaking speed, my voice (Lessac, Alan, Northern, Ryan), "
            "reminder sound (chime/melody/bell/beep/silent), spoken replies, wake word, follow-up listening.")


APP_DIR = Path(__file__).resolve().parent


def _ps_file(script, *args, timeout=30):
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(APP_DIR / script), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    return (r.stdout or "").strip()


def media_control(action):
    """Music/video ALREADY open anywhere (a Chrome tab, Spotify...), via Windows' media controls - so it controls the real
    tab and reports what actually happened. Falls back to the keyboard media keys."""
    a = (action or "").lower()
    act = ("list" if re.search(r"status|what|now playing|which|list", a) else "next" if re.search(r"next|skip", a) else
           "previous" if re.search(r"prev|back|last track", a) else "stop" if "stop" in a else "pause" if "pause" in a else
           "play" if re.search(r"play|resume|continue|unpause|start", a) else "toggle")
    try:
        out = _ps_file("winrt_media.ps1", "-Action", act, timeout=15)
    except Exception:
        out = ""
    if out.startswith("NONE"):
        return "Nothing is open that plays music or video right now (no media tab or app). Ask the user what to play."
    if out and act == "list":
        rows = [r.split("\t") for r in out.splitlines()]
        return "Media open now: " + "; ".join(f"{(r[2] if len(r) > 2 and r[2] != 'A site is playing media' else 'a browser tab')} "
                                             f"in {r[1] if len(r) > 1 else '?'} ({r[0].lower()})" for r in rows)
    if out and "\t" in out:
        ok, status, app, title = (out.split("\t") + ["", "", "", ""])[:4]
        name = "the media" if not title or title == "A site is playing media" else f"'{title}'"
        where = "Chrome" if "chrome" in app.lower() else app
        verb = {"play": "Resumed", "pause": "Paused", "stop": "Stopped", "next": "Skipped to the next item in",
                "previous": "Went back in", "toggle": "Toggled"}[act]
        if ok.lower() != "true":
            return f"Error: Windows wouldn't {act} {name} in {where}. It is {status.lower()}."
        return f"{verb} {name} in {where}. It is now {status.lower()}."
    vk, msg = ((0xB0, "Pressed the next-track key.") if act == "next" else (0xB1, "Pressed the previous-track key.") if act == "previous"
               else (0xB2, "Pressed the stop key.") if act == "stop" else (0xB3, "Pressed the play/pause key."))
    _key(vk)
    return msg + " (couldn't check what is playing)"


def read_screen(window=""):
    """Read the text visible on screen with Windows' built-in offline OCR. Read-only: never clicks, nothing is saved or sent."""
    w = (window or "").strip()
    if w.lower() in ("active", "current", "front", "foreground", "my screen", "screen", "whole screen", "everything", "all"):
        w = "screen" if w.lower() in ("screen", "whole screen", "everything", "all", "my screen") else ""
    try:
        out = _ps_file("winrt_ocr.ps1", *(["-Window", w] if w else []), timeout=40)
    except Exception as e:
        return f"Error: couldn't read the screen ({e})."
    note = ""
    if out.startswith("NO WINDOW") and w and w != "screen":     # misheard name ("clod" for Claude) -> the window in front
        note = f"(No window has '{w}' in its title, so this is the window in front.) "
        out = _ps_file("winrt_ocr.ps1", timeout=40)
    if not out or out.startswith(("NO WINDOW", "NO OCR")):
        return out or "Error: couldn't read the screen."
    lines = out.splitlines()
    title = lines[0].replace("WINDOW: ", "")
    body = "\n".join(l for l in lines[1:] if len(l.strip()) > 1)
    return (note + f"Text visible in the window '{title}' (read by OCR, may contain small mistakes):\n" + (body or "(no readable text)"))[:MAX_OUT]


MMPROJ = Path(__file__).resolve().parents[1] / "models" / "mmproj-gemma-4-E2B-it-Q8_0.gguf"   # optional: setup.ps1 -Vision
IMAGE_MARK = "IMAGE:"     # a tool result starting with this = a picture for the brain to look at (agent attaches it)


def _view(mode, path=""):
    import tempfile
    out = Path(tempfile.gettempdir()) / f"jarvis_view_{int(time.time() * 1000)}.jpg"
    res = _ps_file("capture_image.ps1", "-Mode", mode, *(["-Path", path] if path else []), "-Out", str(out), timeout=30)
    if not res.startswith("OK") or not out.exists():
        return f"Error: couldn't get the picture ({res[:150]})."
    return f"{IMAGE_MARK}{out}"


def look_at_screen(question="", window="front"):
    """SEE the screen (local vision model - nothing leaves the PC). For reading text, read_screen is faster and exact."""
    return _view("screen" if str(window).lower() in ("screen", "whole screen", "all", "everything") else "front")


def describe_image(path, question=""):
    """Look at a picture file on this PC (photo, screenshot, diagram)."""
    p = _path(path)
    if not p.exists():
        return f"Error: {p} does not exist."
    if p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff"):
        return "Error: I can only look at JPG, PNG, BMP, GIF or TIFF pictures."
    return _view("file", str(p))


def screenshot():
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    dest = SCREENSHOTS / f"Jarvis_{datetime.now():%Y%m%d_%H%M%S}.png"
    script = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
              "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen; "
              "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height; "
              "$g=[System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size); "
              f"$bmp.Save('{dest}'); $g.Dispose(); $bmp.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=30,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    return f"Screenshot saved to {dest}." if dest.exists() else "Screenshot failed."


def set_clipboard(text):
    subprocess.run(["clip"], input=text.encode("utf-16-le"), check=False, creationflags=subprocess.CREATE_NO_WINDOW)
    return f"Copied to the clipboard: {text[:80]}"


def get_clipboard():
    r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], capture_output=True, text=True,
                       timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    return "Clipboard contains: " + (r.stdout.strip()[:MAX_OUT] or "(empty)")


def lock_pc():
    ctypes.windll.user32.LockWorkStation()
    return "Locked the PC."


SETTINGS = {"wifi": "ms-settings:network-wifi", "bluetooth": "ms-settings:bluetooth", "display": "ms-settings:display",
            "brightness": "ms-settings:display", "notifications": "ms-settings:notifications", "sound": "ms-settings:sound",
            "storage": "ms-settings:storagesense", "battery": "ms-settings:batterysaver", "update": "ms-settings:windowsupdate",
            "windowsupdate": "ms-settings:windowsupdate", "apps": "ms-settings:appsfeatures", "network": "ms-settings:network",
            "personalization": "ms-settings:personalization", "wallpaper": "ms-settings:personalization-background",
            "power": "ms-settings:powersleep", "privacy": "ms-settings:privacy", "mouse": "ms-settings:mousetouchpad",
            "keyboard": "ms-settings:keyboard", "dnd": "ms-settings:notifications", "donotdisturb": "ms-settings:notifications"}


def open_settings(page):
    key = page.lower().replace("-", "").replace(" ", "").replace("_", "").removesuffix("settings")
    p = SETTINGS.get(key) or next((v for k, v in SETTINGS.items() if k in key), None)
    if not p:
        return f"No settings page '{page}'. Options: {', '.join(SETTINGS)}"
    os.startfile(p); return f"Opened {page} settings."


def send_message(to, text):
    try:
        os.startfile("whatsapp://send?text=" + urllib.parse.quote(text))
    except OSError:
        webbrowser.open("https://web.whatsapp.com/send?text=" + urllib.parse.quote(text))
    return f"WhatsApp is open with the message ready. The user picks {to} and presses Send."


# ======================================================== notes, reminders, timers, tasks
def take_note(text):
    with open(NOTES, "a", encoding="utf-8") as f:
        f.write(f"- [{datetime.now():%Y-%m-%d %H:%M}] {text}\n")
    return f"Note saved: {text}"


def read_notes():
    try:
        notes = Path(NOTES).read_text(encoding="utf-8").strip()
    except OSError:
        notes = ""
    return ("Your notes:\n" + notes[-MAX_OUT:]) if notes else "You have no notes yet."


def set_reminder(text, time):
    if re.fullmatch(r"\s*(now|right now|immediately|asap|right away|this moment)\s*", str(time).lower()):
        return ("Not set: a reminder for 'now' is pointless - the user wants this done NOW. Do the task itself with the right tool "
                "(e.g. play_video / play_music / open_app) instead of a reminder.")
    when = parse_time(time)
    if not when:
        return f"Could not understand the time '{time}'. Ask the user for a time like '7 pm' or 'in 20 minutes'."
    items = load_json(REMINDERS, [])
    items.append({"text": text, "when": when.isoformat(timespec="seconds"), "done": False})
    save_json(REMINDERS, items)
    return f"Reminder set for {when:%a %d %b, %I:%M %p}: {text}"


def set_timer(duration, label="Timer"):
    d = str(duration).strip()
    if re.fullmatch(r"\d+(\.\d+)?", d):
        d += " minutes"
    when = parse_time("in " + d if not d.startswith("in ") else d)
    if not when:
        return f"Could not understand '{duration}'. Use e.g. '10 minutes' or '30 seconds'."
    items = load_json(REMINDERS, [])
    items.append({"text": f"{label} done ({d})", "when": when.isoformat(timespec="seconds"), "done": False})
    save_json(REMINDERS, items)
    return f"Timer set for {d} - it goes off at {when:%I:%M:%S %p}."


def snooze_reminder(minutes="5"):
    """The reminder that just went off (last 30 min) comes back in N minutes."""
    m = re.search(r"\d+(\.\d+)?", str(minutes))
    mins = float(m.group()) if m else 5.0
    items = load_json(REMINDERS, [])
    fired = [r for r in items if r.get("fired") and not r.get("missed")
             and (datetime.now() - datetime.fromisoformat(r["fired"])).total_seconds() < 1800]
    if not fired:
        return "No reminder went off in the last 30 minutes, so there's nothing to snooze."
    last = max(fired, key=lambda r: r["fired"])
    when = datetime.now() + timedelta(minutes=mins)
    items.append({"text": last["text"], "when": when.isoformat(timespec="seconds"), "done": False})
    save_json(REMINDERS, items)
    return f"Snoozed: '{last['text']}' comes back at {when:%I:%M %p} (in {mins:g} minutes)."


def list_reminders():
    items = [r for r in load_json(REMINDERS, []) if not r.get("done")]
    if not items:
        return "You have no upcoming reminders or timers."
    return "Upcoming reminders/timers:\n" + "\n".join(
        f"- {datetime.fromisoformat(r['when']):%a %d %b %I:%M %p}: {r['text']}" for r in sorted(items, key=lambda r: r["when"]))


def cancel_reminder(text):
    items = load_json(REMINDERS, [])
    words = [w for w in re.findall(r"\w+", text.lower()) if len(w) > 2 and w not in ("the", "reminder", "timer", "and")]
    hit = [r for r in items if not r.get("done") and (not words or any(w in r["text"].lower() for w in words))]
    if not hit:
        return f"No upcoming reminder matching '{text}'."
    for r in hit:
        r["done"] = True
    save_json(REMINDERS, items)
    return "Cancelled: " + "; ".join(r["text"] for r in hit)


def _list_key(name):
    n = (name or "to-do").lower().strip()
    n = re.sub(r"\b(my|the|list)\b", "", n).strip() or "to-do"
    return {"todo": "to-do", "to do": "to-do", "tasks": "to-do", "task": "to-do", "grocery": "shopping", "groceries": "shopping"}.get(n, n)


def add_task(item, list_name="to-do"):
    data = load_json(TASKS, {})
    key = _list_key(list_name)
    new = [i.strip() for i in re.split(r",| and ", item) if i.strip()]
    data.setdefault(key, []).extend({"item": i, "done": False} for i in new)
    save_json(TASKS, data)
    return f"Added to your {key} list: {', '.join(new)}."


def list_tasks(list_name="to-do"):
    data = load_json(TASKS, {})
    if list_name.lower().strip() in ("all", "", "lists"):
        if not data:
            return "You have no lists yet."
        return "\n".join(f"{k}: " + (", ".join(i["item"] for i in v if not i["done"]) or "empty") for k, v in data.items())
    key = _list_key(list_name)
    items = [i["item"] for i in data.get(key, []) if not i["done"]]
    return f"Your {key} list: " + (", ".join(items) if items else "is empty.")


def complete_task(item, list_name="to-do"):
    data = load_json(TASKS, {})
    key = _list_key(list_name)
    hits = [i for i in data.get(key, []) if not i["done"] and item.lower() in i["item"].lower()]
    for i in hits:
        i["done"] = True
    save_json(TASKS, data)
    return f"Marked done on {key}: {', '.join(i['item'] for i in hits)}" if hits else f"'{item}' isn't on your {key} list."


# ======================================================== chat history (stays on this PC)
CHATS = DATA / "chat_history.jsonl"
_chat_vecs = {}                      # embedding cache: (time, user) -> vector


def save_chat(user, answer):
    """Every finished request is kept (time, what you asked, what Jarvis answered) - shown again after a restart and
    searchable with recall_chats. Plain local file, never sent anywhere."""
    try:
        if CHATS.exists() and CHATS.stat().st_size > 5_000_000:      # keep it small: drop the oldest half
            lines = CHATS.read_text(encoding="utf-8").splitlines()
            CHATS.write_text("\n".join(lines[len(lines) // 2:]) + "\n", encoding="utf-8")
        with open(CHATS, "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "user": user, "answer": answer},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass


def load_chats():
    out = []
    try:
        for line in CHATS.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def _when_range(when):
    """'today', 'yesterday', '3 days ago', 'last week', 'this week', 'monday' -> (start, end) datetimes, or None."""
    w = (when or "").lower().strip()
    if not w or w in ("any", "anytime", "ever", "all"):
        return None
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if "today" in w or "this morning" in w or "earlier" in w:
        return today, today + timedelta(days=1)
    if "yesterday" in w:
        return today - timedelta(days=1), today
    m = re.search(r"(\d+|one|two|three|four|five|six|seven) days? ago", w)
    if m:
        n = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}.get(m.group(1)) or int(m.group(1))
        return today - timedelta(days=n), today - timedelta(days=n - 1)
    if "last week" in w:
        return today - timedelta(days=today.weekday() + 7), today - timedelta(days=today.weekday())
    if "this week" in w:
        return today - timedelta(days=today.weekday()), today + timedelta(days=1)
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, d in enumerate(days):
        if d in w:
            back = (today.weekday() - i) % 7 or 7
            return today - timedelta(days=back), today - timedelta(days=back - 1)
    return None


def _embed(texts):
    """Meaning vectors from the router's small embedding model (already running on port 8101 - no extra model)."""
    import numpy as np
    req = urllib.request.Request("http://127.0.0.1:8101/v1/embeddings", data=json.dumps({"input": texts}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        v = np.array([d["embedding"] for d in json.loads(r.read())["data"]], dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def recall_chats(query="", when=""):
    """Search what you and Jarvis talked about before (saved chats). when: today / yesterday / 3 days ago / last week..."""
    chats = load_chats()
    rng = _when_range(when) or _when_range(query)
    if rng:
        chats = [c for c in chats if rng[0] <= datetime.fromisoformat(c["time"]) < rng[1]]
    if not chats:
        return "No saved conversations " + (f"for {when or query}." if rng else "yet.")
    chats = chats[-400:]
    q = re.sub(r"\b(what|did|we|talk(ed)?|about|chat(ted)?|discuss(ed)?|say|said|ask(ed)?|tell|told|you|me|i|the|our|"
               r"conversation|yesterday|today|earlier|last|week|time|remember|do)\b", " ", (query or "").lower()).strip()
    if q:
        try:
            import numpy as np
            keys = [(c["time"], c["user"]) for c in chats]
            new = [k for k in keys if k not in _chat_vecs]
            for i in range(0, len(new), 64):
                part = new[i:i + 64]
                byk = {(c["time"], c["user"]): c for c in chats}
                vecs = _embed([f"{k[1]} - {byk[k]['answer'][:200]}" for k in part])
                _chat_vecs.update(zip(part, vecs))
            qv = _embed([q])[0]
            scores = np.array([float(_chat_vecs[k] @ qv) for k in keys])
            chats = [chats[i] for i in np.argsort(-scores)[:6] if scores[i] > 0.45] or chats[-6:]
            chats.sort(key=lambda c: c["time"])
        except Exception:                      # embedding model not running: plain word match
            words = set(re.findall(r"\w{3,}", q))
            hit = [c for c in chats if words & set(re.findall(r"\w{3,}", (c["user"] + " " + c["answer"]).lower()))]
            chats = (hit or chats)[-6:]
    else:
        chats = chats[-8:]
    return "Saved conversations:\n" + "\n".join(
        f"- {datetime.fromisoformat(c['time']):%a %d %b %I:%M %p}: you asked \"{c['user'][:150]}\" -> I said \"{c['answer'][:220]}\""
        for c in chats)


# ======================================================== memory, skills
def remember(fact):
    facts = load_json(MEMORY, [])
    m = re.match(r"(User's name is|User likes to be called|User lives in|User's [\w ]+? is) ", fact)
    if m and fact not in facts:          # a new name/city/favourite replaces the old one
        facts = [f for f in facts if not f.startswith(m.group(0))]
        save_json(MEMORY, facts)
    if fact not in facts:
        facts.append(fact)
        save_json(MEMORY, facts)
    return f"Remembered: {fact}"


def _stems(s):
    stop = {"the", "my", "i", "me", "that", "where", "what", "is", "a", "an", "about", "user", "user's", "forget", "do", "you"}
    return {w[:4] for w in re.findall(r"[a-z']+", s.lower()) if w not in stop and len(w) > 2}


def forget(fact):
    facts = load_json(MEMORY, [])
    target = _stems(fact)
    keep = [f for f in facts if not (target & _stems(f))]
    save_json(MEMORY, keep)
    gone = [f for f in facts if f not in keep]
    return f"Forgot: {'; '.join(gone)}" if gone else f"Nothing remembered about '{fact}'."


def _skill_path(name):
    return SKILLS / (re.sub(r"[^a-z0-9_-]+", "_", name.lower()).strip("_") + ".md")


def save_skill(name, when_to_use, steps):
    _skill_path(name).write_text(f"# {name}\nUse when: {when_to_use}\n\n{steps}\n", encoding="utf-8")
    return f"Skill '{name}' saved."


def use_skill(name):
    p = _skill_path(name)
    return p.read_text(encoding="utf-8") if p.exists() else f"No skill called '{name}'."


def list_skills():
    out = []
    for p in sorted(SKILLS.glob("*.md")):
        lines = p.read_text(encoding="utf-8").splitlines()
        out.append(f"{p.stem}: {lines[1].removeprefix('Use when: ') if len(lines) > 1 else ''}")
    return out


def ask_big_ai(question):
    return "The big-AI hand-off is not set up yet. Do your best with your own tools, or tell the user it isn't available yet."


# ======================================================== browser profiles
def chrome_profiles(open_profile=""):
    """List Chrome profiles (from Chrome's own Local State file); optionally open one by name."""
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    try:
        cache = json.load(open(base / "Local State", encoding="utf-8"))["profile"]["info_cache"]
    except Exception:
        return "Chrome profile information not found (is Google Chrome installed?)."
    profs = [(d, v.get("name") or d, v.get("user_name") or "") for d, v in cache.items()]
    lines = [f"- {n}" + (f" ({u})" if u else "") + f"  [folder: {d}]" for d, n, u in profs]
    out = f"Chrome has {len(profs)} profile(s):\n" + "\n".join(lines)
    if open_profile:
        q = open_profile.lower()
        hit = next((p for p in profs if q in p[1].lower() or q in p[2].lower() or q == p[0].lower()), None)
        if not hit:
            return out + f"\nNo profile matching '{open_profile}'."
        exe = next((p for p in (Path(os.environ.get("ProgramFiles", "")) / r"Google\Chrome\Application\chrome.exe",
                                Path(os.environ.get("ProgramFiles(x86)", "")) / r"Google\Chrome\Application\chrome.exe",
                                Path(os.environ.get("LOCALAPPDATA", "")) / r"Google\Chrome\Application\chrome.exe") if p.exists()), None)
        if not exe:
            return out + "\nCouldn't find chrome.exe to open the profile."
        subprocess.Popen([str(exe), f"--profile-directory={hit[0]}"], creationflags=subprocess.CREATE_NO_WINDOW)
        out += f"\nOpened Chrome with profile '{hit[1]}'."
    return out


# ======================================================== desktop control (see desktop.py)
def desktop_look(window=""):
    import desktop
    return desktop.look(window)


def desktop_do(window, steps):
    import desktop   # the agent passes the user's confirm callback; this plain version is for direct use
    return desktop.run_steps(window, [x for x in re.split(r"\n|;", steps) if x.strip()])


# ======================================================== registry (what the model sees)
def _fn(tool_name, desc, fn, **params):
    req = [k for k, v in params.items() if not v.endswith("(optional)")]
    return {"name": tool_name, "fn": fn, "schema": {"type": "function", "function": {
        "name": tool_name, "description": desc,
        "parameters": {"type": "object", "properties": {k: {"type": "string", "description": v} for k, v in params.items()},
                       "required": req}}}}


TOOLS = {t["name"]: t for t in [
    # info
    _fn("web_search", "Search the internet. Returns results AND the text of the top pages.", web_search, query="what to search"),
    _fn("read_webpage", "Read the text of a web page.", read_webpage, url="page address"),
    _fn("get_news", "Latest news headlines on a topic.", get_news, topic="news topic, e.g. India, cricket, tech"),
    _fn("get_weather", "Current weather and tomorrow's forecast (incl. rain chance) for a place.", get_weather, place="city or place"),
    _fn("convert_currency", "Convert money between currencies at today's rate.", convert_currency,
        amount="number", from_currency="3-letter code, e.g. USD", to_currency="3-letter code, e.g. INR"),
    _fn("world_time", "Current time in a city or country.", world_time, place="city or country"),
    # this PC
    _fn("system_info", "This PC now: date/time, Windows version, battery & charging, RAM, top apps by RAM, CPU, disks, "
        "screen resolution, process count, uptime.", system_info),
    _fn("network_info", "Wi-Fi network name, local and public IP addresses.", network_info),
    _fn("installed_apps", "List installed programs, optionally filtered by name (e.g. browsers: chrome, edge, firefox).",
        installed_apps, name_contains="part of the app name (optional)"),
    # files
    _fn("list_files", "List what is inside a folder.", list_files, path="folder path"),
    _fn("read_file", "Read a plain text file (txt, md, csv, code...).", read_file, path="file path"),
    _fn("find_files", "Find and COUNT files by kind (image, video, audio, document, pdf, python...; several kinds comma-separated) or by name, in a folder or 'all'.",
        find_files, path="folder path, or 'all' for the whole PC", kind="file kind (optional)", name_contains="part of the file name (optional)"),
    _fn("largest_files", "The biggest individual files in a folder or drive.", largest_files, path="folder or drive", count="how many (optional)"),
    _fn("folder_sizes", "Total size of a folder and which sub-folders take the most space.", folder_sizes, path="folder or drive"),
    _fn("write_file", "Create or overwrite a text file (user approves).", write_file, path="file path", content="text to write"),
    _fn("move_file", "Move a file or folder (user approves).", move_file, source="file or folder", destination="target folder or path"),
    _fn("delete_file", "Move a file or folder to the Recycle Bin (user approves).", delete_file, path="file or folder path"),
    _fn("download_file", "Download a file to the Downloads folder (user approves). Never opens it.", download_file,
        url="file address", filename="name to save as (optional)"),
    # do anything else
    _fn("run_python", "Run a short Python script (standard library + psutil only, never install packages) and get what it prints. "
        "Use for counting, calculating, analysing files, anything no other tool does.", run_python, code="Python code that print()s the result"),
    _fn("run_command", "Run a PowerShell command. Look-only commands run instantly; commands that change things need the user's OK.",
        run_command, command="PowerShell command"),
    # apps & device
    _fn("open_app", "Open an app (or its website if not installed).", open_app, name="app name"),
    _fn("close_app", "Close a running app (user approves).", close_app, name="app name, e.g. notepad, chrome"),
    _fn("open_url", "Open a website in the browser.", open_url, url="web address"),
    _fn("play_music", "Play music (YouTube; Spotify only if installed and allowed). For background music use query 'background music'.",
        play_music, query="song, artist or genre", incognito="yes to play in a private/incognito window (optional)"),
    _fn("play_video", "Play a song or video on YouTube right away (opens the video itself, it starts playing). "
        "incognito='yes' plays it in a private/incognito window.", play_video, query="what to play", incognito="yes or no"),
    _fn("open_incognito", "Open a private (incognito) browser window, optionally at a web address.", open_incognito,
        url="web address (optional)"),
    _fn("media_control", "Control music/video ALREADY open in any app or browser tab (YouTube in Chrome, Spotify...): play/resume, "
        "pause, next, previous, stop, or status (what is open/playing). Use this - not play_video - to resume what is already open.",
        media_control, action="play, pause, next, previous, stop or status"),
    _fn("read_screen", "READ the text that is visible on screen (offline OCR; read-only, never clicks). For 'what's on my screen', "
        "'read this', 'what does it say'. window = part of a window title; empty = the window in front; 'screen' = everything.",
        read_screen, window="window title part, or empty (optional)"),
    _fn("set_volume", "Speaker volume of the PC: up, down, mute, unmute, what it is now, or an exact level like 60.", set_volume,
        direction="up, down, mute, unmute, set or what", level="exact percent 0-100 (optional)"),
    _fn("jarvis_settings", "Change YOURSELF (Jarvis): theme/colour (glass, emerald=green, crimson=red), speaking speed "
        "(faster, slower, normal), which voice you speak with (Lessac US, Alan British male, Northern British male, Ryan US male), "
        "spoken replies on/off, wake word on/off, follow-up listening on/off.", jarvis_settings,
        setting="theme, speech speed, voice, wake word or follow-up", value="e.g. crimson, faster, slower, on, off"),
    _fn("open_settings", "Open a Windows settings page (wifi, bluetooth, display, sound, storage, battery, update...).",
        open_settings, page="settings page"),
    _fn("screenshot", "Take a screenshot of the whole screen and save it.", screenshot),
    _fn("lock_pc", "Lock the computer.", lock_pc),
    _fn("set_clipboard", "Copy text to the clipboard.", set_clipboard, text="text to copy"),
    _fn("get_clipboard", "Read what is on the clipboard.", get_clipboard),
    _fn("chrome_profiles", "List Google Chrome profiles; can also open Chrome with one of them.", chrome_profiles,
        open_profile="profile name to open (optional)"),
    _fn("desktop_look", "See the CONTROLS of an app window: its title and the names of its buttons, fields, menus and tabs "
        "(not the text content - use read_screen to read). Use before desktop_do.", desktop_look, window="app name or window title (empty = the active window)"),
    _fn("desktop_do", "Operate an app on screen like a user. Give short steps, one per line: 'click <button name>', "
        "'type <text> in <field name>', 'enter <text> in <field>' (types + Enter), 'press ctrl+t', 'select <option> in <list>', "
        "'check <box>', 'scroll down', 'open <app>', 'focus <app>', 'wait 2', 'read <element>'. A fast executor runs them and reports.",
        desktop_do, window="app name or window title", steps="the steps, one per line"),
    _fn("send_message", "Prepare a WhatsApp message to a contact (user approves and presses Send).", send_message,
        to="contact name", text="message text"),
    # organiser
    _fn("set_reminder", "Set a reminder at a time.", set_reminder, text="what to remind", time="when, e.g. '7 pm', 'tomorrow 9am', 'in 10 minutes'"),
    _fn("set_timer", "Start a countdown timer.", set_timer, duration="e.g. '10 minutes', '30 seconds'", label="name (optional)"),
    _fn("list_reminders", "Show upcoming reminders and timers.", list_reminders),
    _fn("cancel_reminder", "Cancel reminders/timers matching some words.", cancel_reminder, text="words from the reminder"),
    _fn("snooze_reminder", "Snooze the reminder that just went off ('snooze', 'remind me again in 10 minutes').", snooze_reminder,
        minutes="minutes to wait (optional, default 5)"),
    _fn("add_task", "Add items to a list (to-do, shopping, any name).", add_task, item="item(s), comma separated",
        list_name="list name, e.g. shopping (optional)"),
    _fn("list_tasks", "Show the items on a list ('all' for every list).", list_tasks, list_name="list name (optional)"),
    _fn("complete_task", "Tick off an item on a list.", complete_task, item="the item", list_name="list name (optional)"),
    _fn("take_note", "Save a note.", take_note, text="note text"),
    _fn("read_notes", "Read the notes the user asked you to save (NOT files on disk - use read_file for files).", read_notes),
    # memory & learning
    _fn("remember", "Save a lasting fact about the user or a preference.", remember, fact="the fact, as a short sentence"),
    _fn("recall_chats", "Search earlier conversations with the user (saved on this PC): 'what did we talk about yesterday', "
        "'what did I ask you about X'.", recall_chats, query="what to look for (optional)",
        when="today / yesterday / 3 days ago / last week / monday (optional)"),
    _fn("forget", "Delete remembered facts about a topic.", forget, fact="what to forget"),
    _fn("save_skill", "Learn a skill: save SHORT steps for a task to repeat later. Use only the tools needed to get the "
        "data (e.g. 1. system_info 2. report battery, RAM, C free). Never add run_python to read another tool's output.", save_skill,
        name="short skill name", when_to_use="when this skill applies", steps="numbered steps naming the tools to use"),
    _fn("use_skill", "Load the steps of a learned skill.", use_skill, name="skill name"),
    _fn("ask_big_ai", "Hand a very hard task to a bigger AI (not set up yet).", ask_big_ai, question="the full task"),
]}

if MMPROJ.exists():          # the brain's eyes (Gemma 4's own image encoder, ~0.6 GB): look_at_screen / describe_image
    TOOLS.update({t["name"]: t for t in [
        _fn("look_at_screen", "SEE the screen with your own eyes (local, private): what something looks like, pictures, colours, "
            "layout, 'what do you see'. For reading/quoting TEXT use read_screen (faster, exact).", look_at_screen,
            question="what to look for (optional)", window="'front' (default) or 'screen' for everything (optional)"),
        _fn("describe_image", "Look at a picture file on this PC (photo, screenshot, diagram) and describe it or answer about it.",
            describe_image, path="full path of the image file", question="what to look for (optional)"),
    ]})


# ======================================================== plugins: your own tools (data/plugins/*.py)
PLUGINS = DATA / "plugins"
PLUGIN_ERRORS = []


def tool(description, risk="confirm", **params):
    """Mark a function in a plugin file as a Jarvis tool. params: name="what it is" (all strings; add '(optional)').
    risk: "confirm" (default - an Allow/Deny popup every time) or "safe" (runs straight away: only for look-only tools)."""
    def wrap(fn):
        fn._jarvis = {"description": description, "risk": "safe" if risk == "safe" else "confirm", "params": params}
        return fn
    return wrap


def _load_plugins():
    """Loads ONLY the .py files YOU put in data/plugins (Jarvis never writes there). A broken file is skipped, never fatal."""
    import importlib.util
    PLUGINS.mkdir(parents=True, exist_ok=True)
    loaded = []
    for f in sorted(PLUGINS.glob("*.py")):
        try:
            spec = importlib.util.spec_from_file_location(f"jarvis_plugin_{f.stem}", f)
            mod = importlib.util.module_from_spec(spec)
            mod.tool = tool                                 # so a plugin can use @tool without importing anything
            spec.loader.exec_module(mod)
        except Exception as e:
            PLUGIN_ERRORS.append(f"{f.name}: {e}")
            continue
        for name, fn in vars(mod).items():
            meta = getattr(fn, "_jarvis", None)
            if callable(fn) and meta and name not in TOOLS:
                TOOLS[name] = dict(_fn(name, meta["description"], fn, **meta["params"]), risk=meta["risk"], plugin=f.name)
                loaded.append(name)
    return loaded


PLUGIN_TOOLS = _load_plugins()
