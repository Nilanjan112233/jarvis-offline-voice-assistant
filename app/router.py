"""System-1 router: fast intent detection BEFORE the language model.

How it works:
- A tiny embedding model (bge-small-en, 37 MB) turns the request into a vector (~15 ms).
- It's compared with example phrases for each intent (no training - just examples).
- Simple commands (volume, pause/next, lock, screenshot, timer, open app, battery, time...) are DONE DIRECTLY:
  a keyword gate + high similarity + a filled slot are all required, otherwise it goes to the LLM.
  No language model = instant, and it can't "claim" something it didn't do.
- For everything else it picks the few tools that fit the top intents, so the LLM sees ~8-15 tools instead of 44.
"""
import json, re, subprocess, time, urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "engine" / "llama-server.exe"
EMBED_MODEL = ROOT / "models" / "bge-small-en-v1.5-q8_0.gguf"
PORT = 8101

QUESTIONY = re.compile(r"^\s*(how (do|can|to|does|would)|why|what (is|are|does) (a|an|the)?\s*(volume|screenshot|timer)|explain|can you tell me how)\b", re.I)

# intent: examples, tools for the LLM, optional direct handler key + keyword gate
INTENTS = {
    "chitchat": {"ex": ["hi", "hello there", "how are you", "thanks", "thank you so much", "you're awesome", "good night",
                        "good morning jarvis", "who are you", "tell me a joke", "what's up", "nice work"], "tools": []},
    "general_knowledge": {"ex": ["what is the capital of France", "who wrote Hamlet", "what is 25 times 4", "explain gravity",
                                 "how many days in a year", "what does RAM stand for", "convert 5 km to miles", "define photosynthesis"],
                          "tools": ["run_python"]},
    "writing": {"ex": ["write an email to my boss", "rewrite this politely", "give me ideas for a party", "make a packing list",
                       "summarize this text", "write a python function", "draft a message saying sorry"], "tools": []},
    "weather": {"ex": ["what's the weather", "weather in Delhi", "will it rain tomorrow", "is it hot outside", "temperature in Mumbai"],
                "tools": ["get_weather"]},
    "news": {"ex": ["latest news", "what's happening in India", "tech news today", "cricket headlines", "any news about AI"],
             "tools": ["get_news", "web_search", "read_webpage"]},
    "web_lookup": {"ex": ["who is the CEO of Google", "search for the best phones", "gold price today", "bitcoin price",
                          "who won the match yesterday", "when was Nvidia founded", "look up the release date",
                          "summarize this web page https", "what does this website say"],
                   "tools": ["web_search", "read_webpage", "get_news"]},
    "currency": {"ex": ["convert 100 dollars to rupees", "how much is 50 euros in INR", "dollar rate today"], "tools": ["convert_currency"]},
    "world_time": {"ex": ["what time is it in Tokyo", "time in London now", "current time in New York"], "tools": ["world_time"]},
    "pc_status": {"ex": ["how much battery do I have", "is my laptop charging", "how much RAM is free", "which app uses most memory",
                         "free space on C drive", "how many CPU cores", "how long has my PC been on", "which windows version",
                         "screen resolution", "how many processes are running", "system check"],
                  "tools": ["system_info", "run_python", "run_command"]},
    "network": {"ex": ["what's my IP address", "which wifi am I connected to", "what is my public IP", "is my internet working"],
                "tools": ["network_info", "run_command"]},
    "installed": {"ex": ["which browsers are installed", "is python installed", "what apps do I have installed", "do I have VLC"],
                  "tools": ["installed_apps", "run_command", "run_python"]},
    "files": {"ex": ["what's in my downloads folder", "how many images are in this folder", "count the pdf files", "read this text file",
                     "find files named report", "biggest file in the folder", "what is taking space on my D drive",
                     "total size of this folder", "sum the numbers in data.csv", "how many words in this file"],
              "tools": ["list_files", "read_file", "find_files", "largest_files", "folder_sizes", "run_python"]},
    "file_change": {"ex": ["delete this file", "move the video to another folder", "create a file called notes.txt",
                           "rename this folder", "download this file", "copy the file"],
                    "tools": ["write_file", "move_file", "delete_file", "download_file", "list_files", "run_command"]},
    "apps": {"ex": ["open notepad", "launch chrome", "start spotify", "open youtube", "close notepad", "open google.com"],
             "tools": ["open_app", "close_app", "open_url"]},
    "music": {"ex": ["play some music", "play Arijit Singh songs", "put on lofi", "pause the music", "next song", "previous track"],
              "tools": ["play_music", "play_video", "media_control"]},
    "device": {"ex": ["volume up", "turn it down", "mute", "take a screenshot", "lock my computer", "open bluetooth settings", "copy this to clipboard", "what's on my clipboard",
                      "open display settings"], "tools": ["set_volume", "screenshot", "lock_pc", "open_settings", "set_clipboard", "get_clipboard"]},
    "reminders": {"ex": ["remind me to call mom at 7", "set a timer for 10 minutes", "wake me up at 6", "what reminders do I have",
                         "cancel the reminder", "set an alarm"],
                  "tools": ["set_reminder", "set_timer", "list_reminders", "cancel_reminder"]},
    "lists_notes": {"ex": ["add milk to my shopping list", "what's on my to-do list", "mark the task done", "note that the code is 1234",
                           "what notes do I have", "read my notes"],
                    "tools": ["add_task", "list_tasks", "complete_task", "take_note", "read_notes"]},
    "memory": {"ex": ["my name is Rahul", "I live in Pune", "remember that I like tea", "forget where I live",
                      "what's my name", "where do I live", "I prefer short answers"],
               "tools": ["remember", "forget"]},
    "past_chats": {"ex": ["what did we talk about yesterday", "what did I ask you earlier", "remind me what you told me about the weather",
                          "what were we discussing last time", "list what we talked about today"], "tools": ["recall_chats"]},
    "skills": {"ex": ["learn this skill", "when I say good morning do this", "teach you a routine"], "tools": ["save_skill", "use_skill"]},
    "desktop": {"ex": ["click the save button", "in chrome open a new tab", "go to the settings in notepad", "type hello in notepad",
                        "switch to chrome", "fill in the form", "press the next button", "close this tab", "what's on my screen",
                        "scroll down in chrome", "open the file menu", "use my mouse to click", "open my chrome profile"],
                 "tools": ["desktop_look", "desktop_do", "open_app", "chrome_profiles"]},
    "messages": {"ex": ["send a whatsapp to Rahul", "text mom that I'm late", "message Priya happy birthday"], "tools": ["send_message"]},
    "personal_unknown": {"ex": ["what did I eat for breakfast", "what's my wife's name", "what did my boss say", "my phone battery",
                                "read my email", "what's on my calendar"], "tools": []},
}
CORE_TOOLS = ["web_search", "run_python", "run_command", "remember", "use_skill", "save_skill"]
KEYWORD_TOOLS = [
    (r"\b(on|in|of) (my |the |this )?(screen|window|page|tab)\b|\bwhat (do you see|does it say|is written)\b|\bread (this|it|the screen|my screen)\b",
     ["read_screen"]),
    (r"\b(news|headlines?)\b", ["get_news", "web_search"]),
    (r"\b(we (talked|spoke|chatted|discussed)|did we|did i (ask|tell|say)|i (asked|told) you|our (chat|conversation)s?|"
     r"last time|you (said|told me) (earlier|yesterday|before))\b", ["recall_chats"]),
    (r"\b(weather|rain|temperature|forecast|humid)", ["get_weather"]),
    (r"\b(rupees?|dollars?|euros?|pounds?|yen|usd|inr|eur|currency|exchange rate)\b", ["convert_currency"]),
    (r"\btime in\b|\btime is it in\b", ["world_time"]),
    (r"\b(battery|ram|memory usage|cpu|disk|storage|uptime|resolution|processes)\b", ["system_info"]),
    (r"\b(ip address|wi-?fi|network|internet)\b", ["network_info"]),
    (r"\b(installed|browsers?)\b", ["installed_apps"]),
    (r"\b(remind|reminder|timer|alarm)\b", ["set_reminder", "set_timer", "list_reminders", "cancel_reminder", "snooze_reminder"]),
    (r"\bsnooze\b|\bremind me again\b", ["snooze_reminder"]),
    (r"\b(what do you see|look at (my|the|this) (screen|window|picture|photo|image)|what does (it|this|that|the \w+) look like|"
     r"describe (this|the|my|that) (screen|picture|photo|image|window)|\.(jpe?g|png|bmp|gif)\b|(picture|photo|image) (of|in|on))",
     ["look_at_screen", "describe_image"]),
    (r"\b(skill|routine)\b", ["save_skill", "use_skill"]),
    (r"\b(your (voice|colou?r|theme|speed|look|settings)|(talk|speak)(ing)? (faster|slower|speed))\b", ["jarvis_settings"]),
    (r"\bvolume\b", ["set_volume"]),
    (r"\b(youtube|video|song|music|play)\b", ["play_video", "play_music", "media_control"]),
    (r"\b(incognito|private (window|mode|tab)|inprivate)\b", ["open_incognito", "play_video"]),
    (r"\b(my system|this system|my pc|my computer|my laptop)\b", ["system_info"]),
    (r"\b(profiles?)\b.*\bchrome\b|\bchrome\b.*\bprofiles?\b", ["chrome_profiles"]),
    (r"\b(click|tap|press the|type .* in|on (my )?screen|window|tab|menu|button|scroll)\b", ["desktop_look", "desktop_do"]),
    (r"\b(current|latest|today|now|who is the|ceo|president|prime minister|price|score|won)\b", ["web_search", "get_news"]),
]

