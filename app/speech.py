"""Live speech for Jarvis - fully offline, CPU.

  VAD        : Silero VAD (2 MB)                      -> is someone talking right now?
  Smart Turn : pipecat Smart Turn v3.2 (8.7 MB)       -> pause = finished, or just thinking mid-sentence?
  STT        : NVIDIA Nemotron Speech Streaming EN 0.6B int8 (sherpa-onnx) -> words appear while you talk
               (Moonshine base EN as a light fallback / phone option)
"""
import difflib, queue, re, threading, time
from pathlib import Path

import numpy as np

RATE = 16000
MODELS = Path(__file__).resolve().parents[1] / "voice" / "models"


# ================================================================ Whisper-style log-mel (for Smart Turn), numpy only
def _mel_filters(n_mels=80, n_fft=400, sr=RATE, fmax=8000.0):
    """Slaney mel filter bank (same as transformers WhisperFeatureExtractor)."""
    def hz_to_mel(f):
        f = np.asarray(f, dtype=np.float64)
        mel = 3.0 * f / 200.0
        log_region = f >= 1000.0
        mel = np.where(log_region, 15.0 + np.log(np.maximum(f, 1e-10) / 1000.0) / (np.log(6.4) / 27.0), mel)
        return mel

    def mel_to_hz(m):
        m = np.asarray(m, dtype=np.float64)
        f = 200.0 * m / 3.0
        return np.where(m >= 15.0, 1000.0 * np.exp((np.log(6.4) / 27.0) * (m - 15.0)), f)

    fft_freqs = np.linspace(0, sr / 2, n_fft // 2 + 1)
    mel_pts = mel_to_hz(np.linspace(hz_to_mel(0.0), hz_to_mel(fmax), n_mels + 2))
    fdiff = np.diff(mel_pts)
    slopes = mel_pts[None, :] - fft_freqs[:, None]          # (freq, mels+2)
    down = -slopes[:, :-2] / fdiff[:-1]
    up = slopes[:, 2:] / fdiff[1:]
    fb = np.maximum(0, np.minimum(down, up))                 # (freq, mels)
    fb *= (2.0 / (mel_pts[2:n_mels + 2] - mel_pts[:n_mels]))[None, :]
    return fb.astype(np.float32)


_MEL = None
_WINDOW = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(400) / 400)).astype(np.float32)   # periodic Hann


def log_mel(audio):
    """audio float32 [-1,1] at 16 kHz, 8 s -> (80, 800) like WhisperFeatureExtractor(chunk_length=8, do_normalize=True)."""
    global _MEL
    if _MEL is None:
        _MEL = _mel_filters()
    x = audio.astype(np.float32)
    x = (x - x.mean()) / np.sqrt(x.var() + 1e-7)                          # do_normalize
    x = np.pad(x, 200, mode="reflect")                                    # center=True
    n = 1 + (len(x) - 400) // 160
    idx = np.arange(400)[None, :] + 160 * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * _WINDOW, axis=1)) ** 2             # (frames, 201)
    mel = spec @ _MEL                                                     # (frames, 80)
    logm = np.log10(np.maximum(mel, 1e-10)).T[:, :-1]                     # drop last frame -> 800
    logm = np.maximum(logm, logm.max() - 8.0)
    return ((logm + 4.0) / 4.0).astype(np.float32)


DANGLING = set("and or but to the a an of for with at in on from about called named my your his her their this that "
               "is are was were if so because then like please can could would will should um uh".split())


def ends_unfinished(text):
    """'open chrome in incognito mode and' -> True (wait, they're still talking)."""
    w = re.findall(r"[a-z']+", (text or "").lower())
    return bool(w) and w[-1] in DANGLING


WAKE_WORDS = ("jarvis", "jarvas", "jervis", "javis", "jarviss", "travis", "jarbis", "garvis", "jarvez")


FIXES = [  # words speech models often get wrong in commands
    (r"\bin ?cognito\b|\bincognita\b|\bin carbonator\b", "incognito"),
    (r"\bincognito mod\b", "incognito mode"),
    (r"\b(s?potif(y|ier|ire|i)|spot ?a ?fly|spotty ?fi)\b", "Spotify"),
    (r"\byou ?tube\b", "YouTube"),
    (r"\bwhat'?s ?app\b", "WhatsApp"),
    (r"\bnote ?pad\b", "Notepad"),
    (r"\bg ?mail\b", "Gmail"),
]


def fix_words(text):
    for pat, rep in FIXES:
        text = re.sub(pat, rep, text, flags=re.I)
    return text


def split_wake(text):
    """'Hey Jarvis, open notepad' -> (True, 'open notepad'). The wake word must be within the first 4 words
    (fuzzy: 'Jervis', 'Travis'... but not 'Java')."""
    words = re.findall(r"[A-Za-z']+|[^A-Za-z'\s]+", text or "")
    seen = 0
    for i, w in enumerate(words):
        if not re.match(r"[A-Za-z']", w):
            continue
        lw = w.lower()[:-2] if w.lower().endswith("'s") else w.lower()
        prev = next((x.lower() for x in reversed(words[:i]) if re.match(r"[A-Za-z']", x)), "")
        if (lw in WAKE_WORDS or difflib.SequenceMatcher(None, lw, "jarvis").ratio() >= 0.66
                or re.search(r"[aeu]r+v[iea]s|[aeu]rbis|ervis", prev[-2:] + lw)   # 'Page Arvis', 'Jervis'
                or (lw in ("service", "servis", "services", "harvest", "jervice", "jarvice") and prev in ("hey", "hi", "a", "hay", "okay", "ok"))):
            tail = text[_offset_after(text, w, i, words):]
            return True, re.sub(r"^[\s,.!?:;-]+", "", tail).strip()
        seen += 1
        if seen >= 4:
            break
    return False, ""


