"""Safety guardrails for actions. Enforced in CODE, not by the model.

  SAFE    - runs straight away: anything that only LOOKS (read files, list folders, read-only commands,
            searches, PC info) plus small harmless actions (open app, music, notes, reminders)
  CONFIRM - popup first: anything that CHANGES things (write/delete files, changing commands,
            downloads, messages, code that touches files/internet)
  BLOCK   - never: destructive or security-changing commands, Windows system folders
Text the model read from websites or files can't change these rules.
"""
import os, re
from pathlib import Path

SAFE, CONFIRM, BLOCK = "safe", "confirm", "block"

TOOL_RISK = {
    "web_search": SAFE, "get_news": SAFE, "get_weather": SAFE, "convert_currency": SAFE, "read_webpage": SAFE, "system_info": SAFE,
    "list_files": SAFE, "read_file": SAFE, "find_files": SAFE, "folder_sizes": SAFE,
    "use_skill": SAFE, "ask_big_ai": SAFE, "run_command": SAFE, "run_python": SAFE,
    "open_app": SAFE, "open_url": SAFE, "play_music": SAFE, "set_volume": SAFE, "open_settings": SAFE,
    "take_note": SAFE, "set_reminder": SAFE, "remember": SAFE, "forget": SAFE, "save_skill": SAFE,
    # additions: look-only or small & easy to undo
    "world_time": SAFE, "network_info": SAFE, "installed_apps": SAFE, "largest_files": SAFE, "read_notes": SAFE,
    "list_reminders": SAFE, "cancel_reminder": SAFE, "set_timer": SAFE, "add_task": SAFE, "list_tasks": SAFE,
    "complete_task": SAFE, "media_control": SAFE, "screenshot": SAFE, "lock_pc": SAFE,
    "set_clipboard": SAFE, "get_clipboard": SAFE,
    "chrome_profiles": SAFE, "desktop_look": SAFE,
    "desktop_do": SAFE,   # the executor itself asks before clicking send/delete/buy/confirm-type buttons
    # changes things -> ask
    "write_file": CONFIRM, "delete_file": CONFIRM, "move_file": CONFIRM, "close_app": CONFIRM,
    "download_file": CONFIRM, "send_message": CONFIRM,
}

HOME = Path.home().resolve()
PROTECTED = [Path(os.environ.get("SystemRoot", r"C:\Windows")), Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)"),
             Path(r"C:\ProgramData"), HOME / "AppData"]

DANGEROUS_COMMAND = re.compile(
    r"(\bformat(\.com)?\s+[a-z]:|\bformat-volume\b|\bdiskpart\b|\bbcdedit\b|\bcipher\s+/w|\bdel\s+/[sq]|\brd\s+/s|\brmdir\s+/s|"
    r"\brm\s+-[rf]{1,2}\b|remove-item\b.*-recurse|\breg\s+(delete|add)\b|\bvssadmin\b|\bwbadmin\b|\bclear-disk\b|"
    r"\bshutdown\b|\brestart-computer\b|\bstop-computer\b|\bnet\s+(user|localgroup)\b|set-mppreference|"
    r"add-mppreference|\bnetsh\b.*firewall|set-netfirewallprofile|set-executionpolicy|"
    r"invoke-webrequest|\biwr\b|invoke-restmethod|\birm\b|\bcurl\b|\bwget\b|\bbitsadmin\b|\bcertutil\b|"
    r"\binvoke-expression\b|\biex\b|start-bitstransfer|\bschtasks\b|new-service|\bsc\s+(create|config|delete))",
    re.I)

# ---- read-only command detection
READ_ONLY_VERBS = {"get", "select", "sort", "measure", "where", "foreach", "format", "group", "test", "resolve",
                   "compare", "convertto", "convertfrom", "write", "out", "split", "join", "find"}
READ_ONLY_WORDS = {"ipconfig", "systeminfo", "whoami", "hostname", "tasklist", "netstat", "nslookup", "ping", "where",
                   "dir", "ls", "gci", "cat", "type", "gc", "echo", "select", "sort", "measure", "ft", "fl", "%", "?",
                   "findstr", "tree", "ver", "date", "time", "driverquery", "getmac", "arp", "route", "foreach", "if", "else"}