# --- direct (no LLM) handlers: (intent, keyword gate, slot extractor -> (tool, args) or None)
def _app_slot(t):
    m = re.match(r"^\s*(?:please\s+)?(?:open|launch|start|run)\s+(?:the\s+|my\s+)?(.+?)(?:\s+(?:app|for me|please|now))?\s*[.!?]*$", t, re.I)
    return m.group(1).strip() if m else None


_NUMS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
                                    "sixteen seventeen eighteen nineteen".split())}
_NUMS.update({w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())})
_NUMS["hundred"] = 100; _NUMS["full"] = 100; _NUMS["max"] = 100; _NUMS["maximum"] = 100; _NUMS["half"] = 50


def spoken_number(t):
    """'to sixty five' / 'at 60%' / 'to max' -> 65 / 60 / 100 (only after to/at/is/= so 'turn it up' isn't a number)."""
    m = re.search(r"\b(?:to|at|is|=|of)\s+(?:about\s+|around\s+|more or less\s+)?(\d{1,3})\s*%?", t)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(?:to|at|is|of)\s+(?:about\s+|around\s+|more or less\s+)?([a-z]+)(?:[\s-]+([a-z]+))?", t)
    if m and m.group(1) in _NUMS:
        n = _NUMS[m.group(1)]
        if m.group(2) in _NUMS and n >= 20 and _NUMS[m.group(2)] < 10:
            n += _NUMS[m.group(2)]
        return n
    return None


