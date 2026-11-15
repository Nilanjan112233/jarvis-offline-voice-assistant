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
WHISPER_SERVER = VOICE_DIR / "whisper" / "Release" / "whisper-server.exe"
WHISPER_MODEL = VOICE_DIR / "whisper" / "ggml-base.en-q8_0.bin"
WHISPER_PORT = 8099
RATE = 16000


# ================================================================ speaking
class Speaker:
    def __init__(self):
        self.q = queue.Queue()
        self.stop_flag = threading.Event()
        self.voice = None
        self.speed = 1.0            # 1.0 normal, 1.3 faster, 0.8 slower
        try:
            from piper import PiperVoice
            self.voice = PiperVoice.load(str(PIPER_MODEL))
            for _ in self.voice.synthesize("Hi."):   # warm-up: first call is slow
                pass
        except Exception as e:
            print("Piper voice not available, using Windows voice:", e)
        self.audio_q = queue.Queue()
        self._gen = 0                     # bumped by stop(): audio made before a stop is never played
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()

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
        sd.stop()

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
            sd.play((tone * 0.22 * 32767).astype(np.int16), sr)
        except Exception:
            pass

    _speaking = _playing = False

    def _loop(self):
        """Makes the audio for each sentence while the previous one is still playing (no gaps between sentences)."""
        while True:
            gen, text = self.q.get()
            if gen != self._gen:
                continue                      # queued before a stop
            self._speaking = True
            try:
                for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
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
                    sd.play(audio, rate)
                    while sd.get_stream().active:
                        if gen != self._gen:
                            sd.stop(); break
                        time.sleep(0.02)
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
class WakeWord:
    """Listens all the time for "Hey Jarvis" (openWakeWord, ~0.4% CPU). Nothing is recorded or sent anywhere.
    Two-stage check to avoid false wake-ups ("Hey, Java..."): the wake model must fire on 2 frames in a row,
    then the last ~2 s of audio are transcribed and must contain "Jarvis"."""
    CHUNK = 1280   # 80 ms at 16 kHz

    def __init__(self, on_wake, listener, paused=lambda: False, threshold=0.5):
        from openwakeword.model import Model
        self.model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        self.on_wake, self.listener, self.paused, self.threshold = on_wake, listener, paused, threshold
        self.ring, self.hits, self.running, self.cooldown = [], 0, False, 0.0

    def start(self):
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.running = False

    def _loop(self):
        while self.running:
            try:
                with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=self.CHUNK) as st:
                    while self.running:
                        data, _ = st.read(self.CHUNK)
                        self.process(data[:, 0].copy())
            except Exception as e:   # mic unplugged etc. -> retry
                print("wake word mic error:", e)
                time.sleep(3)

    def process(self, chunk):
        """Feed 80 ms of int16 audio. On a wake, calls on_wake(buffered_audio) and returns True."""
        self.ring.append(chunk)
        self.ring = self.ring[-30:]   # keep ~2.4 s
        if self.paused() or time.time() < self.cooldown:
            self.hits = 0
            self.model.reset()
            return False
        score = self.model.predict(chunk)["hey_jarvis"]
        self.hits = self.hits + 1 if score >= self.threshold else 0
        if self.hits < 2:
            return False
        self.hits, self.cooldown = 0, time.time() + 2.0
        self.model.reset()
        buffered = np.concatenate(self.ring)
        self.ring = []
        self.on_wake(buffered)   # the app keeps recording from here and checks the transcript says "Jarvis"
        return True

    @staticmethod
    def split_command(text):
        """'hey jarvis what's the weather' -> (True, "what's the weather"); no 'Jarvis' -> (False, '')."""
        m = re.search(r"\b(jarvis|jarvas|jervis|javis)\b[\s,.!?]*(.*)", text or "", re.I)
        return (True, m.group(2).strip()) if m else (False, "")

    def verify(self, audio):
        """Second opinion from the speech recognizer. Returns words after 'Jarvis' ('' if none), or None if not really 'Jarvis'."""
        if not self.listener:
            return ""
        try:
            text = self.listener.transcribe(audio).lower()
        except Exception:
            return ""   # recognizer busy/unavailable -> trust the wake model
        m = re.search(r"\b(jarvis|jarvas|jervis|javis)\b[\s,.!?]*(.*)", text)
        return m.group(2).strip() if m else None
