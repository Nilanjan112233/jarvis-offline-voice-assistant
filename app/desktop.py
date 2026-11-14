"""Desktop control: Jarvis operates apps like a user - fast, no vision model.

Hands:  agent-desktop (Vercel Labs, v1.2.0) reads apps through the Windows accessibility tree and acts on
        elements by reference (click, type, select, toggle, scroll, keys). ~0.3-0.8 s per action.
System-1 executor (this file): the LLM writes a short plan in plain steps ("click File", "type hello in Text editor",
        "press ctrl+s"). The executor turns each step into an action WITHOUT the LLM: it reads the window, matches the
        step's target to the best element by name (fuzzy text matching), acts, re-reads, and moves on.
        The LLM only plans and checks the final report -> fast.
Safety: risky buttons (send, delete, buy, pay, submit, install, sign out...) need the user's OK first;
        password fields are never typed into.
"""
import difflib, json, re, subprocess, time
from pathlib import Path

AD = Path(__file__).resolve().parents[1] / "bin" / "agent-desktop.exe"
RISKY = re.compile(r"\b(send|delete|remove|erase|buy|pay|purchase|checkout|order|submit|post|publish|share|install|uninstall|"
                   r"format|sign out|log ?out|transfer|confirm|accept|agree|allow|empty recycle|reset|wipe|unsubscribe)\b", re.I)


def ad(*args, headed=False, timeout=40):
    cmd = [str(AD)] + (["--headed"] if headed else []) + [str(a) for a in args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8",
                           creationflags=subprocess.CREATE_NO_WINDOW)
        return json.loads(r.stdout or "{}") if r.stdout.strip().startswith("{") else {"ok": False, "error": {"message": r.stderr[:300]}}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": {"code": "TIMEOUT", "message": "agent-desktop timed out"}}


def list_windows(app=""):
    d = ad("list-windows", *(["--app", app] if app else []))
    return d.get("data", []) if d.get("ok") else []


def pick_window(target=""):
    """Find a window by app name or title words; prefer the focused one."""
    wins = [w for w in list_windows() if w.get("visible") and not w.get("minimized")] or list_windows()
    if not target:
        return next((w for w in wins if w.get("is_focused")), wins[0] if wins else None)
    t = target.lower().removesuffix(".exe")
    scored = []
    for w in wins:
        hay = f"{w.get('app_name', '')} {w.get('title', '')}".lower()
        app = w.get("app_name", "").lower().removesuffix(".exe")
        s = 2 if t in app else 1 if t in hay else max(
            difflib.SequenceMatcher(None, t, app).ratio() * 2 if difflib.SequenceMatcher(None, t, app).ratio() >= 0.8 else 0,
            difflib.SequenceMatcher(None, t, hay).ratio())
        scored.append((s + (0.1 if w.get("is_focused") else 0), w))
    scored.sort(key=lambda x: -x[0])
    if not scored or scored[0][0] < 0.5:
        return None
    top = [w for s, w in scored if s >= scored[0][0] - 0.05]
    if len(top) > 1 and not any(w.get("is_focused") for w in top):
        recent = [w for w in top if w.get("id") in LAUNCHED]
        if recent:
            return recent[-1]
        raise LookupError("Several windows match '" + target + "': " + "; ".join(f"'{w.get('title')}'" for w in top[:5])
                          + ". Use the exact window title.")
    return scored[0][1]


LAUNCHED = []   # windows Jarvis opened itself - preferred when several windows of one app are open


def elements(window_id, timeout_ms=4000):
    """Flatten the window's interactive accessibility tree into a list of {ref, role, name, actions, context}."""
    d = ad("snapshot", "--window-id", window_id, "-i", "--compact", "--timeout-ms", timeout_ms)
    out = []

    def walk(n, ctx):
        if not isinstance(n, dict):
            return
        name = (n.get("name") or "").strip()
        if n.get("ref_id"):
            out.append({"ref": n["ref_id"], "role": n.get("role", ""), "name": name, "actions": n.get("available_actions", []),
                        "context": ctx, "states": n.get("states", [])})
        for c in n.get("children", []) or []:
            walk(c, (name or ctx)[:40])

    if d.get("ok"):
        walk(d["data"].get("tree"), "")
    return out


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", s.lower().replace("'", "")).split()


def best_match(target, els, role_hint=None):
    """Score elements by how well their name matches the target words (System-1 matching, no LLM)."""
    tw = _norm(target)
    if not tw:
        return None, 0
    best, best_s = None, 0.0
    for e in els:
        nw = _norm(e["name"])
        if not nw:
            continue
        name = " ".join(nw)
        t = " ".join(tw)
        s = 1.0 if name == t else 0.9 if name.startswith(t) or t in name.split(" . ") else 0.0
        if not s:
            overlap = len(set(tw) & set(nw)) / len(set(tw))
            s = max(overlap * 0.85, difflib.SequenceMatcher(None, t, name).ratio() * 0.8)
        if role_hint and role_hint in e["role"]:
            s += 0.1
        if s > best_s:
            best, best_s = e, s
    return best, best_s