def self_command(text):
    """Commands about Jarvis itself / the user's identity, answered without the LLM."""
    t = text.strip().lower().rstrip(".!? ")
    m = re.search(r"\b(?:change|switch|set|make|turn)\s+(?:your|the|jarvis'?s?)?\s*(?:colou?r|theme|look|skin)?\s*(?:to|into)\s+(?:the\s+)?(\w+)", t) \
        or re.search(r"\b(?:use|go|switch to)\s+(?:the\s+)?(glass|emerald|crimson)\b", t)
    if m and re.search(r"colou?r|theme|look|skin|glass|emerald|crimson", t):
        return "jarvis_settings", {"setting": "theme", "value": m.group(1)}
    if re.search(r"\b(talk|speak|talking|speaking|voice|speech|reply|replies)\b", t) and \
            re.search(r"\b(fast(er)?|quick(er)?|slow(er)?|speed|pace|rate)\b", t):
        v = "slower" if re.search(r"\b(slow(er)?|decrease|reduce|lower|down)\b", t) else \
            "normal" if re.search(r"\b(normal|default|reset)\b", t) else "faster"
        return "jarvis_settings", {"setting": "speech speed", "value": v}
    return None


def my_name():
    from tools import load_json, MEMORY
    for f in load_json(MEMORY, []):
        m = re.match(r"User's name is (.+?)\.?$", f)
        if m:
            return m.group(1)
    return None


