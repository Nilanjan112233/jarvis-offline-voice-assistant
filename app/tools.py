"""Tools Jarvis can use. Each tool = description for the model + Python function.

Most tools work in the background and return text for the model to read.
On-screen tools (open_app, open_url, play_music, media keys, screenshot, lock) act on the PC directly.
"""
import ctypes, html, json, os, platform, re, ssl, subprocess, sys, time, urllib.parse, urllib.request, webbrowser
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
def web_search(query):
    page = _get("https://html.duckduckgo.com/html/", urllib.parse.urlencode({"q": query}).encode())
    links = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.S)
    snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)
    out, urls = [], []
    for (u, t), s in list(zip(links, snips))[:6]:
        u = urllib.parse.unquote(re.sub(r"^.*?uddg=", "", u).split("&rut=")[0])
        urls.append(u)
        out.append(f"- {_text(t)}\n  {u}\n  {_text(s)}")
    if not out:
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
    try:
        build = int(platform.version().split(".")[-1])
        return f"Windows {'11' if build >= 22000 else '10'} (build {build})"
    except Exception:
        return platform.platform()


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


def run_python(code):
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=180,
                       creationflags=subprocess.CREATE_NO_WINDOW, cwd=str(Path.home()))
    out = r.stdout.strip()
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


def play_music(query):
    no_spotify = any("spotify" in f.lower() and re.search(r"(don'?t|do not|never|\bno\b|avoid|stop)", f.lower())
                     for f in load_json(MEMORY, []))
    if not no_spotify:
        try:
            os.startfile("spotify:search:" + urllib.parse.quote(query)); return f"Searching Spotify for {query}."
        except OSError:
            pass
    webbrowser.open("https://music.youtube.com/search?q=" + urllib.parse.quote_plus(query))
    return f"Opened YouTube Music for {query}."


def _key(vk, times=1):
    for _ in range(times):
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0); ctypes.windll.user32.keybd_event(vk, 0, 2, 0)


def set_volume(direction):
    d = direction.lower()
    if "mute" in d or d in ("off", "0"):
        _key(0xAD); return "Toggled mute."
    if any(w in d for w in ("down", "lower", "decrease", "quiet", "less")):
        _key(0xAE, 5); return "Volume down."
    _key(0xAF, 5); return "Volume up."


def media_control(action):
    a = action.lower()
    vk, msg = ((0xB0, "Skipped to the next track.") if "next" in a or "skip" in a else
               (0xB1, "Went back to the previous track.") if "prev" in a or "back" in a else
               (0xB2, "Stopped playback.") if "stop" in a else (0xB3, "Toggled play/pause."))
    _key(vk)
    return msg


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


# ======================================================== memory, skills
def remember(fact):
    facts = load_json(MEMORY, [])
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
    _fn("play_music", "Search and play music.", play_music, query="song, artist or genre"),
    _fn("media_control", "Control the music/video that is playing: pause, play, next, previous, stop.", media_control,
        action="pause, play, next, previous or stop"),
    _fn("set_volume", "Speaker volume: up, down or mute.", set_volume, direction="up, down or mute"),
    _fn("open_settings", "Open a Windows settings page (wifi, bluetooth, display, sound, storage, battery, update...).",
        open_settings, page="settings page"),
    _fn("screenshot", "Take a screenshot of the whole screen and save it.", screenshot),
    _fn("lock_pc", "Lock the computer.", lock_pc),
    _fn("set_clipboard", "Copy text to the clipboard.", set_clipboard, text="text to copy"),
    _fn("get_clipboard", "Read what is on the clipboard.", get_clipboard),
    _fn("chrome_profiles", "List Google Chrome profiles; can also open Chrome with one of them.", chrome_profiles,
        open_profile="profile name to open (optional)"),
    _fn("desktop_look", "See what is on screen in an app window: its title and the names of its buttons, fields, menus and tabs. "
        "Use before desktop_do.", desktop_look, window="app name or window title (empty = the active window)"),
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
    _fn("add_task", "Add items to a list (to-do, shopping, any name).", add_task, item="item(s), comma separated",
        list_name="list name, e.g. shopping (optional)"),
    _fn("list_tasks", "Show the items on a list ('all' for every list).", list_tasks, list_name="list name (optional)"),
    _fn("complete_task", "Tick off an item on a list.", complete_task, item="the item", list_name="list name (optional)"),
    _fn("take_note", "Save a note.", take_note, text="note text"),
    _fn("read_notes", "Read the notes the user asked you to save (NOT files on disk - use read_file for files).", read_notes),
    # memory & learning
    _fn("remember", "Save a lasting fact about the user or a preference.", remember, fact="the fact, as a short sentence"),
    _fn("forget", "Delete remembered facts about a topic.", forget, fact="what to forget"),
    _fn("save_skill", "Learn a skill: save SHORT steps for a task to repeat later. Use only the tools needed to get the "
        "data (e.g. 1. system_info 2. report battery, RAM, C free). Never add run_python to read another tool's output.", save_skill,
        name="short skill name", when_to_use="when this skill applies", steps="numbered steps naming the tools to use"),
    _fn("use_skill", "Load the steps of a learned skill.", use_skill, name="skill name"),
    _fn("ask_big_ai", "Hand a very hard task to a bigger AI (not set up yet).", ask_big_ai, question="the full task"),
]}
