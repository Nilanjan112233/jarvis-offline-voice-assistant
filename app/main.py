"""Jarvis desktop app: floating orb + slide-out panel, voice, brain from agent.py.

Windows (pywebview / Edge WebView2):
  orb   - small, always on top, draggable, animated by state. Click = open/close panel, right-click = talk.
  panel - chat with live steps, result cards, in-panel Allow/Deny, quick actions, settings.
Global shortcut: Ctrl+Alt+J = talk from anywhere.
"""
import ctypes, json, os, re, subprocess, threading, time, winsound
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import psutil
import webview

import tools
from agent import Agent

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"
SETTINGS_FILE = tools.DATA / "settings.json"
LOG = tools.DATA / "actions.log"
STARTUP_LNK = Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs\Startup\Jarvis.lnk"


# ======================================================================== result cards
def build_cards(actions):
    cards = []
    for a in actions:
        if not a.get("ok"):
            continue
        tool, res = a["tool"], a["result"]
        if tool == "get_weather":
            m = re.search(r"^(.*?) now: (.*?), (-?\d+)°C \(feels (-?\d+)°C\), humidity (\d+)%, wind (\d+) km/h\. Today: (-?\d+) to (-?\d+)°C\. "
                          r"Tomorrow: (-?\d+) to (-?\d+)°C, (.*?), chance of rain up to (\d+)%", res)
            if m:
                g = m.groups()
                cards.append({"type": "weather", "place": g[0], "desc": g[1], "temp": g[2], "feels": g[3], "humidity": g[4], "wind": g[5],
                              "today": f"{g[6]}–{g[7]}°", "tomorrow": f"{g[8]}–{g[9]}°", "rain": g[11]})
        elif tool == "system_info" and not any(c["type"] == "system" for c in cards):
            b, vm = psutil.sensors_battery(), psutil.virtual_memory()
            disks = []
            for p in psutil.disk_partitions(all=False):
                try:
                    u = psutil.disk_usage(p.mountpoint)
                    disks.append({"name": p.device.rstrip("\\"), "free": round(u.free / 1e9, 1), "used_pct": round(u.percent)})
                except Exception:
                    pass
            cards.append({"type": "system", "battery": round(b.percent) if b else None, "charging": bool(b and b.power_plugged),
                          "ram_free": round(vm.available / 1e9, 1), "ram_total": round(vm.total / 1e9), "ram_used_pct": round(vm.percent),
                          "disks": disks[:2]})
        elif tool == "get_news":
            items = []
            for line in res.splitlines():
                m = re.match(r"- (.*?)(?: - ([^-()]+))? \((.*?)\)$", line.strip())
                if m:
                    items.append({"text": m.group(1), "side": (m.group(2) or "").strip()})
            if items:
                cards.append({"type": "list", "title": "Headlines", "items": items[:6]})
        elif tool in ("list_files", "find_files", "largest_files") and "does not exist" not in res[:200]:
            lines = res.splitlines()
            items = []
            for line in lines[1:]:
                line = line.strip()
                if not line or line.startswith(("Files:", "Examples:")):
                    continue
                m = re.match(r"^\s*([\d.]+ \w+)\s+(.+)$", line) if tool == "largest_files" else None
                if m:
                    items.append({"text": Path(m.group(2)).name, "side": m.group(1)})
                else:
                    m = re.match(r"^(.*?)\s+\((.*?)\)$", line)
                    name = (m.group(1) if m else line).replace("[folder] ", "📁 ")
                    items.append({"text": Path(name).name if ("\\" in name or "/" in name) else name, "side": m.group(2) if m else ""})
            if items:
                cards.append({"type": "list", "title": lines[0][:60].rstrip(":"), "items": items[:8]})
        elif tool == "convert_currency":
            m = re.match(r"(.*?) = (.*?) \((.*?)\)", res)
            if m:
                cards.append({"type": "big", "title": m.group(1), "value": m.group(2), "sub": m.group(3)})
        elif tool == "world_time":
            m = re.match(r"Time in (.*?): (.*?), (.*?) \(", res)
            if m:
                cards.append({"type": "big", "title": f"Time in {m.group(1)}", "value": m.group(2), "sub": m.group(3)})
        elif tool in ("list_reminders", "list_tasks", "read_notes"):
            rows = [r.strip("- ").strip() for r in res.splitlines()[1:] if r.strip()] or \
                   [x.strip() for x in res.split(":", 1)[-1].split(",") if x.strip() and "empty" not in x]
            if rows:
                cards.append({"type": "list", "title": res.splitlines()[0].split(":")[0][:40], "items": [{"text": r} for r in rows[:8]]})
        elif tool in ("set_reminder", "set_timer", "add_task", "take_note", "remember", "save_skill", "screenshot",
                      "set_clipboard", "cancel_reminder", "complete_task", "download_file", "write_file", "move_file", "delete_file"):
            icon = {"set_reminder": "⏰", "set_timer": "⏱️", "add_task": "📝", "take_note": "🗒️", "remember": "🧠", "save_skill": "✨",
                    "screenshot": "📸", "set_clipboard": "📋", "download_file": "⬇️"}.get(tool, "✓")
            cards.append({"type": "pill", "text": f"{icon} {res[:90]}"})
    return cards[:3]