def media_command(text):
    """'open chrome in incognito mode and play Play with Fire on YouTube' -> ('play_video', {...}); 'open incognito' -> open_incognito."""
    t = re.sub(r"[.!?]+$", "", text.strip())
    # something ALREADY open ("play the music that was playing before", "resume", "play that tab") -> resume it, don't search
    if re.search(r"\b(resume|continue|unpause)\b.{0,30}\b(music|song|video|playing|it|that)\b|^(?:please\s+)?(?:resume|unpause)\b"
                 r"|\bplay\b.{0,40}\b(was playing|playing before|that was on|already (open|playing)|(that|the|my) (tab|window))\b"
                 r"|\b(tab|window)\b.{0,80}\b(is |was )?playing\b|\b(music|song|video) (is |was )?(already )?playing (right now|already)\b", t, re.I):
        return "media_control", {"action": "play"}
    if re.fullmatch(r"(?:(?:can|could|would) you |please |jarvis,? )*(?:play|put on|start) (?:some |any |a little )?(?:background |nice |relaxing |good |calm )?"
                    r"(?:music|songs?|tunes)(?: in the background| for me| please| now)*", t, re.I):
        return "play_music", {"query": "background music"}
    inc = bool(re.search(r"\b(incognito|in ?private|private (mode|window|tab))\b", t, re.I))
    m = re.search(r"\bplay(?:\s+the)?(?:\s+(?:song|video|track))?\s+(.+?)\s+(?:on|in|from)\s+youtube\b", t, re.I) \
        or (re.search(r"\byoutube\b", t, re.I) and re.search(r"\bplay(?:\s+the)?(?:\s+(?:song|video|track))?\s+(.+?)$", t, re.I))
    if m:
        q = re.sub(r"\s+(?:in|on)\s+(?:an?\s+)?(?:incognito|private)(?:\s+(?:mode|window|tab))?.*$", "", m.group(1), flags=re.I)
        q = re.sub(r"\b(?:on|in|from)\s+youtube\b|\bsong\b$", "", q, flags=re.I).strip(" ,'\"")
        if q and not re.fullmatch(r"(it|something|anything|music|a song|songs)", q, re.I):
            return "play_video", {"query": q, "incognito": "yes" if inc else "no"}
    if inc:
        m = re.search(r"\bplay(?:\s+(?:me|us))?\s+(.+)$", t, re.I)
        if m:
            q = re.sub(r"\s+(?:in|on|with|using)\s+(?:an?\s+|the\s+)?(?:incognito|private|inprivate)\b.*$", "", m.group(1), flags=re.I)
            q = re.sub(r"\b(?:in|on)\s+(?:google\s+)?chrome\b.*$|\b(?:on|in|from)\s+youtube\b", "", q, flags=re.I).strip(" ,'\"")
            if re.fullmatch(r"(?:a|the|some|any|random|me)?\s*(?:song|music|songs|video|something)?", q, re.I):
                q = "top hits songs"
            return "play_video", {"query": q, "incognito": "yes"}
    if inc and re.fullmatch(r"(?:please\s+)?(?:open|start|launch)?\s*(?:google\s+)?(?:chrome|browser|a)?\s*(?:in\s+)?(?:an?\s+)?"
                            r"(?:incognito|private|inprivate)(?:\s+(?:mode|window|tab))?(?:\s+(?:in\s+)?chrome)?", t, re.I):
        return "open_incognito", {}
    return None


