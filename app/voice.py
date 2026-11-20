"""Voice for the assistant - fully offline.

Speaker:  Piper neural voice (en_US-lessac-medium). Falls back to the built-in Windows voice.
Listener: microphone -> stops when you go quiet -> whisper.cpp (base.en) turns speech into text.
"""
import io, json, queue, re, subprocess, threading, time, urllib.request, uuid, wave
from pathlib import Path

import numpy as np
import sounddevice as sd

VOICE_DIR = Path(__file__).resolve().parents[1] / "voice"
PIPER_MODEL = VOICE_DIR / "piper" / "en_US-lessac-medium.onnx"
# voices you can pick in Settings (all Piper: fast on this CPU - Kokoro/Kitten were tried and were too slow / skipped names)
VOICES = {
    "en_US-lessac-medium": "Lessac · US, clear",
    "en_GB-alan-medium": "Alan · British male",
    "en_GB-northern_english_male-medium": "Northern · British male",
    "en_US-ryan-high": "Ryan · US male, richer (a bit slower)",
}
DEFAULT_VOICE = "en_US-lessac-medium"
WHISPER_SERVER = VOICE_DIR / "whisper" / "Release" / "whisper-server.exe"
WHISPER_MODEL = VOICE_DIR / "whisper" / "ggml-base.en-q8_0.bin"
WHISPER_PORT = 8099
RATE = 16000


# ================================================================ what Jarvis is playing (for the echo canceller)
class FarEnd:
    """Keeps the sound Jarvis is playing right now (16 kHz), so the mic side can remove it again (echo cancellation)."""
    def __init__(self):
        self.lock = threading.Lock()
        self.audio, self.t0 = None, 0.0

    def play(self, audio, rate):
        a = np.asarray(audio, dtype=np.float32)
        if rate != RATE and len(a):
            a = np.interp(np.arange(0, len(a), rate / RATE), np.arange(len(a)), a)
        with self.lock:
            self.audio, self.t0 = a.astype(np.int16), time.monotonic()

    def take(self, n, t_end):
        """The n samples that were playing up to time t_end (None if nothing was playing)."""
        with self.lock:
            a, t0 = self.audio, self.t0
        if a is None:
            return None
        end = int((t_end - t0) * RATE); start = end - n
        if end <= 0 or start >= len(a):
            return None
        out = np.zeros(n, dtype=np.int16)
        lo, hi = max(start, 0), min(end, len(a))
        out[lo - start:hi - start] = a[lo:hi]
        return out


FAR = FarEnd()
# night test 18 Nov: sounddevice's play()/stop()/get_stream() share ONE global stream and are not thread-safe.
# stop() from the app thread while the play thread was inside play/active crashed the whole app natively
# (access violation, 3 of 6 self-test runs). Rule: every sd.* call holds SD_LOCK; only the play thread stops speech.
SD_LOCK = threading.Lock()