def bind_children_to_app():
    """Put the model/router/speech servers in a Windows Job Object that is killed when Jarvis exits -
    so they can never keep running (and holding ~2.5 GB RAM) after the app closes or crashes."""
    k32 = ctypes.windll.kernel32
    job = k32.CreateJobObjectW(None, None)

    class LIMITS(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class EXT(ctypes.Structure):
        _fields_ = [("Basic", LIMITS), ("IoInfo", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    info = EXT()
    info.Basic.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
    for child in psutil.Process().children(recursive=True):
        h = k32.OpenProcess(0x0100 | 0x0001, False, child.pid)   # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        if h:
            k32.AssignProcessToJobObject(job, h)
            k32.CloseHandle(h)
    globals()["_JOB_HANDLE"] = job   # keep the handle open for the app's lifetime


# ======================================================================== the app
class Bridge:
    """Methods callable from JavaScript (window.pywebview.api.*). Each call runs on its own thread."""
    def __init__(self, app):
        self._app = app

    def ready(self): self._app.on_panel_ready()
    def send(self, text): self._app.submit(text)
    def listen(self): self._app.listen()
    def start_wake(self):
        if self.wake or not self.listener:
            return
        try:
            from voice import WakeWord
            busy_states = ("listening", "understanding", "thinking", "working", "waiting", "speaking", "starting")
            self.wake = WakeWord(self.on_wake, self.listener,
                                 paused=lambda: self.busy or self.state in busy_states or (self.speaker and self.speaker.busy()))
            self.wake.start()
        except Exception as e:
            self.wake = None
            self.emit(type="toast", text=f"Wake word unavailable: {e}")

    def stop_wake(self):
        if self.wake:
            self.wake.stop()
            self.wake = None

    def on_wake(self, buffered):
        """'Hey Jarvis' heard: keep recording (with the buffered audio) and check the transcript really says Jarvis."""
        if self.busy:
            return
        self.busy = True
        self.show_panel()
        self.set_state("listening", "Yes?")
        threading.Thread(target=self._wake_worker, args=(buffered,), daemon=True).start()

    def _wake_worker(self, buffered):
        from voice import WakeWord
        try:
            audio = self.listener.record(prefix=buffered, silence_after=0.9)
            self.set_state("understanding")
            text = self.listener.transcribe(audio) if audio is not None else ""
        except Exception as e:
            self.busy = False; self.set_state("idle"); self.emit(type="toast", text=f"Microphone problem: {e}"); return
        ok, command = WakeWord.split_command(text)
        self.busy = False
        if not ok:                       # false alarm ("Hey, Java...") -> quietly go back to waiting
            self.set_state("idle")
            return
        if command and len(command.split()) >= 2:
            self.submit(command, echo=True)
        else:                            # just "Hey Jarvis" -> answer and listen for the request
            self.set_state("idle")
            self.say("Yes?")
            time.sleep(0.9)
            self.listen()

    def hush(self): self._app.hush()
    def answer_confirm(self, cid, ok): self._app.answer_confirm(cid, ok)
    def toggle_panel(self): self._app.toggle_panel()
    def hide_panel(self): self._app.panel.hide()
    def get_settings(self): self._app.push_settings()
    def set_setting(self, key, value): self._app.set_setting(key, value)
    def forget_index(self, i): self._app.forget_index(i)
    def delete_skill(self, name): self._app.delete_skill(name)
    def open_data(self): os.startfile(str(tools.DATA))
    def quit(self): self._app.quit()


class JarvisApp:
    def __init__(self):
        self.settings = {"voice": True, "wake": True, "autostart": STARTUP_LNK.exists()}
        self.settings.update(tools.load_json(SETTINGS_FILE, {}))
        self.settings["autostart"] = STARTUP_LNK.exists()
        self.agent = Agent(tools.DATA / "engine.log")
        self.speaker = self.listener = self.wake = None
        self.busy, self.ready_flag, self.panel_shown = True, False, True
        self.confirms, self.state = {}, "starting"
        self.bridge = Bridge(self)
        sw, sh = webview.screens[0].width, webview.screens[0].height
        self.orb = webview.create_window("Jarvis orb", str(UI / "orb.html"), js_api=self.bridge, width=130, height=130,
                                         x=sw - 160, y=sh - 210, frameless=True, easy_drag=True, on_top=True, transparent=True,
                                         shadow=False, resizable=False, background_color="#000000")
        self.panel = webview.create_window("Jarvis", str(UI / "panel.html"), js_api=self.bridge, width=420, height=700,
                                           x=sw - 600, y=sh - 790, frameless=True, easy_drag=False, on_top=False,
                                           resizable=True, min_size=(360, 480), background_color="#07090f")

    # ---------------- events to the UI
    def emit(self, **e):
        try:
            self.panel.evaluate_js(f"window.onEvent && window.onEvent({json.dumps(e)})")
        except Exception:
            pass

    def set_state(self, state, detail=None, level=None):
        self.state = state
        self.emit(type="state", state=state, detail=detail)
        try:
            self.orb.evaluate_js(f"window.setOrb && setOrb({json.dumps(state)}, {level if level is not None else 'null'})")
        except Exception:
            pass

    def say(self, text):
        if self.speaker and self.settings.get("voice"):
            self.speaker.say(text)

    # ---------------- startup
    def boot(self):
        self.set_state("starting", "Loading the brain…")
        try:
            secs = self.agent.start()
        except Exception as e:
            self.emit(type="error", text=f"The model failed to start: {e}")
            return
        try:
            from voice import Speaker, Listener
            self.speaker = Speaker()
            self.listener = Listener(tools.DATA / "whisper.log")
            self.listener.start()
            if self.settings.get("wake"):
                self.start_wake()
        except Exception as e:
            self.emit(type="toast", text=f"Voice not available ({e}). You can still type.")
        bind_children_to_app()
        self.busy, self.ready_flag = False, True
        self.set_state("idle", f"Ready · started in {secs:.0f}s")
        name = next((re.sub(r"^User's name is ", "", f) for f in tools.load_json(tools.MEMORY, []) if f.startswith("User's name is")), "")
        h = datetime.now().hour
        self.say(("Good morning" if h < 12 else "Good afternoon" if h < 17 else "Good evening") + (f", {name}." if name else ".")
                 + " Jarvis is online.")
        threading.Thread(target=self.reminder_loop, daemon=True).start()
        threading.Thread(target=self.hotkey_loop, daemon=True).start()
        if os.environ.get("JARVIS_SELFTEST"):
            threading.Thread(target=self.selftest, daemon=True).start()

    def selftest(self):
        """Drives the real UI end to end and logs what the panel shows (set JARVIS_SELFTEST=1)."""
        out = open(tools.DATA / "selftest.log", "w", encoding="utf-8")
        js = lambda code: self.panel.evaluate_js(code)
        def wait_idle(timeout=120):
            t0 = time.time()
            while (self.busy or self.state in ("thinking", "working")) and time.time() - t0 < timeout:
                time.sleep(0.3)
        tests = ["I asked you about my system.", "how many profiles are in my Google Chrome",
                 "open a new notepad and type 'Jarvis UI test' in it", "now close that new notepad window without saving",
                 "what is 17 times 23"]
        for q in tests:
            t0 = time.time()
            js(f"document.getElementById('input').value = {json.dumps(q)}; document.getElementById('sendBtn').click()")   # like a user typing
            time.sleep(0.5); wait_idle()
            r = js("(() => { const b = [...document.querySelectorAll('.bubble')].pop(); const m = [...document.querySelectorAll('.msg-j')].pop();"
                   " return JSON.stringify({bubbles: document.querySelectorAll('.bubble').length, cards: m ? [...m.querySelectorAll('.card,.pill')].map(c => c.className + ':' + c.innerText.slice(0,60).replace(/\\n/g,' ')) : [],"
                   " steps: m ? m.querySelectorAll('.step').length : 0, text: b ? b.innerText.slice(0,160) : null, status: document.getElementById('status').innerText}) })()")
            out.write(f"{time.time()-t0:5.1f}s  {q}\n      {r}\n"); out.flush()
        # permission card -> click Deny in the panel (tests JS -> Python bridge)
        sandbox = tools.DATA / "selftest_sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)
        (sandbox / "todo.md").write_text("self-test file - Jarvis must NOT delete this
", encoding="utf-8")
        target = (sandbox / "todo.md").as_posix()
        js(f"document.getElementById('input').value = 'delete {target}'; document.getElementById('sendBtn').click()")
        for _ in range(200):
            if js("document.querySelectorAll('.ask:not(.done)').length") :
                break
            time.sleep(0.3)
        asked = js("[...document.querySelectorAll('.ask:not(.done) .t')].map(e => e.innerText).join('|')")
        js("document.querySelector('.ask:not(.done) .btn.no').click()")
        time.sleep(0.5); wait_idle()
        last = js("[...document.querySelectorAll('.bubble')].pop().innerText")
        out.write(f"PERMISSION: card={asked!r} -> clicked Deny -> answer={last!r} -> file still exists={Path(target).exists()}\n")
        # settings sheet + memory list
        js("document.getElementById('settingsBtn').click()"); time.sleep(1)
        out.write("SETTINGS: " + str(js("JSON.stringify({open: document.getElementById('sheet').classList.contains('open'), "
                                         "memory: document.querySelectorAll('#memList .item').length, skills: document.querySelectorAll('#skillList .item').length})")) + "\n")
        out.write("DONE\n"); out.close()

    def on_panel_ready(self):
        self.push_settings()
        if self.ready_flag:
            self.set_state("idle")

    # ---------------- requests
    def submit(self, text, echo=False):
        text = (text or "").strip()
        if not text or self.busy or not self.ready_flag:
            return
        self.busy = True
        if echo:
            self.emit(type="heard", text=text)
        self.hush()
        threading.Thread(target=self._work, args=(text,), daemon=True).start()

    def _work(self, text):
        t0 = time.time()
        self.set_state("thinking")
        try:
            answer, actions = self.agent.run(text, self._confirm, self._narrate)
        except Exception as e:
            self.emit(type="error", text=str(e)); self.busy = False; self.set_state("idle"); return
        secs = time.time() - t0
        self.emit(type="answer", text=answer, cards=build_cards(actions), secs=secs, tools=[a["tool"] for a in actions])
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "user": text, "answer": answer,
                                "actions": [{k: v for k, v in a.items() if k != "result"} for a in actions]}, ensure_ascii=False) + "\n")
        self.busy = False
        if self.speaker and self.settings.get("voice"):
            self.set_state("speaking")
            self.say(answer)
            threading.Thread(target=self._idle_after_speech, daemon=True).start()
        else:
            self.set_state("idle")

    def _idle_after_speech(self):
        time.sleep(0.3)
        while self.speaker and self.speaker.busy() and self.state == "speaking":
            time.sleep(0.15)
        if self.state == "speaking":
            self.set_state("idle")

    def _narrate(self, say, line):
        self.set_state("working", say or line)
        self.emit(type="step", line=say or line)
        if say:
            self.say(say)

    def _confirm(self, tool, args, reason):
        cid = int(time.time() * 1000)
        ev, box = threading.Event(), {}
        self.confirms[cid] = (ev, box)
        self.set_state("waiting")
        self.show_panel()
        self.say("I need your OK for this one.")
        self.emit(type="confirm", id=cid, tool=tool, args=args, reason=reason)
        ev.wait()
        self.set_state("working")
        return box.get("ok", False)

    def answer_confirm(self, cid, ok):
        ev, box = self.confirms.pop(int(cid), (None, None))
        if ev:
            box["ok"] = bool(ok); ev.set()

    # ---------------- voice
    def listen(self):
        if self.busy or not self.listener:
            if not self.listener:
                self.emit(type="toast", text="Voice isn't available.")
            return
        self.busy = True
        self.hush()
        self.show_panel()
        threading.Thread(target=self._listen_worker, daemon=True).start()

    def _listen_worker(self):
        self.set_state("listening")
        last = [0.0]

        def level(v):
            if time.time() - last[0] > 0.08:
                last[0] = time.time()
                try:
                    self.orb.evaluate_js(f"setOrb('listening', {min(v / 3000, 1):.2f})")
                except Exception:
                    pass
        try:
            audio = self.listener.record(on_level=level)
            if audio is None:
                self.busy = False; self.set_state("idle", "Didn't catch anything"); return
            self.set_state("understanding")
            text = self.listener.transcribe(audio)
        except Exception as e:
            self.busy = False; self.set_state("idle"); self.emit(type="toast", text=f"Microphone problem: {e}"); return
        self.busy = False
        if text:
            self.submit(text, echo=True)
        else:
            self.set_state("idle", "Didn't catch anything")

    def hush(self):
        if self.speaker:
            self.speaker.stop()

    # ---------------- windows
    def toggle_panel(self):
        self.hide_panel() if self.panel_shown else self.show_panel()

    def show_panel(self):
        self.panel_shown = True
        try:
            self.panel.show(); self.panel.restore()
        except Exception:
            pass

    def hide_panel(self):
        self.panel_shown = False
        self.panel.hide()

    # ---------------- settings
    def push_settings(self):
        self.emit(type="settings", settings=self.settings, memory=tools.load_json(tools.MEMORY, []), skills=tools.list_skills())

    def set_setting(self, key, value):
        if key == "wake":
            self.start_wake() if value else self.stop_wake()
            value = bool(self.wake) if value else False
        if key == "autostart":
            if value:
                bat = ROOT / "run_jarvis.bat"
                ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{STARTUP_LNK}');$s.TargetPath='{bat}';"
                      f"$s.WorkingDirectory='{ROOT}';$s.WindowStyle=7;$s.Save()")
                subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=subprocess.CREATE_NO_WINDOW)
            elif STARTUP_LNK.exists():
                STARTUP_LNK.unlink()
            value = STARTUP_LNK.exists()
        if key == "voice" and not value:
            self.hush()
        self.settings[key] = value
        tools.save_json(SETTINGS_FILE, {k: v for k, v in self.settings.items() if k != "autostart"})
        self.push_settings()

    def forget_index(self, i):
        facts = tools.load_json(tools.MEMORY, [])
        if 0 <= int(i) < len(facts):
            facts.pop(int(i))
            tools.save_json(tools.MEMORY, facts)
        self.push_settings()

    def delete_skill(self, name):
        p = tools._skill_path(name)
        if p.exists():
            p.unlink()
        self.push_settings()

    # ---------------- background loops
    def reminder_loop(self):
        while True:
            items = tools.load_json(tools.REMINDERS, [])
            changed = False
            for r in items:
                if not r.get("done") and datetime.fromisoformat(r["when"]) <= datetime.now():
                    r["done"] = changed = True
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                    self.show_panel()
                    self.emit(type="reminder", text=r["text"])
                    self.say("Reminder. " + r["text"])
            if changed:
                tools.save_json(tools.REMINDERS, items)
            time.sleep(1)

    def hotkey_loop(self):
        """Global Ctrl+Alt+J -> talk."""
        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, 1, 0x0002 | 0x0001 | 0x4000, ord("J")):   # MOD_CONTROL|MOD_ALT|MOD_NOREPEAT
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == 0x0312:   # WM_HOTKEY
                self.listen()

    def quit(self):
        try:
            self.stop_wake()
            self.agent.stop()
            if self.listener:
                self.listener.stop()
        finally:
            for w in (self.panel, self.orb):
                try:
                    w.destroy()
                except Exception:
                    pass
            os._exit(0)


if __name__ == "__main__":
    app = JarvisApp()
    webview.start(app.boot, private_mode=False, storage_path=str(tools.DATA / "webview"))
    app.agent.stop()
    if app.listener:
        app.listener.stop()