def _direct(text):
    """Return (tool, args, say) for a clear simple command, else None."""
    t = text.strip().lower().rstrip(".!? ")
    sk = skill_trigger(text)
    if sk:
        return "_skill", {"name": sk[0], "steps": sk[1]}, None
    med = self_command(text) or media_command(text)
    if med:
        return med[0], med[1], None
    dsk = desktop_command(text)
    if dsk:
        return "_desktop", dsk, None
    if re.fullmatch(r"\W*(?:um+|uh+)?\W*(what'?s|what is|do you know|tell me|say) my name\W*", text.strip().lower()):
        n = my_name()
        acct = Path.home().name
        return "_calc", {"answer": f"You're {n}." if n else
                         f"You haven't told me yet. Your Windows account is called {acct} - is that you? Say \"my name is ...\" and I'll remember it."}, None
    ans = calc(text)
    if ans:
        return "_calc", {"answer": ans}, None
    ans = pc_query(text)
    if ans:
        return "_calc", {"answer": ans, "source": "system_info"}, None
    m = re.match(r"^\s*(?:please\s+)?download\s+(https?://\S+)(?:\s+(?:as|to)\s+(\S+))?\s*$", text.strip(), re.I)
    if m:
        return "download_file", {"url": m.group(1), **({"filename": m.group(2)} if m.group(2) else {})}, None
    if QUESTIONY.search(t):
        return None
    m = re.match(r"^\s*(?:please\s+)?(?:make a note|note down|note|jot down|write down)\s*(?:that|:)?\s+(.{3,})$", text.strip(), re.I)
    if m and not re.search(r"\b(note ?book|notes? app)\b", t) and not re.search(r"\b(and|then) (also |remind|set|send|open)\b", t):
        return "take_note", {"text": m.group(1).strip()}, None
    if len(t.split()) > 9:
        return None
    if re.search(r"\b(mute|unmute)\b", t):
        return "set_volume", {"direction": "mute"}, None
    if re.search(r"\b(volume|sound)\b", t):
        lvl = spoken_number(t)
        if lvl is not None and 0 <= lvl <= 100:
            return "set_volume", {"direction": "set", "level": str(lvl)}, None
    if re.search(r"\b(volume|sound|louder|quieter)\b", t) or re.fullmatch(r"turn it (up|down)", t):
        if re.search(r"\b(down|lower|decrease|quieter|reduce|less)\b", t):
            return "set_volume", {"direction": "down"}, None
        if re.search(r"\b(up|louder|increase|raise|more)\b", t):
            return "set_volume", {"direction": "up"}, None
    if re.search(r"\b(pause|resume)\b", t) and re.search(r"\b(music|song|video|it|playback|track|spotify)\b|^(pause|resume)$", t):
        return "media_control", {"action": "pause"}, None
    if re.search(r"\b(next|skip)\b", t) and re.search(r"\b(song|track|music|one)\b|^(next|skip)$", t):
        return "media_control", {"action": "next"}, None
    if re.search(r"\b(previous|last|go back)\b", t) and re.search(r"\b(song|track)\b", t):
        return "media_control", {"action": "previous"}, None
    if re.search(r"\b(screenshot|screen shot|capture (the |my )?screen)\b", t):
        return "screenshot", {}, None
    if re.search(r"\block (my |the )?(pc|computer|laptop|screen|system)\b", t):
        return "lock_pc", {}, None
    m = re.search(r"\b(?:set |start )?(?:a )?timer (?:for )?(\d+(?:\.\d+)?\s*(?:sec|second|min|minute|hour|hr)s?)\b", t)
    if m:
        return "set_timer", {"duration": m.group(1)}, None
    if re.fullmatch(r"(what are |show |list )?(my )?(reminders|timers|upcoming reminders)|what reminders do i have", t):
        return "list_reminders", {}, None
    if re.fullmatch(r"(show |read |what are )?(my )?notes|what notes do i have|read my notes", t):
        return "read_notes", {}, None
    m = re.fullmatch(r"(?:what'?s|what is|show|read) (?:on )?(?:my )?([a-z -]+?) list", t)
    if m:
        return "list_tasks", {"list_name": m.group(1)}, None
    if re.fullmatch(r"(what('?s| is) the )?(time|date)( now| today| right now)?|what time is it( now)?|what('?s| is) (today'?s )?date", t):
        return "_now", {}, None
    app = _app_slot(text)
    if app and len(app.split()) <= 4:
        if re.search(r"\.[a-z]{2,}(/|$)", app):
            return "open_url", {"url": app}, None
        if not re.search(r"\b(file|folder|document|my mind|up|account|incognito|private|mode)\b", app.lower()):
            from tools import find_app
            if find_app(app):
                return "open_app", {"name": app}, None
    return None