_JARVIS_LIKE = re.compile(r"^(jarvis|jarvas|jervis|javis|jarvice|jervice|jarves|jarbis|jarvi|jarvish|charvis|garvis|jarvies|jaarvis)$")


def find_jarvis(text):
    """'open Notepad, Jarvis' / 'Jarvis, what's the time' / 'can you, Jarvis, check the weather' -> (True, request without
    the name). 'Hey Service' at the start also counts (the usual mishearing). Not: 'Java', 'service desk', 'Travis Scott'."""
    words = (text or "").split()
    for i, w in enumerate(words):
        lw = re.sub(r"[^a-z']", "", w.lower())
        lw = lw[:-2] if lw.endswith("'s") else lw
        prev = re.sub(r"[^a-z]", "", words[i - 1].lower()) if i else ""
        if _JARVIS_LIKE.match(lw) or (len(lw) >= 5 and difflib.SequenceMatcher(None, lw, "jarvis").ratio() >= 0.8) \
                or (i <= 1 and lw in ("service", "servis", "harvest") and prev in ("hey", "hi", "hay", "")):
            rest = words[:i] + words[i + 1:]
            if i >= 1 and re.fullmatch(r"\W*(hey|hi|hay|ok|okay|oh|hello)\W*", words[i - 1], re.I):
                rest = words[:i - 1] + words[i + 1:]
            cmd = re.sub(r"^[\s,.!?:;-]+|[\s,:;-]+$", "", " ".join(rest)).strip()
            cmd = re.sub(r"\s+([,.!?])", r"\1", cmd)
            return True, cmd
    return False, ""


def wake_command(text):
    """The request after the wake phrase. 'Hey Jarvis, open notepad' -> 'open notepad'. If the name was misheard
    ('Hey Service, open notepad') drop the greeting + one word - the wake model already heard 'Hey Jarvis'."""
    ok, cmd = split_wake(text)
    if ok:
        return cmd
    w = (text or "").split()
    i = 0
    while i < len(w) and re.fullmatch(r"\W*(hey|hi|hay|a|ok|okay|oh|hello|eh|uh|um)\W*", w[i], re.I):
        i += 1
    COMMON = {'what', 'whats', "what's", 'open', 'play', 'can', 'could', 'set', 'tell', 'close', 'turn', 'how', 'who', 'where',
              'when', 'why', 'is', 'are', 'please', 'show', 'search', 'find', 'start', 'stop', 'remind', 'call', 'send', 'take', 'go', 'do'}
    if i < len(w) and len(w) - i >= 2 and w[i].lower().strip(',.!?') not in COMMON:
        i += 1                                       # the misheard 'Jarvis'
    elif len(w) - i <= 1:
        return ""                                    # 'Hey Service' / a single word = just the (misheard) name
    return re.sub(r"^[\s,.!?:;-]+", "", " ".join(w[i:]))


def _offset_after(text, word, index, words):
    """Character offset in text just after the index-th token."""
    pos = 0
    for j, w in enumerate(words):
        pos = text.find(w, pos)
        if j == index:
            return pos + len(w)
        pos += len(w)
    return len(text)


class SmartTurn:
    """Is the speaker finished? Looks at the last 8 s of audio (tone of voice, not words). ~30-60 ms on CPU."""
    def __init__(self, path=MODELS / "smart-turn-v3.2-cpu.onnx"):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.inter_op_num_threads, so.intra_op_num_threads = 1, 2
        self.sess = ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])

    def done_probability(self, audio_f32):
        a = audio_f32[-8 * RATE:]
        if len(a) < 8 * RATE:
            a = np.pad(a, (8 * RATE - len(a), 0))
        out = self.sess.run(None, {"input_features": log_mel(a)[None]})
        return float(out[0][0][0])


# ================================================================ speech -> text
class StreamingSTT:
    """NVIDIA Nemotron Speech Streaming EN 0.6B (int8, sherpa-onnx). Text appears while you talk."""
    name = "nemotron"

    def __init__(self, threads=4):
        import sherpa_onnx
        d = next(MODELS.glob("sherpa-onnx-nemotron-speech-streaming-en*"))
        f = lambda pat: str(next(d.glob(pat)))
        self.r = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=f("tokens.txt"), encoder=f("encoder*.onnx"), decoder=f("decoder*.onnx"), joiner=f("joiner*.onnx"),
            num_threads=threads, model_type="nemo_transducer")
        s = self.new(); self.feed(s, np.zeros(RATE, np.float32)); self.finish(s)   # warm-up

    def new(self):
        return self.r.create_stream()

    def feed(self, s, f32):
        s.accept_waveform(RATE, f32)
        while self.r.is_ready(s):
            self.r.decode_stream(s)

    def text(self, s):
        return self.r.get_result(s).strip()

    def finish(self, s):
        s.accept_waveform(RATE, np.zeros(int(RATE * 0.6), np.float32))   # flush the last chunk
        s.input_finished()
        while self.r.is_ready(s):
            self.r.decode_stream(s)
        return self.text(s)


class WhisperSTT:
    """Fallback: the old whisper-server (no live words; transcribes at the end)."""
    name = "whisper"

    def __init__(self, listener):
        self.l = listener

    def new(self):
        return []

    def feed(self, s, f32):
        s.append(f32)

    def text(self, s):
        return ""

    def finish(self, s):
        if not s:
            return ""
        return self.l.transcribe((np.concatenate(s) * 32767).astype(np.int16))