WRITE_SIGNS = re.compile(r"(>|\bout-file\b|\bset-content\b|\badd-content\b|::\s*(delete|move|copy|write|create|append)|"
                         r"\bstart-process\b|\b(route|arp)\s+(add|delete|-d)\b|\bwmic\b)", re.I)


def is_read_only_command(cmd):
    """True if every command in the line is a known look-only one (Get-*, Select, ipconfig, ...)."""
    if WRITE_SIGNS.search(cmd):
        return False
    if re.fullmatch(r"\s*[\w.-]+(\.exe)?\s+(--version|-v|-V|version|/\?|--help|-h)\s*", cmd):
        return True   # e.g. "python --version", "git --version"
    for seg in re.split(r"[|;\n{}()]|&&|\|\|", cmd):
        words = seg.strip().split()
        if not words:
            continue
        first = words[0].lower()
        if first.startswith(("$", "@", "[", "'", '"', "-", ",", "=")) or first[0].isdigit():
            continue  # variables, values, parameters
        if first in READ_ONLY_WORDS:
            continue
        if "-" in first and first.split("-")[0] in READ_ONLY_VERBS and first != "out-file":
            continue
        return False
    return True


DANGEROUS_PYTHON = re.compile(
    r"(\bos\.(remove|unlink|rmdir|removedirs|rename|renames|replace|system|startfile|chmod|kill)\b|"
    r"\bshutil\.(rmtree|move|copy\w*)\b|\bsubprocess\b|\bopen\([^)]*['\"]\s*[wax+]|\.write_(text|bytes)\(|"
    r"\.(unlink|rmdir|rename|replace|touch|mkdir)\(|\bos\.(mkdir|makedirs)\b|\burllib\b|\brequests\b|\bsocket\b|"
    r"\bhttp\.|\bctypes\b|\bwinreg\b|\beval\(|\bexec\(|__import__|\bimportlib\b)")


def _inside(p, roots):
    return any(p == r or r in p.parents for r in roots)


def _path(args, key="path"):
    try:
        return Path(os.path.expandvars(os.path.expanduser(str(args.get(key, ""))))).resolve()
    except Exception:
        return None


def check(tool, args, user_text):
    """Return (level, reason). user_text = what YOU typed, not what the model or a website wrote."""
    if tool not in TOOL_RISK:
        return BLOCK, f"unknown action '{tool}'"
    args = args or {}

    if tool == "run_command":
        cmd = str(args.get("command", ""))
        if DANGEROUS_COMMAND.search(cmd):
            return BLOCK, "this command could destroy data, change security/system settings, or download things"
        if is_read_only_command(cmd):
            return SAFE, ""
        return CONFIRM, "this command changes something on your PC"

    if tool == "run_python":
        if DANGEROUS_PYTHON.search(str(args.get("code", ""))):
            return CONFIRM, "this code can change or delete files, start programs, or use the internet"
        return SAFE, ""

    if tool in ("write_file", "delete_file"):
        p = _path(args)
        if p is None or _inside(p, PROTECTED) or p == HOME or (tool == "delete_file" and len(p.parts) <= 2):
            return BLOCK, "that location is part of Windows/apps or too broad"
        return CONFIRM, ("deletes (to the Recycle Bin): " if tool == "delete_file" else "writes the file: ") + str(p)

    if tool == "download_file":
        return CONFIRM, "downloads a file from the internet"
    if tool == "move_file":
        for key in ("source", "destination"):
            p = _path(args, key)
            if p is None or _inside(p, PROTECTED):
                return BLOCK, "that location is part of Windows/apps"
        return CONFIRM, f"moves {args.get('source')} to {args.get('destination')}"
    if tool == "close_app":
        return CONFIRM, f"closes {args.get('name')} (unsaved work could be lost)"
    if tool == "send_message":
        return CONFIRM, "sends a message to another person"
    return TOOL_RISK[tool], ""