class Router:
    def __init__(self, log_path):
        self.log_path, self.proc = log_path, None
        self.labels, self.vecs = [], None

    def start(self):
        log = open(self.log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen([str(SERVER), "-m", str(EMBED_MODEL), "--embedding", "--pooling", "cls", "--host", "127.0.0.1",
                                      "--port", str(PORT), "-t", "2", "-c", "512", "-ub", "512", "-np", "1"],
                                     stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=1)
                break
            except Exception:
                time.sleep(0.3)
        texts = []
        for name, it in INTENTS.items():
            for e in it["ex"]:
                self.labels.append(name); texts.append(e)
        self.vecs = self._embed(texts)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def _embed(self, texts):
        body = json.dumps({"input": texts}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/embeddings", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())["data"]
        v = np.array([d["embedding"] for d in data], dtype=np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    def route(self, text):
        """Returns dict: direct=(tool,args) or None, intents=[(name, score)], tools=[...], no_tools=bool."""
        t0 = time.time()
        direct = _direct(text)
        q = self._embed([text])[0]
        sims = self.vecs @ q
        best = {}
        for lab, s in zip(self.labels, sims):
            best[lab] = max(best.get(lab, -1), float(s))
        ranked = sorted(best.items(), key=lambda x: -x[1])
        top = [n for n, s in ranked[:5] if s > ranked[0][1] - 0.10 or s > 0.78] or [ranked[0][0]]
        tools = []
        for n in top:
            tools += INTENTS[n]["tools"]
        # keywords guarantee the right tools even when another intent wins (multi-part requests)
        for pat, extra in KEYWORD_TOOLS:
            if re.search(pat, text, re.I):
                tools += extra
        has_path = re.search(r"https?://|\b[a-z]:[\\/]|~[\\/]|\.(txt|csv|md|pdf|py|json|docx?|xlsx?)\b", text, re.I)
        if has_path:   # a real file/folder/URL -> file tools, never the notes tools
            tools = [t for t in tools if t not in ("read_notes", "take_note")] + ["read_file", "list_files", "find_files", "run_python"]
        no_tools = ranked[0][0] in ("chitchat", "writing", "personal_unknown") and ranked[0][1] - ranked[1][1] > 0.03 \
            and not has_path and not any(re.search(p, text, re.I) for p, _ in KEYWORD_TOOLS)
        if ranked[0][0] == "writing" and re.match(r"\s*(rewrite|rephrase|summari[sz]e this|translate|write|draft|make|give me)\b", text, re.I) \
                and not has_path:
            no_tools = True
        if not no_tools:
            tools += CORE_TOOLS
        return {"direct": direct, "intents": ranked[:4], "tools": list(dict.fromkeys(tools)), "no_tools": no_tools,
                "ms": round((time.time() - t0) * 1000)}


# ---------------------------------------------------------------- exact maths (no LLM: small models garble arithmetic)
import ast, math, operator

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos}


def _safe_eval(node):
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        l, r = _safe_eval(node.left), _safe_eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(r) > 100:
            raise ValueError("too big")
        return _OPS[type(node.op)](l, r)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand))
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "sqrt" and len(node.args) == 1:
        return math.sqrt(_safe_eval(node.args[0]))
    raise ValueError("not simple maths")


def _fmt(x):
    if isinstance(x, float) and x.is_integer():
        x = int(x)
    return f"{x:,}" if isinstance(x, int) else f"{x:,.4f}".rstrip("0").rstrip(".")