STEP = re.compile(r"^\s*(?P<verb>double[- ]click|right[- ]click|click|press|tap|type|enter|write|select|choose|check|uncheck|toggle|"
                  r"scroll|focus|switch to|open|launch|start|wait for|wait|read|close window|maximi[sz]e|minimi[sz]e)\b\s*(?P<rest>.*)$", re.I)


def run_steps(target_window, steps, confirm=None, user_text=""):
    """Execute plain-language steps fast. Returns a report for the LLM to check."""
    try:
        win = pick_window(target_window) if target_window else pick_window()
    except LookupError as e:
        return str(e)
    log = []
    if not win and not any(re.match(r"\s*(open|launch|start)\b", s, re.I) for s in steps):
        return f"No window matching '{target_window}' is open. Open windows: " + \
               ", ".join(f"{w.get('app_name')}: {w.get('title', '')[:40]}" for w in list_windows()[:10])
    t0 = time.time()
    closed = False
    for raw in steps:
        step = raw.strip().strip("-•*0123456789.) ").strip()
        if closed:   # our window is gone: optional leftovers are fine, anything else is not run on another window
            log.append(f"OK: window closed, skipped '{step}'" if re.search(r"if (present|there|it appears|shown|asked)$", step, re.I)
                       else f"STOPPED: window closed before '{step}'")
            continue
        before_ids = {w["id"] for w in list_windows()}
        if not step:
            continue
        m = STEP.match(step)
        if not m:
            log.append(f"SKIPPED (not understood): {step}"); continue
        verb, rest = m.group("verb").lower(), m.group("rest").strip().strip('"')
        optional = bool(re.search(r"\s+if (present|there|it appears|shown|asked)$", rest, re.I))
        rest = re.sub(r"\s+if (present|there|it appears|shown|asked)$", "", rest, flags=re.I)
        try:
            if verb in ("open", "launch", "start"):
                import tools
                before = {w["id"] for w in list_windows()}
                msg = tools.open_app(rest)
                r = {"ok": not msg.startswith(("No app", "Couldn't"))}
                new = []
                for _ in range(20):   # wait up to ~6 s for the new window
                    time.sleep(0.3)
                    new = [w for w in list_windows() if w["id"] not in before]
                    if new:
                        break
                if new:
                    LAUNCHED.append(new[0]["id"]); win = new[0]
                else:
                    win = pick_window(rest) or win
                log.append(f"{'OK' if r.get('ok') else 'FAILED'}: open {rest}")
                continue
            if verb in ("focus", "switch to"):
                w = pick_window(rest)
                if w:
                    ad("focus-window", "--window-id", w["id"]); win = w
                log.append(f"{'OK' if w else 'FAILED (no such window)'}: focus {rest}"); continue
            if verb == "wait":
                secs = float(re.search(r"[\d.]+", rest).group()) if re.search(r"[\d.]+", rest) else 1.0
                time.sleep(min(secs, 10)); log.append(f"OK: waited {secs:g}s"); continue
            if verb in ("press", "tap") and re.fullmatch(r"[\w+\-]+", rest.replace(" ", "")) and not rest.lower().startswith(("the ", "on ")):
                r = ad("press", rest.replace(" ", "").lower(), "--window-id", win["id"], headed=True)
                log.append(f"{'OK' if r.get('ok') else 'FAILED'}: press {rest}"); time.sleep(0.3); continue
            if verb in ("maximise", "maximize", "minimise", "minimize", "close window"):
                cmd = {"maximise": "maximize", "maximize": "maximize", "minimise": "minimize", "minimize": "minimize"}.get(verb)
                if cmd:
                    r = ad(cmd, "--window-id", win["id"])
                    log.append(f"{'OK' if r.get('ok') else 'FAILED'}: {cmd}"); continue
                verb, rest = "click", "Close"

            els = elements(win["id"])
            if verb == "read":
                el, s = best_match(rest, els) if rest else (None, 0)
                if el:
                    v = ad("get", el["ref"], "--property", "value").get("data", {}).get("value") or el["name"]
                    log.append(f"READ '{el['name']}': {str(v)[:400]}")
                else:
                    log.append("SEEN: " + "; ".join(f"{e['role']} '{e['name']}'" for e in els if e["name"])[:900])
                continue

            text = None
            if verb in ("type", "enter", "write"):
                mm = re.match(r"""["']?(.+?)["']?\s+(?:in|into|on)\s+(?:the\s+)?(.+)$""", rest, re.I)
                text, target = (mm.group(1), mm.group(2)) if mm else (rest, "")
                cands = [e for e in els if e["role"] in ("textfield", "combobox", "searchfield", "document", "textarea")] or els
                el, s = best_match(target, cands, "textfield") if target else ((cands[0], 1.0) if cands else (None, 0))
                if el and re.search(r"password|passcode|pin\b", el["name"], re.I):
                    log.append(f"BLOCKED: won't type into a password field ('{el['name']}')"); break
            elif verb in ("select", "choose"):
                mm = re.match(r"""["']?(.+?)["']?\s+(?:in|from)\s+(?:the\s+)?(.+)$""", rest, re.I)
                text, target = (mm.group(1), mm.group(2)) if mm else (rest, rest)
                el, s = best_match(target, els, "combobox")
            else:
                target = re.sub(r"^(on |the )+", "", rest, flags=re.I)
                target = re.sub(r"\s+(button|link|tab|menu item|menu|checkbox|icon)$", "", target, flags=re.I)
                el, s = best_match(target, els)
            if (not el or s < 0.45) and verb in ("type", "enter", "write", "click") and not optional:
                time.sleep(0.8)   # window may still be loading -> look once more
                els = elements(win["id"])
                cands2 = [e for e in els if e["role"] in ("textfield", "combobox", "searchfield", "document", "textarea")] or els \
                    if verb in ("type", "enter", "write") else els
                el, s = best_match(target, cands2) if target else ((cands2[0], 1.0) if cands2 else (None, 0))
            if (not el or s < 0.45) and optional:
                log.append(f"OK: '{rest}' wasn't there (optional step skipped)"); continue
            if not el or s < 0.45:
                close = sorted(els, key=lambda e: -best_match(rest, [e])[1])[:6]
                log.append(f"FAILED: couldn't find '{rest}'. Closest: " + ", ".join(f"'{e['name']}'" for e in close if e["name"]))
                break
            label = el["name"] or el["role"]
            if verb not in ("type", "enter", "write", "scroll") and (RISKY.search(label) or RISKY.search(rest)):
                if not confirm or not confirm("desktop_click", {"window": win.get("title", ""), "click": label}, "a button that sends, deletes, buys or confirms something"):
                    log.append(f"NOT DONE: user declined clicking '{label}'"); break
            if verb in ("type", "enter", "write"):
                r = ad("set-value", el["ref"], text) if "SetValue" in el["actions"] else {"ok": False}
                if not r.get("ok"):
                    r = ad("type", el["ref"], text, headed=True)
                if not r.get("ok"):   # just-opened windows can refuse for a moment: wait, re-read, try once more
                    time.sleep(1.0)
                    fresh = [e for e in elements(win["id"]) if e["name"] == el["name"] and e["role"] == el["role"]]
                    if fresh:
                        el = fresh[0]
                        r = ad("set-value", el["ref"], text) if "SetValue" in el["actions"] else ad("type", el["ref"], text, headed=True)
                if r.get("ok") and verb == "enter":
                    ad("press", "return", "--window-id", win["id"], headed=True)
                desc = f"type '{text[:40]}' into '{label}'"
            elif verb in ("select", "choose"):
                r = ad("select", el["ref"], text); desc = f"select '{text}' in '{label}'"
            elif verb in ("check", "uncheck", "toggle"):
                r = ad(verb, el["ref"]); desc = f"{verb} '{label}'"
            elif verb == "scroll":
                d = "up" if "up" in rest.lower() else "down"
                r = ad("scroll", el["ref"], "--direction", d, "--amount", 5); desc = f"scroll {d}"
            else:
                kind = {"double-click": "double-click", "double click": "double-click", "right-click": "right-click",
                        "right click": "right-click"}.get(verb, "click")
                r = ad(kind, el["ref"], headed=kind != "click")
                if not r.get("ok") and kind == "click":
                    r = ad("click", el["ref"], headed=True)   # physical click like a user when there's no semantic press
                desc = f"{kind} '{label}'"
            log.append(f"{'OK' if r.get('ok') else 'FAILED'}: {desc}" + ("" if r.get("ok") else f" ({r.get('error', {}).get('code', '')})"))
            if not r.get("ok"):
                break
            time.sleep(0.35)   # let the UI update before the next step
            # stay locked on OUR window; only follow a dialog/window that this step opened; stop if ours closed
            now = list_windows()
            ids = {w["id"] for w in now}
            appeared = [w for w in now if w["id"] not in before_ids and w.get("pid") == win.get("pid")]
            if appeared:
                win = appeared[0]
            elif win["id"] not in ids:
                closed = True
        except Exception as e:
            log.append(f"FAILED: {step} ({e})"); break
    state = "the window was closed" if closed else f"window is now titled '{(win or {}).get('title', '?')}'"
    return f"Ran {len(log)} step(s) in {time.time() - t0:.1f}s:\n" + "\n".join(log) + f"\nAfterwards: {state}."


def look(target_window=""):
    """What's on screen in a window: title + its buttons/fields/menus (names only), for the LLM to plan."""
    try:
        win = pick_window(target_window)
    except LookupError as e:
        return str(e)
    if not win:
        return "Open windows: " + "; ".join(f"{w.get('app_name')}: {w.get('title', '')[:50]}" for w in list_windows()[:15])
    els = elements(win["id"])
    names = []
    for e in els:
        if e["name"] and len(names) < 70:
            names.append(f"{e['role']} '{e['name'][:50]}'")
    return f"Window: {win.get('title')} ({win.get('app_name')})\nElements: " + "; ".join(names)