# ================================================================ the ears: one always-open microphone
STOP_WORDS = {"stop", "top", "stop it", "jarvis stop", "cancel", "never mind", "nevermind", "forget it", "nothing", "shut up", "be quiet", "quiet"}
_STOP_TOKEN = re.compile(r"\b(stop+|stopp?ed|cancel|enough|shut up|be quiet|quiet|never ?mind|forget it|abort|halt)\b", re.I)


def is_stop(text):
    """'stop', 'Stop coroner stop', 'Or Nija stop, stop, stop' -> True. A stop word in a short phrase, or said twice.
    ('stop the music' / 'stop the timer' are requests, not 'stop listening'.)"""
    t = re.sub(r"[^a-z' ]", " ", (text or "").lower()).split()
    if not t:
        return False
    # "top top" = "stop stop" with the 's' clipped (common while Jarvis is talking): only stop-ish words in the phrase
    if set(t) <= {"top", "stop", "stopp", "sop", "stap", "jarvis", "hey", "please", "now", "it", "that", "this", "okay", "ok", "no"} and {"top", "stop", "stopp", "sop", "stap"} & set(t):
        return True
    hits = len(_STOP_TOKEN.findall(" ".join(t)))
    if hits >= 2:
        return True
    if hits == 1 and len(t) <= 4:
        return not re.search(r"\bstop (the|my|that|this|all|playing|music|song|video|timer|reminder|alarm|download)\b(?! (it|now|talking|listening))",
                             " ".join(t)) or " ".join(t) in ("stop that", "stop this", "stop it")
    return False


_REQUEST = re.compile(
    r"^(hey |hi |ok |okay |and |also |so |now |then |please |jarvis |oh |um |uh )*"
    r"(what|what's|whats|how|who|when|where|which|why|is|are|am|can|could|would|will|do|does|did|should|shall|tell|show|open|close|"
    r"play|pause|stop|resume|skip|next|set|turn|search|find|look|check|give|make|create|write|type|read|send|remind|add|remove|"
    r"delete|start|launch|go|take|call|increase|decrease|raise|lower|mute|unmute|switch|change|save|remember|forget|list|count|"
    r"explain|translate|calculate|help|let's|lets|i want|i'd like|i would like|i need|yes|no|yeah|yep|nope|sure|thanks|thank you|"
    r"try|again|repeat|louder|quieter|faster|slower|cancel|copy|paste|scroll|click|minimize|maximize|lock|download|install|run|"
    r"same|in|for|at|about)\b", re.I)


# replies about what Jarvis just did: "Nothing is being played", "That's not what I said", "you didn't play anything"
_FEEDBACK = re.compile(r"\b(you|your|you're|didn'?t|did not|doesn'?t|isn'?t|wasn'?t|not|nothing|wrong|that's|thats|it's|its|still|"
                       r"instead|again|already|never mind|wait)\b", re.I)


def looks_like_request(text):
    """Follow-up listening: does this sound like it's said TO Jarvis (a question/command, or a reply/correction about what
    it just did), not to someone else in the room?"""
    t = re.sub(r"[,.!;:]+", " ", (text or "")).strip()
    t = re.sub(r"\s+", " ", t)
    return bool(t) and (bool(_REQUEST.match(t)) or bool(re.search(r"\bjarvis\b|\?\s*$", t, re.I))
                        or bool(_FEEDBACK.search(" ".join(t.split()[:6]))))


class VoiceID:
    """Is this the owner's voice? WeSpeaker ResNet34 speaker embeddings (sherpa-onnx, ~26 MB).
    Enrolled from the clips recorded in voice training (data/voice). Used for follow-ups: other people talking in the
    room - or Jarvis's own voice from the speakers - don't count as requests."""
    MODEL = MODELS / "wespeaker_resnet34_LM.onnx"

    def __init__(self, enroll_path):
        import sherpa_onnx
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(self.MODEL), num_threads=1)
        self.ex = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        self.enroll_path = Path(enroll_path)
        self.me = np.load(self.enroll_path) if self.enroll_path.exists() else None
        self.vad = None

    def speech_only(self, a):
        """Only the spoken parts (Silero VAD). Without this the match is mostly about room noise/silence, not the voice."""
        import sherpa_onnx
        a = np.asarray(a, dtype=np.float32)
        if self.vad is None:
            cfg = sherpa_onnx.VadModelConfig()
            cfg.silero_vad.model = str(MODELS / "silero_vad.onnx")
            cfg.silero_vad.threshold = 0.4
            cfg.silero_vad.min_silence_duration = 0.1
            cfg.silero_vad.min_speech_duration = 0.1
            cfg.sample_rate = RATE
            self.vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=60)
        self.vad.reset()
        segs = []
        for k in range(0, len(a), 512):
            self.vad.accept_waveform(a[k:k + 512])
            while not self.vad.empty():
                segs.append(np.array(self.vad.front.samples, dtype=np.float32)); self.vad.pop()
        self.vad.flush()
        while not self.vad.empty():
            segs.append(np.array(self.vad.front.samples, dtype=np.float32)); self.vad.pop()
        out = np.concatenate(segs) if segs else a[:0]
        return out if len(out) >= RATE * 0.4 else None

    def embed(self, audio_f32):
        sp = self.speech_only(audio_f32)
        if sp is None:
            return None
        sp = sp * (0.1 / (float(np.sqrt(np.mean(sp ** 2))) + 1e-6))     # same loudness for every clip
        s = self.ex.create_stream()
        s.accept_waveform(RATE, np.clip(sp, -1, 1).astype(np.float32))
        s.input_finished()
        e = np.array(self.ex.compute(s), dtype=np.float32)
        return e / (np.linalg.norm(e) + 1e-9)

    def enroll(self, clips_f32):
        embs = [e for e in (self.embed(c) for c in clips_f32) if e is not None]
        if not embs:
            return False
        me = np.mean(embs, axis=0); me /= np.linalg.norm(me)
        good = [e for e in embs if e @ me >= 0.5] or embs        # drop near-silent / noisy clips
        me = np.mean(good, axis=0)
        self.me = me / np.linalg.norm(me)
        np.save(self.enroll_path, self.me)
        return True

    def adapt(self, audio_f32, score):
        """Learn a little from each real request you start with 'Hey Jarvis' (your mic, your room) - only if it's clearly you."""
        if self.me is None or score is None or score < 0.75 or len(audio_f32) < RATE * 1.0:
            return
        e = self.embed(audio_f32)
        if e is None:
            return
        me = 0.92 * self.me + 0.08 * e
        self.me = me / np.linalg.norm(me)
        try:
            np.save(self.enroll_path, self.me)
        except Exception:
            pass

    def score(self, audio_f32):
        """Cosine similarity to the owner (owner ~0.9, others ~0.4 on the test clips). None if not enrolled."""
        if self.me is None:
            return None
        e = self.embed(audio_f32)
        return None if e is None else float(e @ self.me)