def calc(text):
    """'what is 17 times 23', '15% of 840', '2 to the power 10', 'square root of 144' -> exact answer, else None."""
    t = text.lower().strip().rstrip("?.! ")
    t = re.sub(r"^(what('?s| is)|calculate|compute|how much is|tell me|solve|jarvis,?)\s+", "", t)
    m = re.fullmatch(r"(?:a |an )?(\d+(?:\.\d+)?)\s*(?:%|percent)\s*(?:tip )?(?:of|on)\s*(?:rs\.?|₹|\$|rupees )?(\d+(?:\.\d+)?)(?:\s*rupees|\s*dollars)?", t)
    if m:
        p, n = float(m.group(1)), float(m.group(2))
        return f"{_fmt(p)} percent of {_fmt(n)} is {_fmt(p * n / 100)}."
    e = t
    for a, b in ((r"square root of", "sqrt"), (r"to the power( of)?", "**"), (r"\btimes\b|\bmultiplied by\b|\bx\b|×", "*"),
                 (r"\bdivided by\b|÷", "/"), (r"\bplus\b", "+"), (r"\bminus\b", "-"), (r"\bsquared\b", "**2"), (r"\bcubed\b", "**3"),
                 (r"\bmod\b", "%"), (r",", "")):
        e = re.sub(a, b, e)
    if not re.fullmatch(r"[\d\s.+\-*/%()sqrt]+", e) or not re.search(r"\d", e) or not re.search(r"[+\-*/%]|sqrt", e):
        return None
    e = re.sub(r"sqrt\s*(\d+(?:\.\d+)?)", r"sqrt(\1)", e)
    try:
        v = _safe_eval(ast.parse(e.strip(), mode="eval"))
    except Exception:
        return None
    return f"That's {_fmt(v)}."


def skill_trigger(text):
    """If the user says the name of a learned skill, return (name, steps)."""
    import tools
    t = re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()
    for p in tools.SKILLS.glob("*.md"):
        name = p.stem.replace("_", " ")
        if t == name or (name in t and len(t) <= len(name) + 12):
            return p.stem, p.read_text(encoding="utf-8")
    return None


