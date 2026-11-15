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
VOICE_FILTER = tools.DATA / "voice" / "my_voice_filter.joblib"
VOICE_ID = tools.DATA / "voice" / "my_voice_id.npy"
TRAIN_SENTENCES = ["What's the weather like today?", "Open Notepad and type a quick note.",
                   "Hey, can you check my emails?", "Play some music on YouTube."]
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
    def hush(self): self._app.hush()
    def train_voice(self): self._app.train_voice()
    def forget_voice(self): self._app.forget_voice()
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
        self.settings = {"voice": True, "wake": True, "followup": True, "theme": "glass", "autostart": STARTUP_LNK.exists()}
        self.settings.update(tools.load_json(SETTINGS_FILE, {}))
        self.settings["autostart"] = STARTUP_LNK.exists()
        self.agent = Agent(tools.DATA / "engine.log")
        self.speaker = self.listener = self.ears = None
        self.last_via = None             # how the last request arrived: typed / button / wake / followup
        self.queue, self.qlock = [], threading.Lock()   # requests made while Jarvis is busy
        self.said_log = []               # (time, sentence) Jarvis spoke - to remove its own echo from what the mic hears
        self.last_answer = ""
        self.no_followup = False         # set by "stop": don't keep listening afterwards
        self.training = False
        self.busy, self.ready_flag, self.panel_shown = True, False, True
        self.confirms, self.state = {}, "starting"
        self.bridge = Bridge(self)
        sw, sh = webview.screens[0].width, webview.screens[0].height
        # floating orb: native layered window (real transparency; a WebView2 window can't be see-through on Windows)
        from orb_native import NativeOrb
        pos = self.settings.get("orb_pos") or (sw - 160, sh - 210)
        self.orb = NativeOrb(pos[0], pos[1], theme=self.settings.get("theme", "glass"),
                             on_click=self.toggle_panel, on_right=self.listen, on_moved=self._orb_moved)
        self.panel = webview.create_window("Jarvis", str(UI / "panel.html"), js_api=self.bridge, width=420, height=700,
                                           x=sw - 600, y=sh - 790, frameless=True, easy_drag=False, on_top=False,
                                           resizable=True, min_size=(360, 480), background_color="#040907")

    # ---------------- events to the UI
    def emit(self, **e):
        try:
            self.panel.evaluate_js(f"window.onEvent && window.onEvent({json.dumps(e)})")
        except Exception:
            pass

    def set_state(self, state, detail=None, level=None):
        self.state = state
        self.emit(type="state", state=state, detail=detail)
        self.orb.set(state, level)

    def say(self, text):
        if self.speaker and self.settings.get("voice"):
            self._said(text)
            self.speaker.say(text)

    def _said(self, text):
        now = time.time()
        self.said_log = [(t, x) for t, x in self.said_log if now - t < 90] + [(now, text)]

    # ---------------- startup
    def boot(self):
        self.set_state("starting", "Loading the brain…")
        try:
            secs = self.agent.start()
        except Exception as e:
            self.emit(type="error", text=f"The model failed to start: {e}")
            return
        try:
            from voice import Speaker
            self.speaker = Speaker()
        except Exception as e:
            self.emit(type="toast", text=f"Spoken replies not available ({e}).")
        if self.speaker:
            self.speaker.speed = float(self.settings.get("speech_speed", 1.0))
        tools.APP["set"] = self.brain_setting
        self.start_ears()                # its own error message; a microphone problem never stops typing
        bind_children_to_app()
        self.busy, self.ready_flag = False, True
        self.set_state("idle", f"Ready · started in {secs:.0f}s")
        name = next((re.sub(r"^User's name is ", "", f) for f in tools.load_json(tools.MEMORY, []) if f.startswith("User's name is")), "")
        h = datetime.now().hour
        self.say(("Good morning" if h < 12 else "Good afternoon" if h < 17 else "Good evening") + (f", {name}." if name else ".")
                 + " Ready when you are.")    # (never says its own name -> can't wake itself)
        threading.Thread(target=self.reminder_loop, daemon=True).start()
        threading.Thread(target=self.hotkey_loop, daemon=True).start()
        if os.environ.get("JARVIS_SELFTEST"):
            threading.Thread(target=self.selftest, daemon=True).start()

    def selftest(self):
        """Drives the real UI end to end and logs what the panel shows (set JARVIS_SELFTEST=1)."""
        out = open(tools.DATA / "selftest.log", "w", encoding="utf-8")
        out.write(f"VOICE: speaker={bool(self.speaker)} ears={bool(self.ears and self.ears.running)} "
                  f"stt={self.ears.stt.name if self.ears else None} wake={bool(self.ears and self.ears.wake_on)} "
                  f"voice_filter={bool(self.ears and self.ears.verified)}\n"); out.flush()
        js = lambda code: self.panel.evaluate_js(code)
        def wait_idle(timeout=120):
            t0 = time.time()
            while (self.busy or self.state in ("thinking", "working")) and time.time() - t0 < timeout:
                time.sleep(0.3)
        # read-only requests only: the self-test must never open, close or change anything on the PC
        tests = ["I asked you about my system.", "how many profiles are in my Google Chrome",
                 "what time is it", "what is 17 times 23"]
        for q in tests:
            t0 = time.time()
            js(f"document.getElementById('input').value = {json.dumps(q)}; document.getElementById('sendBtn').click()")   # like a user typing
            time.sleep(0.5); wait_idle()
            r = js("(() => { const b = [...document.querySelectorAll('.bubble')].pop(); const m = [...document.querySelectorAll('.msg-j')].pop();"
                   " return JSON.stringify({bubbles: document.querySelectorAll('.bubble').length, cards: m ? [...m.querySelectorAll('.card,.pill')].map(c => c.className + ':' + c.innerText.slice(0,60).replace(/\\n/g,' ')) : [],"
                   " steps: m ? m.querySelectorAll('.step').length : 0, text: b ? b.innerText.slice(0,160) : null, status: document.getElementById('status').innerText}) })()")
            out.write(f"{time.time()-t0:5.1f}s  {q}\n      {r}\n"); out.flush()
        try:
            self._selftest_rest(out, js, wait_idle)
        except Exception:
            import traceback
            out.write("SELFTEST ERROR:\n" + traceback.format_exc()); out.close()

    def _selftest_rest(self, out, js, wait_idle):
        # permission card -> click Deny in the panel (tests JS -> Python bridge)
        sandbox = tools.DATA / "selftest_sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)
        (sandbox / "todo.md").write_text("self-test file - Jarvis must NOT delete this\n", encoding="utf-8")
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
        # stop mid-answer (typed "stop" while busy)
        js("document.getElementById('backBtn').click()"); time.sleep(0.5)
        send = lambda q: js(f"document.getElementById('input').value = {json.dumps(q)}; document.getElementById('sendBtn').click()")
        send("Tell me a 150 word story about the sea."); time.sleep(3)
        drafted = js("(document.querySelector('.bubble.draft')||{}).innerText || ''")
        last_bubble = "[...document.querySelectorAll('.bubble')].pop().innerText"
        t1 = time.time(); send("stop"); wait_idle()
        out.write(f"STOP: draft_while_writing={bool(drafted)} stopped_in={time.time()-t1:.1f}s last={js(last_bubble)!r}\n")
        # queue: a second request while busy is done next
        send("Tell me a short joke."); time.sleep(0.8); send("what is 12 times 12"); time.sleep(0.5)
        queued = js("document.querySelectorAll('.msg-you.queued').length")
        time.sleep(1); wait_idle(); time.sleep(1); wait_idle()
        out.write(f"QUEUE: queued_shown={queued} last={js(last_bubble)!r}\n")
        out.flush()
        # theme switch (panel + orb) and back
        users_theme = self.settings.get("theme", "glass")
        js("document.getElementById('settingsBtn').click()"); time.sleep(0.5)
        js("document.querySelector('[data-theme-pick=crimson]').click()"); time.sleep(1)
        out.write("THEME: " + str(js("JSON.stringify({html: document.documentElement.dataset.theme, saved: true})")) +
                  f" settings={self.settings.get('theme')}\n")
        js(f"document.querySelector('[data-theme-pick={users_theme}]').click()"); time.sleep(0.5)   # back to the user's choice
        js("document.getElementById('backBtn').click()")
        # live caption + mic level events render
        self.emit(type="caption", text="open chrome in incognito mode and"); self.emit(type="level", v=0.6); time.sleep(0.3)
        out.write("CAPTION: " + str(js("document.getElementById('liveCap')?.innerText")) + "\n")
        self.emit(type="caption", text=""); self.emit(type="level", v=0)
        out.write("DONE\n"); out.close()

    def on_panel_ready(self):
        self.push_settings()
        self.orb.set_theme(self.settings.get("theme", "glass"))
        if self.ready_flag:
            self.set_state("idle")

    # ---------------- requests
    STOP_RE = re.compile(r"^\W*(?:hey\s+)?(?:jarvis\W+)?(stop|cancel|abort|never ?mind|wait|hold on|enough|shut up|be quiet|quiet|top)"
                         r"(?:\s+(?:it|that|this|now|please|talking|everything|working))*\W*$", re.I)

    def submit(self, text, echo=False, via="typed"):
        """New request. While Jarvis is busy: 'stop' cancels the current job; anything else is queued and done next."""
        text = (text or "").strip()
        if not text or not self.ready_flag:
            return
        from speech import is_stop
        if self.STOP_RE.match(text) or (via != "typed" and is_stop(text)):
            self.stop_current(said=True)
            return
        if self.ears and self.ears.mode == "capture" and via == "typed":
            self.ears.cancel("typed instead")
        with self.qlock:
            if self.busy:
                self.queue.append((text, via))
                self.emit(type="queued", text=text, n=len(self.queue))
                return
            self.busy, self.last_via = True, via
        self.emit(type="heard", text=text)
        self.no_followup = False
        self.hush()
        threading.Thread(target=self._work, args=(text,), daemon=True).start()

    def stop_current(self, said=False):
        """Stop talking, stop the current job (mid-thought or between steps), drop the queue, stop listening."""
        self.hush()
        self.no_followup = True
        if self.ears and self.ears.mode == "capture":
            self.ears.cancel("stopped")
        with self.qlock:
            dropped = len(self.queue)
            self.queue.clear()
        for cid, (ev, box) in list(self.confirms.items()):   # a pending Allow/Deny counts as Deny
            box["ok"] = False; ev.set()
        if self.busy:
            self.agent.cancel()
            self.emit(type="toast", text="Stopped." + (f" Dropped {dropped} queued request(s)." if dropped else ""))
        elif said:
            self.set_state("idle", "Stopped")
        if dropped:
            self.emit(type="unqueue")

    def _work(self, text):
        from agent import Cancelled
        t0 = time.time()
        self.set_state("thinking")
        streamed = []

        def on_sentence(sent):          # speak while the reply is still being written
            if self.speaker and self.settings.get("voice"):
                if not streamed:
                    self.set_state("speaking")
                streamed.append(sent)
                self._said(sent)
                self.speaker.say(sent)

        try:
            answer, actions = self.agent.run(text, self._confirm, self._narrate,
                                             on_delta=lambda t: self.emit(type="draft", text=t), on_sentence=on_sentence)
        except Cancelled:
            self.hush()
            self.emit(type="answer", text="Stopped.", cards=[], secs=time.time() - t0, tools=[])
            self._done_work(speak=None)
            return
        except Exception as e:
            self.emit(type="error", text=str(e)); self._done_work(speak=None); return
        secs = time.time() - t0
        self.last_answer = answer
        self.emit(type="answer", text=answer, cards=build_cards(actions), secs=secs, tools=[a["tool"] for a in actions])
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "user": text, "answer": answer,
                                "actions": [{k: v for k, v in a.items() if k != "result"} for a in actions]}, ensure_ascii=False) + "\n")
        # speak whatever wasn't already spoken while streaming
        rest = [x for x in re.split(r"(?<=[.!?])\s+|\n+", answer) if x.strip() and x.strip() not in self.agent.spoken]
        self._done_work(speak=" ".join(rest) if streamed or True else answer)

    def _done_work(self, speak):
        with self.qlock:
            nxt = self.queue.pop(0) if self.queue else None
            self.busy = False
        if nxt:                                    # queued request: start it now (speech of the last answer continues)
            if speak and self.speaker and self.settings.get("voice"):
                self.say(speak)
            self.emit(type="begin", text=nxt[0])
            self.submit(nxt[0], via=nxt[1])
            return
        if speak and self.speaker and self.settings.get("voice"):
            self.set_state("speaking")
            self.say(speak)
            threading.Thread(target=self._idle_after_speech, daemon=True).start()
        elif self.speaker and self.speaker.busy() and self.settings.get("voice"):
            self.set_state("speaking")
            threading.Thread(target=self._idle_after_speech, daemon=True).start()
        else:
            self.set_state("idle")
            self._maybe_follow_up()

    def _idle_after_speech(self):
        time.sleep(0.3)
        while self.speaker and self.speaker.busy() and self.state == "speaking":
            time.sleep(0.15)
        if self.state == "speaking":
            self.set_state("idle")
            self._maybe_follow_up()

    def _maybe_follow_up(self):
        """Conversation mode: after answering a spoken request, listen ~5 s for a follow-up (no 'Hey Jarvis' needed).
        Not after 'stop'. Only your voice, and only things that sound like they're said to Jarvis, are taken."""
        if self.no_followup:
            self.no_followup = False
            return
        if (self.ears and self.settings.get("followup") and self.last_via in ("wake", "button", "followup")
                and not self.busy and self.state == "idle" and self.ears.mode == "idle"):
            time.sleep(0.25)             # let the speaker's echo die away
            self.set_state("listening", "Listening for a follow-up…")
            self.ears.listen("followup")

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

    # ---------------- voice (one always-open mic: wake word, mic button, follow-ups)
    def start_ears(self):
        try:
            from speech import Ears, StreamingSTT, WhisperSTT
            try:
                stt = StreamingSTT()
            except Exception as e:              # big model missing -> old whisper-server
                from voice import Listener
                self.listener = Listener(tools.DATA / "whisper.log"); self.listener.start()
                stt = WhisperSTT(self.listener)
                self.emit(type="toast", text=f"Using the older speech model ({e}).")
            self.ears = Ears(stt, self.on_ears, wake=bool(self.settings.get("wake")), verifier_path=VOICE_FILTER,
                             sensitivity=float(self.settings.get("wake_sensitivity", 0.4)))
            self.ears.log_path = tools.DATA / "ears.log"
            self.ears.paused = lambda: self.training or self.state in ("waiting", "starting")
            self.ears.speaking = lambda: bool(self.speaker and self.speaker.busy())
            self.ears.recent_spoken = lambda: [x for t, x in self.said_log if time.time() - t < 90]
            self.ears.expecting_answer = lambda: self.last_answer.rstrip().endswith("?")
            self._load_voice_id()
            self.ears.start()
        except Exception as e:
            self.ears = None
            self.emit(type="toast", text=f"Microphone not available ({e}). You can still type.")

    def on_ears(self, kind, **d):
        if kind == "level":
            self.orb.set(None if self.busy else "listening", d["v"])
            self.emit(type="level", v=round(d["v"], 2))
        elif kind == "wake":
            if d.get("interrupt"):
                self.hush()
            self.show_panel()
            if not self.busy:
                self.set_state("listening", "Yes?")
            else:
                self.emit(type="toast", text="Listening… (say \"stop\" to cancel, or a new request to queue it)")
            if self.speaker:
                self.speaker.chime("wake")
        elif kind == "partial":
            self.emit(type="caption", text=d["text"])
        elif kind == "final":
            self.emit(type="caption", text="")
            if not self.busy:
                self.set_state("understanding")
            self.submit(d["text"], echo=True, via=d.get("via") or "button")
        elif kind == "cancel":
            if d.get("silent"):                  # a weak wake that never heard "Jarvis": nothing happened as far as you're concerned
                return
            self.emit(type="caption", text="")
            # a soft falling tone = "I've stopped listening" (after a follow-up window or a wake that heard nothing)
            if self.speaker and not self.busy and d.get("via") in ("followup", "wake", "button") and \
                    d.get("reason") in ("nothing heard", "not your voice", "not for me", "too short", "false wake"):
                self.speaker.chime("off")
            if self.busy and d.get("reason") != "stop":
                self.set_state("thinking" if self.state == "listening" else self.state)
                return
            reason, via = d.get("reason"), d.get("via")
            if reason == "stop":
                self.stop_current(said=True)
                return
            if via == "button" and reason == "nothing heard":
                self.set_state("idle", "Didn't catch anything")
            elif reason in ("not your voice", "not for me"):
                self.set_state("idle", f"Ignored “{(d.get('text') or '')[:40]}” — {reason}")
            else:
                self.set_state("idle")
        elif kind == "error":
            self.emit(type="toast", text=d.get("text", "Microphone problem"))

    def listen(self):
        """Mic button / Ctrl+Space / Ctrl+Alt+J: start listening (press again to stop)."""
        if not self.ears:
            self.emit(type="toast", text="The microphone isn't available.")
            return
        if self.ears.mode == "capture":
            self.ears.cancel("cancelled")
            return
        if not self.ready_flag:
            return
        self.hush()
        self.show_panel()
        if not self.busy:
            self.set_state("listening")
        if self.speaker:
            self.speaker.chime("wake")
        time.sleep(0.12)                 # don't capture our own chime
        self.ears.listen("button")

    def train_voice(self):
        """Settings -> 'Teach Jarvis my voice': record 'Hey Jarvis' x10 + 4 normal sentences, then build the voice filter."""
        if not self.ears or self.training or self.busy:
            return
        self.training = True
        threading.Thread(target=self._train_worker, daemon=True).start()

    def _train_worker(self):
        import shutil, wave
        from speech import train_voice_filter, RATE
        d = tools.DATA / "voice"
        pos, neg = d / "hey_jarvis", d / "other"
        shutil.rmtree(pos, ignore_errors=True); shutil.rmtree(neg, ignore_errors=True)
        pos.mkdir(parents=True); neg.mkdir(parents=True)
        prompts = [("pos", "Say:  “Hey Jarvis”")] * 10 + [("neg", f"Read aloud:  “{s}”") for s in TRAIN_SENTENCES]
        try:
            i = 0
            while i < len(prompts):
                kind, text = prompts[i]
                self.emit(type="train", step=i + 1, total=len(prompts), prompt=text,
                          hint="Normal voice, like you'd really call it. Vary it a little." if kind == "pos" else "Normal speed.")
                time.sleep(0.4)
                audio = self.ears.record_clip(timeout=6)
                if audio is None or len(audio) < RATE * 0.4:
                    self.emit(type="train", step=i + 1, total=len(prompts), prompt=text, hint="Didn't hear that — once more.")
                    time.sleep(1.0)
                    continue
                with wave.open(str((pos if kind == "pos" else neg) / f"{i:02d}.wav"), "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(audio.astype("int16").tobytes())
                i += 1
            self.emit(type="train", step=len(prompts), total=len(prompts), prompt="Learning your voice…", hint="A few seconds.")
            res = train_voice_filter(pos, neg, VOICE_FILTER)
            heard = sum(s >= 0.5 for s in res["scores"])
            self.settings["wake_sensitivity"] = res["sensitivity"]
            tools.save_json(SETTINGS_FILE, {k: v for k, v in self.settings.items() if k != "autostart"})
            self.ears.sensitivity = res["sensitivity"]
            self.ears.load_wake(VOICE_FILTER)
            self._load_voice_id(force=True)
            self.emit(type="train", done=True, ok=True,
                      prompt="Done — Jarvis knows your voice now.",
                      hint=f"The basic wake model heard {heard} of your 10 tries on its own; with your voice filter it now listens at "
                           f"sensitivity {res['sensitivity']} and ignores other voices. Try “Hey Jarvis”.")
        except Exception as e:
            self.emit(type="train", done=True, ok=False, prompt="Training failed", hint=str(e))
        finally:
            self.training = False
            self.push_settings()

    def _load_voice_id(self, force=False):
        """Owner-voice check for follow-ups (enrolled from the voice-training clips)."""
        try:
            from speech import VoiceID
            import wave
            import numpy as np
            vid = VoiceID(VOICE_ID)
            if vid.me is None or force:
                clips = []
                for f in sorted((tools.DATA / "voice").glob("*/*.wav")):
                    with wave.open(str(f)) as w:
                        clips.append(np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768)
                if not clips or not vid.enroll(clips):
                    return
            self.ears.voice_id = vid
        except Exception as e:
            print("voice id not available:", e)

    def forget_voice(self):
        for f in (VOICE_FILTER, VOICE_ID):
            if f.exists():
                f.unlink()
        if self.ears:
            self.ears.voice_id = None
        self.settings.pop("wake_sensitivity", None)
        tools.save_json(SETTINGS_FILE, {k: v for k, v in self.settings.items() if k != "autostart"})
        if self.ears:
            self.ears.sensitivity = 0.4
            self.ears.load_wake(None)
        self.push_settings()


    def hush(self):
        if self.speaker:
            self.speaker.stop()

    # ---------------- windows
    def _orb_moved(self, x, y):
        time.sleep(0.6)                  # after the drag ends, remember where the orb is (logical pixels)
        r = __import__("ctypes").wintypes.RECT()
        __import__("ctypes").windll.user32.GetWindowRect(self.orb.hwnd, __import__("ctypes").byref(r))
        sc = getattr(self.orb, "scale", 1.0)
        self.settings["orb_pos"] = [int(r.left / sc), int(r.top / sc)]
        tools.save_json(SETTINGS_FILE, {k: v for k, v in self.settings.items() if k != "autostart"})

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
        self.emit(type="settings", settings=self.settings, memory=tools.load_json(tools.MEMORY, []), skills=tools.list_skills(),
                  voice_trained=VOICE_FILTER.exists(), stt=self.ears.stt.name if self.ears else None)

    def set_setting(self, key, value):
        if key == "wake" and self.ears:
            self.ears.wake_on = bool(value)
        if key == "autostart":
            if value:
                bat = ROOT / "run_jarvis.bat"
                ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{STARTUP_LNK}');$s.TargetPath='{bat}';"
                      f"$s.WorkingDirectory='{ROOT}';$s.WindowStyle=7;$s.Save()")
                subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=subprocess.CREATE_NO_WINDOW)
            elif STARTUP_LNK.exists():
                STARTUP_LNK.unlink()
            value = STARTUP_LNK.exists()
        if key == "theme":
            value = value if value in ("glass", "emerald", "crimson") else "glass"
            self.orb.set_theme(value)
        if key == "voice" and not value:
            self.hush()
        self.settings[key] = value
        tools.save_json(SETTINGS_FILE, {k: v for k, v in self.settings.items() if k != "autostart"})
        self.push_settings()

    def brain_setting(self, key, value):
        """jarvis_settings tool -> really change the app. Returns what happened (for the answer)."""
        if key == "speech_speed":
            cur = float(self.settings.get("speech_speed", 1.0))
            v = str(value)
            num = re.search(r"\d+(\.\d+)?", v)
            new = (float(num.group()) if num and float(num.group()) <= 3 else
                   1.0 if "normal" in v or "default" in v else
                   cur / 1.2 if re.search(r"slow|decrease|reduce|lower", v) else cur * 1.2)
            new = round(max(0.6, min(1.8, new)), 2)
            self.settings["speech_speed"] = new
            if self.speaker:
                self.speaker.speed = new
            tools.save_json(SETTINGS_FILE, {k: v for k, v in self.settings.items() if k != "autostart"})
            return f"Speaking speed set to {new}x (was {cur}x)."
        self.set_setting(key, value)
        names = {"theme": "Theme", "voice": "Spoken replies", "wake": "Wake word", "followup": "Follow-up listening"}
        val = value if key == "theme" else ("on" if value else "off")
        return f"{names.get(key, key)} is now {val}."

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
            if self.ears:
                self.ears.stop()
            self.agent.stop()
            if self.listener:
                self.listener.stop()
        finally:
            self.orb.destroy()
            for w in (self.panel,):
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