def strip_echo(text, spoken, min_run=4):
    """Remove Jarvis's own words that the mic picked up from the speakers ('I need a little more information ... stop' -> 'stop').
    spoken: recent sentences Jarvis said. Drops any run of >= min_run words that appears in them."""
    if not text or not spoken:
        return text
    norm = lambda s: re.sub(r"[^a-z0-9' ]", " ", s.lower()).split()
    said = " " + " ".join(norm(" ".join(spoken))) + " "
    words, raw = norm(text), text.split()
    if len(words) != len(raw):                 # punctuation-only tokens: fall back to normalised words
        raw = words
    keep, i = [True] * len(words), 0
    while i < len(words):
        j = i
        while j < len(words) and f" {' '.join(words[i:j + 1])} " in said:
            j += 1
        if j - i >= min_run:
            for k in range(i, j):
                keep[k] = False
            i = j
        else:
            i += 1
    return " ".join(w for w, k in zip(raw, keep) if k).strip(" ,.")


class Ears:
    """One microphone stream shared by the wake word, the mic button and follow-up listening.
    Events -> on(kind, **data):  wake | level(v) | partial(text) | final(text, via) | cancel(reason, via) | error(text)

    End of speech = Silero VAD silence + Smart Turn (tone of voice) + the words so far (ends on 'and', 'to', 'the'...?).
    Finished-sounding -> stop after 0.6 s of quiet. Unfinished -> wait up to 2.2 s."""
    CHUNK = 1280                     # 80 ms (what the wake model wants)
    TIMEOUT = {"wake": 7.0, "button": 8.0, "followup": 4.0}    # seconds to START talking
    MAXLEN = {"wake": 20.0, "button": 30.0, "followup": 15.0}  # longest request (background talk can't keep it listening)
    STRONG = 0.5          # wake score that counts at once; weaker ones must also contain the word "Jarvis" (checked silently)
    TENTATIVE_FOR = 2.5   # seconds a weak wake has to show "Jarvis" in the words

    def __init__(self, stt, on, wake=True, verifier_path=None, sensitivity=0.4):
        import sherpa_onnx
        self.stt, self.on, self.sensitivity = stt, on, sensitivity
        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = str(MODELS / "silero_vad.onnx")
        cfg.silero_vad.threshold = 0.5
        cfg.silero_vad.min_silence_duration = 0.15
        cfg.silero_vad.min_speech_duration = 0.1
        cfg.silero_vad.window_size = 512
        cfg.sample_rate = RATE
        cfg.num_threads = 1
        self.vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=30)
        self.turn = SmartTurn()
        self.wake_on = wake
        self.verified = False
        self.wake_model = None
        self.load_wake(verifier_path)
        self.paused = lambda: False      # app: True while thinking / working (no wake word then)
        self.speaking = lambda: False    # app: True while Jarvis talks (only a verified voice can interrupt)
        self.mode, self.ring, self.cap = "idle", [], None
        self.running, self.lock = False, threading.RLock()
        self.last_level = 0.0
        self.clock = 0.0                 # audio time in seconds (works for live mic and for test files)
        self.audio_q = queue.Queue()     # mic reader -> processor (the reader never waits, so no audio is ever dropped)
        self.ev_q = queue.Queue()        # processor -> app events (the app may be slow updating the screen)
        self.sync_events = False         # tests: deliver events immediately
        self.log_path = None
        self.recent_spoken = lambda: []  # app: what Jarvis said in the last ~90 s (to remove its own echo)
        self.expecting_answer = lambda: False   # app: True when Jarvis just asked the user a question
        self.voice_id, self.voice_threshold = None, 0.60
        # echo cancellation (WebRTC AEC3): removes what Jarvis is playing from the mic. app sets far = voice.FAR.take
        self.far, self.aec, self.aec_until = None, None, 0.0
        try:
            from pywebrtc_audio import AudioProcessor   # AEC3 + light noise suppression (tested best at normal volume)
            self.aec = AudioProcessor(sample_rate=RATE, num_channels=1, echo_cancellation=True, noise_suppression=True,
                                      auto_gain_control=False, ns_level=1, stream_delay_ms=120)
        except Exception:
            self.aec = None
        self.barge = None                # while Jarvis talks: listening for YOU saying "stop" (needs the echo canceller)
        self.busy = lambda: False        # app: True while Jarvis is working on a request
        self.amb, self.amb_pre = None, []  # "Jarvis" anywhere: what you're saying right now (your voice only)
        self.lag_logged = 0.0

    # ---------------- setup
    def load_wake(self, verifier_path=None):
        from openwakeword.model import Model
        kw = {}
        if verifier_path and Path(verifier_path).exists():
            kw = dict(custom_verifier_models={"hey_jarvis": str(verifier_path)}, custom_verifier_threshold=0.1)
        self.wake_model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx", **kw)
        self.verified = bool(kw)

    def start(self):
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._process, daemon=True).start()
        threading.Thread(target=self._dispatch, daemon=True).start()

    def _emit(self, kind, **d):
        if self.sync_events:
            return self.on(kind, **d)
        if kind == "level" and self.ev_q.qsize() > 4:
            return                               # screen is behind: skip meter updates, never real events
        self.ev_q.put((kind, d))

    def _dispatch(self):
        while self.running:
            kind, d = self.ev_q.get()
            try:
                self.on(kind, **d)
            except Exception as e:
                print("ears event error:", e)

    def clean(self, chunk, t):
        """Echo cancellation: take out what Jarvis was playing at time t. Runs only while Jarvis talks (+0.5 s echo tail),
        so normal listening is never changed."""
        if self.aec is None or self.far is None:
            return chunk
        far = self.far(len(chunk), t)
        if far is not None:
            self.aec_until = t + 0.5
        elif t > self.aec_until:
            return chunk
        try:
            return self.aec.process(chunk, far if far is not None else np.zeros(len(chunk), np.int16))
        except Exception:
            return chunk

    def _process(self):
        while self.running:
            item = self.audio_q.get()
            chunk, t = item if isinstance(item, tuple) else (item, time.monotonic())
            lag = time.monotonic() - t
            if lag > 1.0 and time.monotonic() - self.lag_logged > 10:
                self.lag_logged = time.monotonic()
                self._log(f"listening is {lag:.1f}s behind (PC busy)")
            try:
                self.feed(self.clean(chunk, t), raw=chunk)
            except Exception as e:
                self._emit("error", text=f"Listening problem: {e}")

    def _log(self, line):
        if self.log_path:
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(time.strftime("%H:%M:%S ") + line + "\n")
            except Exception:
                pass

    def stop(self):
        self.running = False

    # ---------------- control from the app
    def listen(self, kind="button", prefix=None):
        """Start capturing a request now (mic button / hotkey / follow-up)."""
        with self.lock:
            self._begin(kind, prefix)

    def cancel(self, reason="cancelled"):
        with self.lock:
            if self.mode == "capture":
                self._end(None, reason)

    def record_clip(self, timeout=6.0):
        """Voice training: returns the next thing you say (int16 audio), or None if you said nothing."""
        ev, box = threading.Event(), {}
        with self.lock:
            self.vad.reset()
            self.rec = dict(ev=ev, box=box, audio=[], t0=self.clock, first=None, last=None, timeout=timeout)
            self.mode = "record"
        ev.wait(timeout + 15)
        return box.get("audio")

    def _record(self, chunk):
        r, now = self.rec, self.clock
        r["audio"].append(chunk)
        f = chunk.astype(np.float32) / 32768.0
        self.vad.accept_waveform(f)
        speech = self.vad.is_speech_detected()
        while not self.vad.empty():
            self.vad.pop()
        if now - self.last_level > 0.08:
            self.last_level = now
            self._emit("level", v=min(float(np.sqrt(np.mean(f ** 2))) * 12, 1.0))
        if speech:
            r["first"] = r["first"] or now
            r["last"] = now
        done = r["last"] is not None and now - r["last"] > 0.6
        if done or (r["first"] is None and now - r["t0"] > r["timeout"]) or now - r["t0"] > r["timeout"] + 8:
            if r["first"] is not None:
                a = np.concatenate(r["audio"])
                start = max(0, int((r["first"] - 0.5 - r["t0"]) * RATE))
                r["box"]["audio"] = a[start:]
            self.mode = "idle"
            self.vad.reset()
            r["ev"].set()

    # ---------------- the loop
    def _loop(self):
        import sounddevice as sd
        while self.running:
            try:
                with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=self.CHUNK, latency="high") as st:
                    while self.running:
                        data, overflow = st.read(self.CHUNK)
                        if overflow:
                            self._log("mic overflow (audio lost)")
                        self.audio_q.put((data[:, 0].copy(), time.monotonic()))
            except Exception as e:
                self._emit("error", text=f"Microphone problem: {e}")
                time.sleep(3)

    def feed(self, chunk, raw=None):
        """80 ms of int16 audio (from the mic, or from a file in tests). chunk = echo-cancelled; raw = straight from the mic
        (the wake-word model works better on the raw sound - tested - and the voice check stops Jarvis waking itself)."""
        with self.lock:
            self._raw = chunk if raw is None else raw
            self.clock += len(chunk) / RATE
            if self.mode == "record":
                self._record(chunk)
            elif self.mode == "capture":
                self._capture(chunk)
            else:
                self._wake(chunk)

    def _barge(self, chunk):
        """While Jarvis is talking (echo removed): if YOU say 'stop', stop it - no 'Hey Jarvis' needed."""
        f = chunk.astype(np.float32) / 32768.0
        if self.barge is None:
            self.barge = dict(s=self.stt.new(), audio=[], t0=self.clock, done=False)
        b = self.barge
        b["audio"] = (b["audio"] + [f])[-25:]          # last 2 s
        self.stt.feed(b["s"], f)
        if b["done"]:
            return
        words = strip_echo(self.stt.text(b["s"]), self.recent_spoken(), min_run=3).split()
        tail = " ".join(words[-4:])
        if tail and is_stop(tail):
            vs = self.voice_id.score(np.concatenate(b["audio"])) if self.voice_id is not None else None
            self._log(f"barge-in: heard {tail!r} while talking (voice {vs})")
            if vs is None or vs >= self.voice_threshold - 0.1:
                b["done"] = True
                self._emit("cancel", reason="stop", via="barge")
            else:
                self.barge = None                         # not you (or Jarvis's own echo): start fresh

    AMB_MAX = 20.0      # longest stretch of talk it follows

    def _ambient(self, chunk):
        """Always listening for YOU saying 'Jarvis' anywhere ('open Notepad, Jarvis'), and 'stop' while it works.
        Only your voice is transcribed (checked after 1 s of speech) - other people / TV are never written down."""
        f = chunk.astype(np.float32) / 32768.0
        self.vad.accept_waveform(f)
        speech = self.vad.is_speech_detected()
        while not self.vad.empty():
            self.vad.pop()
        a = self.amb
        if a is None:
            self.amb_pre = (self.amb_pre + [f])[-5:]          # 0.4 s before you start
            if speech:
                self.amb = dict(audio=list(self.amb_pre), t0=self.clock, last=self.clock, speech_s=0.0, ok=None, s=None)
            return
        a["audio"].append(f)
        if speech:
            a["last"] = self.clock; a["speech_s"] += len(f) / RATE
        if a["ok"] is None and a["speech_s"] >= 1.0:          # is it you?
            vs = self.voice_id.score(np.concatenate(a["audio"]))
            a["ok"] = vs is not None and vs >= self.voice_threshold
            if a["ok"]:
                a["s"] = self.stt.new()
                for x in a["audio"]:
                    self.stt.feed(a["s"], x)
        elif a["ok"]:
            self.stt.feed(a["s"], f)
        quiet = self.clock - a["last"]
        need = 1.0
        if a["ok"] and quiet >= 0.6 and ends_unfinished(self.stt.text(a["s"])):
            need = 2.0
        if a["ok"] is False:
            if quiet >= 0.6 or self.clock - a["t0"] > self.AMB_MAX:
                self.amb = None; self.amb_pre = []            # someone else: forget it
            return
        if quiet < need and self.clock - a["t0"] <= self.AMB_MAX:
            return
        self.amb, self.amb_pre = None, []
        if a["ok"] is None:                                   # short (< 1 s of speech): 'stop', 'Jarvis', 'Jarvis stop'
            audio = np.concatenate(a["audio"])
            vs = self.voice_id.score(audio)
            # short words are hard to tell apart by voice (another man's "stop" scored 0.72, yours 0.77-0.85):
            # a slightly lower bar, so YOUR stop always works; clearly different voices are still ignored
            if vs is not None and vs < self.voice_threshold - 0.05:
                return
            s = self.stt.new()
            for x in a["audio"]:
                self.stt.feed(s, x)
            text = self.stt.finish(s)
        else:
            text = self.stt.finish(a["s"])
        if self.clock - a["t0"] > self.AMB_MAX:
            return                                            # a long talk (with someone else) - not a request
        text = fix_words(strip_echo((text or "").strip(), self.recent_spoken()))
        found, cmd = find_jarvis(text)
        if self.busy() and (is_stop(text) or (found and is_stop(cmd))):
            self._log(f"[ambient] heard {text!r} while working -> stop")
            return self._emit("cancel", reason="stop", via="ambient")
        if not found:
            if text:
                self._log(f"[ambient] {text!r} - no 'Jarvis', not for me")
                if looks_like_request(text):          # '<request> ... (pause) ... Hey Jarvis' -> this was for Jarvis
                    self.last_owner = (self.clock, text)
            return
        self._log(f"[ambient] heard {text!r} -> request {cmd!r}")
        self._emit("wake", interrupt=False)
        if not cmd:
            cmd = self._take_last_owner(a["t0"])
            if cmd:
                return self._emit("final", text=cmd, via="ambient")
        if not cmd:                                           # just "Jarvis" -> listen for the request
            self._begin("button", None)
            return
        self._emit("final", text=cmd, via="ambient")

    def _wake(self, chunk):
        if self.aec is not None and self.far is not None and self.speaking() and self.voice_id is not None:
            self._barge(chunk)
        elif self.barge is not None:
            self.barge = None
        if self.voice_id is not None and self.wake_on and not self.speaking() and not self.paused():
            self._ambient(chunk)
            if self.mode != "idle":
                return
        elif self.amb is not None:
            self.amb = None
        self.ring.append(chunk)
        self.ring = self.ring[-25:]                  # last 2 s
        speaking = self.speaking()
        if not self.wake_on or self.paused() or (speaking and not self.verified) or self.clock < getattr(self, "wake_quiet_until", 0):
            self.wake_model.reset()
            return
        score = self.wake_model.predict(getattr(self, "_raw", chunk))["hey_jarvis"]
        need = self.sensitivity if not speaking or self.voice_id is not None else max(0.7, self.sensitivity)
        if score < need:
            return
        self.wake_model.reset()
        prefix = np.concatenate(self.ring)[-int(RATE * 1.8):]
        a = self.amb
        if a is not None and a.get("ok") is not False and a["audio"]:
            # you were already talking ('What's the weather today, hey Jarvis'): keep the whole sentence, not just the name
            said = (np.concatenate(a["audio"]) * 32768).astype(np.int16)[-int(RATE * 12):]
            if len(said) > len(prefix):
                prefix = said
        self.ring = []
        vs = None
        if self.voice_id is not None:               # is it YOUR voice saying it? (TV, other people, Jarvis's own speech -> no)
            try:
                vs = self.voice_id.score(prefix.astype(np.float32) / 32768.0)
            except Exception:
                vs = None
        if vs is not None and vs < self.voice_threshold:
            self._log(f"wake ignored (score {score:.2f}, not your voice {vs:.2f})")
            return
        # clear wake-up AND (your voice confirmed, or no voice check set up) -> answer at once; otherwise wait silently
        strong = score >= max(self.STRONG, need) and (vs is not None or self.voice_id is None)
        self._log(f"wake (score {score:.2f}{'' if strong else ', checking for Jarvis'}{'' if vs is None else f', voice {vs:.2f}'})")
        if strong:
            self._emit("wake", interrupt=speaking)
        self._begin("wake", prefix)
        self.cap["tentative"] = not strong
        self.cap["interrupt"] = speaking

    def _begin(self, kind, prefix):
        self.amb, self.amb_pre = None, []
        self.vad.reset()
        self.cap = dict(kind=kind, s=self.stt.new(), audio=[], t0=self.clock, last_speech=None, heard=False,
                        need=None, partial="", wake_at=self.clock, restarted=False)
        self.mode = "capture"
        if prefix is not None and len(prefix):
            self._capture(prefix.astype(np.int16), is_prefix=True)

    def _capture(self, chunk, is_prefix=False):
        c, now = self.cap, self.clock
        f = chunk.astype(np.float32) / 32768.0
        c["audio"].append(f)
        self.stt.feed(c["s"], f)
        self.vad.accept_waveform(f)
        speech = self.vad.is_speech_detected()
        while not self.vad.empty():
            self.vad.pop()
        if speech:
            c["speech_s"] = c.get("speech_s", 0.0) + len(f) / RATE
        text = self.stt.text(c["s"])
        if c.get("tentative"):                      # weak wake: stay invisible until the words contain "Jarvis"
            if split_wake(text)[0] or find_jarvis(text)[0]:
                vs = self._voice(c)
                if vs is not None and vs < self.voice_threshold:
                    self._log(f"   'Jarvis' heard, but not your voice ({vs:.2f})")
                    return self._end(None, "false wake")
                c["tentative"] = False
                self._log("   wake confirmed: heard 'Jarvis'" + ("" if vs is None else f" (voice {vs:.2f})"))
                self._emit("wake", interrupt=c.get("interrupt", False))
            elif not is_prefix and now - c["wake_at"] > self.TENTATIVE_FOR:
                return self._end(None, "false wake")
            else:
                return
        if now - self.last_level > 0.08:
            self.last_level = now
            self._emit("level", v=min(float(np.sqrt(np.mean(f ** 2))) * 12, 1.0))
        if text != c["partial"]:
            c["partial"] = text
            self._emit("partial", text=wake_command(text) if c["kind"] == "wake" else text)
        if is_prefix:
            return
        # follow-up: as soon as there's 1.5 s of speech, check it's YOUR voice - someone else talking ends it at once
        if c["kind"] == "followup" and self.voice_id is not None and not c.get("vchecked") and c.get("speech_s", 0) >= 1.5:
            c["vchecked"] = True
            try:
                vs = self.voice_id.score(np.concatenate(c["audio"]))
            except Exception:
                vs = None
            if vs is not None and vs < self.voice_threshold:
                self._log(f"   follow-up stopped early: not your voice ({vs:.2f})")
                return self._end(None, "not your voice")
        if c["kind"] == "wake":
            cmd = wake_command(text)
            # speech after the wake phrase (and after our chime) = you're talking, even before words show up
            content = bool(cmd.strip()) or (speech and now - c["wake_at"] > 0.6)
            # false alarm? several words heard and none of them is 'Jarvis' (only if the voice isn't verified)
            if not split_wake(text)[0] and not self.verified and len(text.split()) >= 4:
                return self._end(None, "false wake")
        else:
            cmd = text
            content = bool(text.strip()) or speech
        if speech:
            c["last_speech"], c["need"] = now, None
            if content:
                c["heard"] = True
        elif content and not c["heard"]:
            c["heard"], c["last_speech"] = True, c["last_speech"] or now
        if not c["heard"]:
            if now - c["t0"] > self.TIMEOUT[c["kind"]]:
                return self._end(None, "nothing heard")
            return
        quiet = now - (c["last_speech"] or now)
        if quiet >= 0.5 and c["need"] is None:      # a pause: finished, or thinking mid-sentence? (tone of voice)
            c["turn_p"] = self.turn.done_probability(np.concatenate(c["audio"]))
            c["need"] = 1.0 if c["turn_p"] >= 0.5 else 2.2
        # live words arrive ~0.5 s late, so keep re-checking them during the pause ('...mode and' -> wait)
        need = 2.2 if (c["need"] is not None and ends_unfinished(cmd)) else c["need"]
        if (need is not None and quiet >= need) or now - c["t0"] > self.MAXLEN[c["kind"]]:
            self._end(self.stt.finish(c["s"]), None)

    def _take_last_owner(self, since):
        """Your request from just before (within 2 s) that didn't contain the name - used when the name came after a pause."""
        lo = getattr(self, "last_owner", None)
        self.last_owner = None
        if lo and 0 <= since - lo[0] <= 2.0:
            self._log(f"   using what you said just before: {lo[1]!r}")
            return lo[1]
        return ""

    def _voice(self, c):
        if self.voice_id is None or not c or not c["audio"]:
            return None
        try:
            return self.voice_id.score(np.concatenate(c["audio"]))
        except Exception:
            return None

    def _end(self, text, reason):
        c, self.cap, self.mode = self.cap, None, "idle"
        self.wake_quiet_until = self.clock + 1.5        # no instant re-wake from the tail of what was just said
        self.vad.reset()
        self.wake_model.reset()
        kind = c["kind"] if c else None
        dur = self.clock - c["t0"] if c else 0
        self._log(f"[{kind}] {dur:.1f}s heard={text!r} reason={reason} turn={c.get('turn_p') if c else None}")
        if text is None:
            if c and c.get("tentative") and self.voice_id is not None and self.wake_on:
                # a weak trigger that didn't hear "Jarvis" yet: keep following what you say (the name may come later)
                self.amb = dict(audio=list(c["audio"]), t0=c["t0"], last=self.clock, speech_s=c.get("speech_s", 0.0), ok=None, s=None)
            return self._emit("cancel", reason=reason, via=kind, silent=bool(c and c.get("tentative")))
        if kind == "wake" and c.get("tentative"):
            return self._emit("cancel", reason="false wake", via=kind, silent=True)
        if kind == "wake":                           # the whole request: is it really you?
            vs = self._voice(c)
            if vs is not None and vs < self.voice_threshold:
                self._log(f"   ignored: not your voice ({vs:.2f})")
                return self._emit("cancel", reason="not your voice", via=kind, text=text)
        if kind == "wake":
            if not split_wake(text)[0] and not self.verified:
                return self._emit("cancel", reason="false wake", via=kind)
            found, cmd = find_jarvis(text)                 # 'open Notepad, Jarvis' works too, not only 'Hey Jarvis, ...'
            text = cmd if found else wake_command(text)
            if not text.strip() and not c.get("restarted"):
                said = self._take_last_owner(c["wake_at"])    # '<request>... Hey Jarvis' with a pause in between
                if said:
                    return self._emit("final", text=said, via="wake")
                # only "Hey Jarvis" -> keep listening for the request (no new wake word needed)
                self._begin("button", None)
                self.cap["restarted"] = True
                return self._emit("partial", text="")
        text = fix_words(strip_echo(text.strip(), self.recent_spoken()))   # drop Jarvis's own words heard from the speakers
        bare = re.sub(r"[^a-z ]", "", text.lower()).strip()
        if not bare:
            return self._emit("cancel", reason="nothing heard", via=kind)
        if bare in STOP_WORDS or is_stop(text):
            return self._emit("cancel", reason="stop", via=kind)
        if kind in ("wake", "button") and self.voice_id is not None and c and c["audio"] and self.verified:
            try:
                a = np.concatenate(c["audio"]); self.voice_id.adapt(a, self.voice_id.score(a))
            except Exception:
                pass
        if kind == "followup":
            # follow-ups have no wake word: only take it if it's YOUR voice and it sounds like it's said to Jarvis
            if len(bare.split()) < 2 and not self.expecting_answer():
                return self._emit("cancel", reason="too short", via=kind)
            if self.voice_id is not None and c and c["audio"]:
                try:
                    sc = self.voice_id.score(np.concatenate(c["audio"]))
                except Exception:
                    sc = None
                self._log(f"   voice match {sc}")
                if sc is not None and sc < self.voice_threshold:
                    return self._emit("cancel", reason="not your voice", via=kind, text=text)
            if not self.expecting_answer() and not looks_like_request(text):
                self._log("   ignored: doesn't sound like a request to Jarvis")
                return self._emit("cancel", reason="not for me", via=kind, text=text)
        self._emit("final", text=text, via=kind)