# ---------------------------------------------------------------- direct PC answers (System 1: read the facts, no LLM)
def pc_query(text):
    """Simple questions about this PC answered straight from real data. Returns an answer string or None."""
    import psutil, tools
    t = text.lower().strip().rstrip("?.! ")
    if len(t.split()) > 12 or re.search(r"\b(and|then|also|why|how do|should|if)\b", t):
        return None   # multi-part or advice questions go to the LLM
    if re.search(r"\b(my )?phone'?s?\b", t) and re.search(r"\b(battery|charge|storage|notifications?)\b", t):
        return "I can't see your phone yet - it isn't connected to me. I can only check this PC."
    if re.search(r"\b(battery|charging|charged|plugged)\b", t):
        b = psutil.sensors_battery()
        if not b:
            return "This PC doesn't have a battery."
        state = "and charging" if b.power_plugged else "and running on battery"
        if not b.power_plugged and b.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN):
            state += f", about {b.secsleft // 3600} hours {b.secsleft % 3600 // 60} minutes left"
        return f"Battery is at {b.percent:.0f} percent {state}."
    m = re.search(r"\b(?:free |empty )?(?:space|storage)\b.*?\b([a-z])\s*(?::|drive)|\b([a-z])\s*drive\b.*\b(?:space|storage|free)\b", t)
    if m and re.search(r"\b(space|storage|free|left)\b", t):
        d = (m.group(1) or m.group(2)).upper()
        try:
            u = psutil.disk_usage(f"{d}:\\")
            return f"The {d} drive has {u.free/1e9:.1f} GB free out of {u.total/1e9:.0f} GB."
        except Exception:
            return f"There's no {d} drive on this PC."
    if re.search(r"\b(ram|memory)\b", t) and re.search(r"\b(free|available|left|how much|used)\b", t) and not re.search(r"\b(app|which|using)\b", t):
        vm = psutil.virtual_memory()
        return f"{vm.available/1e9:.1f} GB of RAM is free, out of {vm.total/1e9:.1f} GB."
    if re.search(r"\b(how long|uptime|been on|been running)\b", t) and re.search(r"\b(pc|computer|laptop|system|on)\b", t):
        import time as _t
        from datetime import timedelta
        up = timedelta(seconds=int(_t.time() - psutil.boot_time()))
        return f"Your PC has been on for {up.days} days and {up.seconds // 3600} hours."
    if re.search(r"\bscreen resolution\b|\bresolution\b.*\b(screen|display|monitor)\b", t):
        return f"Your screen resolution is {tools._screen_resolution()}."
    if re.fullmatch(r"(tell me |what about |give me |show me )?(about |info on |information about |the status of |details of )?"
                    r"(my |this )(system|pc|computer|laptop)( info| information| status| details)?|system (info|information|status|check)|"
                    r"(i )?asked (you )?about my (system|pc|computer)|how is my (pc|computer|laptop|system)( doing)?", t):
        b, vm = psutil.sensors_battery(), psutil.virtual_memory()
        disks = []
        for p in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(p.mountpoint)
                disks.append(f"{p.device.rstrip(chr(92))} has {u.free/1e9:.0f} GB free of {u.total/1e9:.0f}")
            except Exception:
                pass
        bat = f"Battery {b.percent:.0f} percent{' and charging' if b.power_plugged else ''}. " if b else ""
        return (f"You're on {tools._windows_name()}. {bat}RAM: {vm.available/1e9:.1f} of {vm.total/1e9:.0f} GB free. "
                f"CPU {psutil.cpu_percent(0.3):.0f} percent busy. " + ". ".join(disks) + ".")
    if re.search(r"\b(windows version|version of windows|which windows)\b", t):
        return f"You're running {tools._windows_name()}."
    if re.search(r"\b(cpu cores|how many cores|cores does)\b", t):
        return f"This PC has {psutil.cpu_count(False)} CPU cores ({psutil.cpu_count()} threads)."
    return None



# ---------------------------------------------------------------- direct desktop commands (System-1: no LLM)
def desktop_command(text):
    t = text.strip().rstrip(".!")
    q = r"""['"“”‘’]?"""
    m = re.match(rf"^(?:please\s+)?(?:open|launch|start)\s+(?:a\s+)?(?:new\s+)?(?P<app>[\w .+-]+?)\s+and\s+(?:type|write)\s+{q}(?P<text>.+?){q}(?:\s+in(?:to)?\s+it)?$", t, re.I)
    if m:
        return {"window": m["app"], "steps": [f"open {m['app']}", f"type {m['text']}"]}
    m = re.match(rf"^(?:please\s+)?(?:type|write)\s+{q}(?P<text>.+?){q}\s+in(?:to)?\s+(?:the\s+)?(?P<app>[\w .+-]+?)(?:\s+window)?$", t, re.I)
    if m and not re.search(r"\b(file|document|folder)\b", m["app"], re.I):
        return {"window": m["app"], "steps": [f"type {m['text']}"]}
    m = re.match(r"^(?:now\s+)?close\s+(?:that\s+|the\s+|this\s+)?(?:new\s+)?(?P<app>[\w .+-]+?)(?:\s+window)?\s+without\s+saving$", t, re.I)
    if m:
        return {"window": m["app"], "steps": ["click Close", "click Don't save if present"]}
    m = re.match(r"^(?:switch to|focus(?: on)?|go to)\s+(?:the\s+)?(?P<app>[\w .+-]+?)(?:\s+window)?$", t, re.I)
    if m and not re.search(r"\b(settings|page|website|folder|tab)\b", m["app"], re.I):
        return {"window": m["app"], "steps": [f"focus {m['app']}"]}
    m = re.match(r"^(?P<v>maximi[sz]e|minimi[sz]e)\s+(?:the\s+)?(?P<app>[\w .+-]+?)(?:\s+window)?$", t, re.I)
    if m:
        return {"window": m["app"], "steps": [m["v"]]}
    return None