# ================================================================ speaking
class Speaker:
    def __init__(self, voice_name=DEFAULT_VOICE):
        self.q = queue.Queue()
        self.stop_flag = threading.Event()
        self.voice = None
        self.voice_name = None
        self.speed = 1.0            # 1.0 normal, 1.3 faster, 0.8 slower
        if not self.set_voice(voice_name) and voice_name != DEFAULT_VOICE:
            self.set_voice(DEFAULT_VOICE)
        if not self.voice:
            print("Piper voice not available, using Windows voice")
        self.audio_q = queue.Queue()
        self._gen = 0                     # bumped by stop(): audio made before a stop is never played
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()

    def set_voice(self, name):
        """Switch to another Piper voice (loads in ~1 s; the old one keeps talking until then)."""
        path = VOICE_DIR / "piper" / f"{name}.onnx"
        if name not in VOICES or not path.exists():
            return False
        try:
            from piper import PiperVoice
            v = PiperVoice.load(str(path))
            for _ in v.synthesize("Hi."):     # warm-up: first call is slow
                pass
            self.voice, self.voice_name = v, name
            return True
        except Exception as e:
            print("voice load failed:", e)
            return False

    def say(self, text):
        text = re.sub(r"[*#`_>|]", "", text or "").strip()
        if text:
            self.q.put((self._gen, text))

    def stop(self):
        """Interrupt current speech and drop anything queued."""
        self._gen += 1
        for q in (self.q, self.audio_q):
            try:
                while True:
                    q.get_nowait()
            except queue.Empty:
                pass
        # no sd.stop() here (other thread): the play loop / reminder sees the new _gen within 20 ms and stops itself

    def busy(self):
        return not self.q.empty() or not self.audio_q.empty() or self._speaking or self._playing

    def chime(self, kind="wake"):
        """Short two-note sound: 'wake' (rising) = I'm listening, 'off' (falling) = stopped listening."""
        if self.busy():
            return
        sr = 22050
        t = np.arange(int(sr * 0.07)) / sr
        notes = {"wake": (880, 1320), "off": (990, 660)}[kind]
        tone = np.concatenate([np.sin(2 * np.pi * f * t) * np.hanning(len(t)) for f in notes])
        try:
            with SD_LOCK:
                FAR.play((tone * 0.22 * 32767).astype(np.int16), sr)
                sd.play((tone * 0.22 * 32767).astype(np.int16), sr)
        except Exception:
            pass

    REMINDER_SOUNDS = {"chime": "Chime", "melody": "Melody", "bell": "Bell", "beep": "Beep", "none": "Silent"}

    @staticmethod
    def reminder_tone(kind):
        """Reminder sounds, made on the fly (nothing downloaded)."""
        sr = 22050

        def note(f, dur, vol=0.25, decay=4.0):
            t = np.arange(int(sr * dur)) / sr
            w = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t)      # a touch of overtone = softer, warmer
            return w * np.exp(-decay * t) * np.minimum(1, t * 200) * vol

        if kind == "melody":                     # a short rising tune, twice
            tune = [(523, .18), (659, .18), (784, .18), (1047, .36), (0, .15)] * 2
        elif kind == "bell":
            tune = [(880, .9), (0, .1), (880, .9)]
        elif kind == "beep":
            tune = [(1000, .12), (0, .08)] * 3
        else:                                    # chime
            tune = [(784, .25), (1175, .5)]
        parts = [note(f, d, decay=1.5 if kind == "bell" else 4.0) if f else np.zeros(int(sr * d)) for f, d in tune]
        return (np.concatenate(parts) * 32767 * 0.8).astype(np.int16), sr

    def play_reminder(self, kind="chime"):
        if kind == "none":
            return
        try:
            audio, sr = self.reminder_tone(kind)
            self._play_until_done(audio, sr, self._gen)
        except Exception:
            pass

    def _play_until_done(self, audio, rate, gen):
        """Play and wait; stops early when stop() was called (new _gen). All sounddevice calls under SD_LOCK."""
        with SD_LOCK:
            FAR.play(audio, rate)
            sd.play(audio, rate)
        while True:
            with SD_LOCK:
                if gen != self._gen:
                    sd.stop()
                    return
                if not sd.get_stream().active:
                    return
            time.sleep(0.02)

    _speaking = _playing = False

    def _loop(self):
        """Makes the audio for each sentence while the previous one is still playing (no gaps between sentences)."""
        while True:
            gen, text = self.q.get()
            if gen != self._gen:
                continue                      # queued before a stop
            self._speaking = True
            try:
                parts = [x for x in re.split(r"(?<=[.!?])\s+|\n+", text) if x.strip()]
                # a long first sentence: speak its first clause right away (Piper makes a whole piece before playing)
                if parts and len(parts[0].split()) > 12:
                    m = re.search(r"^(.{25,}?[,;:])\s+(.{15,})$", parts[0])
                    if m:
                        parts[:1] = [m.group(1), m.group(2)]
                for sentence in parts:
                    if gen != self._gen:
                        break
                    if sentence.strip():
                        audio = self._synth(sentence.strip())
                        if gen == self._gen:
                            self.audio_q.put((gen, audio))
            except Exception as e:
                print("speech error:", e)
            self._speaking = False

    def _play_loop(self):
        while True:
            gen, (audio, rate) = self.audio_q.get()
            if gen != self._gen:
                continue
            self._playing = True
            try:
                if rate == "win":
                    subprocess.run(["powershell", "-NoProfile", "-Command",
                                    "Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak($input)"],
                                   input=audio, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    self._play_until_done(audio, rate, gen)
            except Exception as e:
                print("speech error:", e)
            self._playing = False

    def _synth(self, sentence):
        if not self.voice:
            return (sentence, "win")
        chunks, rate = [], 22050
        try:
            from piper.config import SynthesisConfig
            cfg = SynthesisConfig(length_scale=1.0 / max(0.5, min(2.0, self.speed)))
        except Exception:
            cfg = None
        for ch in (self.voice.synthesize(sentence, syn_config=cfg) if cfg else self.voice.synthesize(sentence)):
            chunks.append(ch.audio_int16_array)
            rate = ch.sample_rate
        return (np.concatenate(chunks) if chunks else np.zeros(1, np.int16), rate)


# ================================================================ listening
class Listener:
    def __init__(self, log_path):
        self.proc = None
        self.log_path = log_path

    def start(self):
        log = open(self.log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen([str(WHISPER_SERVER), "-m", str(WHISPER_MODEL), "--host", "127.0.0.1",
                                      "--port", str(WHISPER_PORT), "-t", "4", "-l", "en"],
                                     stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        for _ in range(100):
            if self.proc.poll() is not None:
                raise RuntimeError("speech recognizer stopped")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{WHISPER_PORT}/", timeout=1)
                return
            except Exception:
                time.sleep(0.3)
        raise TimeoutError("speech recognizer did not start")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def record(self, on_level=None, max_seconds=20, silence_after=1.0, prefix=None):
        """Record until you stop talking. Returns int16 audio or None if nothing was said.
        prefix: audio already captured (e.g. the wake word buffer) - speech is treated as already started."""
        frames, block = [], int(RATE * 0.05)
        noise, speech_started, quiet_for, t0 = None, False, 0.0, time.time()
        if prefix is not None and len(prefix):
            frames.append(prefix.reshape(-1, 1))
            rms = [float(np.sqrt(np.mean(prefix[i:i + block].astype(np.float32) ** 2))) for i in range(0, len(prefix) - block, block)]
            noise = float(np.percentile(rms, 15)) if rms else 200.0
            speech_started, t0 = True, time.time() - 0.5   # skip calibration
        with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=block) as stream:
            while time.time() - t0 < max_seconds:
                data, _ = stream.read(block)
                frames.append(data.copy())
                level = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
                if noise is None or (not speech_started and time.time() - t0 < 0.4):
                    noise = level if noise is None else 0.8 * noise + 0.2 * level
                    continue
                if on_level:
                    on_level(level)
                loud = level > max(noise * 3.0, 300)
                if loud:
                    speech_started, quiet_for = True, 0.0
                elif speech_started:
                    quiet_for += 0.05
                    if quiet_for >= silence_after:
                        break
                elif time.time() - t0 > 8:
                    return None  # nobody spoke
        return np.concatenate(frames) if speech_started else None

    def transcribe(self, audio):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(audio.tobytes())
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n"
                f"Content-Type: audio/wav\r\n\r\n").encode() + buf.getvalue() + \
               (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson"
                f"\r\n--{boundary}--\r\n").encode()
        req = urllib.request.Request(f"http://127.0.0.1:{WHISPER_PORT}/inference", data=body,
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            text = json.loads(r.read()).get("text", "")
        text = re.sub(r"\[.*?\]|\(.*?\)", "", text).strip()   # drop [BLANK_AUDIO], (music) etc.
        return text


# ================================================================ wake word ("Hey Jarvis"), offline