# ================================================================ your-voice wake filter (openWakeWord custom verifier)
def wake_scores(clips):
    """Highest 'hey jarvis' score in each clip (int16 arrays) -> how well the base model hears YOUR voice."""
    from openwakeword.model import Model
    m = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    out = []
    for a in clips:
        m.reset()
        a = np.concatenate([np.zeros(RATE, np.int16), a.astype(np.int16), np.zeros(RATE // 2, np.int16)])
        out.append(max(m.predict(a[i:i + 1280])["hey_jarvis"] for i in range(0, len(a) - 1280, 1280)))
    return out


def train_voice_filter(pos_dir, neg_dir, out_path):
    """Learns your voice saying 'Hey Jarvis' (a few seconds of CPU, no GPU). Returns a suggested sensitivity."""
    import glob, wave
    import openwakeword
    pos = sorted(glob.glob(str(Path(pos_dir) / "*.wav")))
    neg = sorted(glob.glob(str(Path(neg_dir) / "*.wav")))
    clips = []
    for f in pos:
        with wave.open(f) as w:
            clips.append(np.frombuffer(w.readframes(w.getnframes()), np.int16))
    scores = wake_scores(clips)
    openwakeword.train_custom_verifier(positive_reference_clips=pos, negative_reference_clips=neg,
                                       output_path=str(out_path), model_name=str(Path(openwakeword.__file__).parent / "resources" / "models" / "hey_jarvis_v0.1.onnx"),
                                       inference_framework="onnx")
    # sensitivity: low enough to catch ~95% of your own 'Hey Jarvis' (the filter blocks other voices/words)
    sens = float(np.clip(0.8 * np.percentile(scores, 5), 0.15, 0.5))
    return {"scores": [round(float(s), 2) for s in scores], "sensitivity": round(sens, 2)}
