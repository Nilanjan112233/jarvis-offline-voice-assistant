# Jarvis: an offline voice assistant for Windows, running a 2B LLM on CPU

A personal assistant that runs **entirely on your own PC**: wake word, streaming speech-to-text, a local LLM, text-to-speech, and 53 tools that act on Windows. No cloud and no GPU. It is built around a **layered "System 1 / System 2" design**, so a small 2-billion-parameter model behaves reliably:

```
 "Hey Jarvis, ..." ─► wake word (openWakeWord + name check + optional owner-voice check)
                                   │
                                   ▼
        streaming speech-to-text (NVIDIA Nemotron 0.6B via sherpa-onnx, live captions)
        end of turn = Silero VAD silence + Smart Turn v3.2 + "did the sentence end on 'and'?"
                                   │
        ┌──────────────────────────┘
        ▼
  System 1 router (bge-small embeddings + rules, 6–50 ms)
        ├── simple / exact things ─► run directly, no LLM (volume, timers, maths, PC facts, open apps…)
        └── everything else ─► LLM (Gemma 4 E2B, English-only trim, llama.cpp + MTP draft, streamed)
                                   │  JSON tool calls
                                   ▼
                        tools (53) ─► permission policy (in code) ─► Allow/Deny card for risky actions
                                   │
                        code guards: no "I did X" without a real tool call, numbers must come
                        from tool output, every part of a multi-part request must be covered …
                                   │
                                   ▼
          answer ─► panel cards + spoken reply (Piper TTS, sentence by sentence while it's written)
```

**The model:** [Gemma 4 E2B trimmed to an English-only vocabulary](https://github.com/Nilanjan112233/llm-vocab-trimmer). That is my own no-training method: the file is 41% smaller and the assistant is 33% faster per task, with the same accuracy.

## Results

I tested it on my own set of **138 real requests** across chat, PC info, actions, reminders, memory, writing, research, safety, skills and hallucination traps. Each change was kept only if the score went up:

| Run | What changed | Pass | Unsafe actions | Median time |
|---|---|---|---|---|
| baseline | plain LLM + tools | 63% | 0 | 4.9 s |
| 3 | System-1 router + code guards + persona | 81% | 0 | 7.9 s |
| 4 | multi-part / fresh-fact rules, more tools | 91% | 0 | 10.3 s |
| 5 | exact maths, full cached tool list (prompt cache) | 91% | 0 | 6.1 s |
| **6** | direct PC facts, "promise" check | **94%** | **0** | **5.5 s** |
| 7 | name-the-items and missing-file checks | 93% | 0 | 5.6 s |

More than 20 kinds of request are answered instantly by the router, with no LLM, so they can't be hallucinated. The benchmark itself isn't in this repo yet, because it uses files from my own machine.

### Choosing the brain

I compared smaller tool-calling models with the same 25 voice-assistant tests (all actions stubbed), on the same PC:

| Model | Passed | Median / mean per request | Writing speed |
|---|---|---|---|
| **Gemma 4 E2B en50v Q4_0 + MTP (used)** | **24/25** | **4.8 s / 6.1 s** | ~15 tok/s |
| xLAM-2-1B-fc-r Q4_0 | 21/25 | 5.0 s / 15.0 s | ~12 tok/s |
| LFM3-1.7B Q4_0 | 18/25 | ~20 s / ~38 s | ~17 tok/s |

LFM3 writes faster but reads prompts slower, and its hybrid layers reuse the prompt cache badly, so it re-reads 1,000+ tokens on every step of a tool loop. A smaller model wasn't faster in practice. The speed comes from MTP, the prompt cache and more direct routes.

### Speech-to-text

| Engine | Word error rate (my test clips) | Delay after you stop talking |
|---|---|---|
| **Nemotron Speech Streaming EN 0.6B int8 (used)** | **4.5%** | **0.4 s** |
| Moonshine base | 6.1% | |
| Whisper base.en | 8.0% | 1.0 s |

## Features

- **Voice, fully offline.** One always-open microphone for the wake word, the button and follow-ups, with live captions while you talk.
  - **Wake word:** "Hey Jarvis" (openWakeWord) plus a fuzzy name check, so "Jervis" is accepted and "Hey, Java is…" is rejected.
  - **Owner voice (optional):** "Teach Jarvis my voice" trains a filter, and a speaker-ID check (WeSpeaker ResNet34) means other people and the TV can't wake it. Your voice scores 0.89–0.98, other voices 0.36–0.42.
  - **End of turn:** a pause after "and…" or "to…" doesn't cut you off. The end is decided by silence (Silero VAD), tone of voice (Smart Turn v3.2) and whether the sentence sounds finished.
  - **Stop and queue:** "stop", "cancel" or "never mind" stops it mid-thought in 0.1–0.6 s. Requests made while it's busy wait in a queue. Its own voice is filtered out of what the mic hears.
- **Streaming replies.** Words appear about 1 s after a request. Finished sentences are spoken while the rest is still being written, but only after they pass the honesty checks.
- **UI:** a floating particle-sphere orb in a native see-through window. It ripples with your voice, spins while thinking, shows progress while working, and pulses gold when it needs your OK. There are three themes (Emerald, Crimson, Glass) and a slide-out panel with a live timeline of each step, result cards (weather, PC stats, headlines, files, currency, world clock), in-panel Allow/Deny cards, history and settings. Global hotkey: Ctrl+Alt+J.
- **53 tools:** web search, weather, news, currency, world time, PC/network info, installed apps, files (find, read, largest, sizes, write, move, delete to the Recycle Bin), sandboxed Python/PowerShell, exact volume, media control of the real Chrome/Spotify session, YouTube playback (also in incognito), Chrome profiles, settings, screenshot, lock, clipboard, reminders/timers, lists, notes, memory, learned "skills", and changing its own settings ("talk faster", "change your colour").
- **Reading the screen:** offline OCR with Windows' built-in engine. It captures a window even when it's covered, and it's read-only: nothing is saved or sent.
- **Desktop control without vision:** it uses apps through the Windows accessibility tree ([agent-desktop](https://github.com/vercel-labs/agent-desktop)). The LLM writes plain steps ("click File", "type X in Text editor"), and a System-1 executor matches each one to an on-screen element by fuzzy name matching, with no LLM call per step. It only does this when you asked it to operate an app.
- **Memory:** facts like "my name is…", "I live in…" and "don't use X" are saved automatically by code, not by the model.

## Safety, enforced in code rather than in the prompt

- `app/policy.py`: read-only actions run immediately. Anything that changes something asks first. Destructive, security-changing or downloading commands and system folders are blocked. The model's text is never passed straight to a shell.
- Desktop control stays locked to the window it started on, asks before risky buttons (send, delete, buy, pay, install, sign out…), and never types into password fields.
- Text from websites, files, the screen and command output is treated as data, never as instructions.
- Settings changes are dropped unless you actually asked for one. Claims like "I have stopped Notepad" are checked against what the tools really did.
- `tests/check_policy.py`: 28 permission-rule cases, all passing.

## Setup (Windows 10/11, Python 3.13)

```powershell
git clone https://github.com/Nilanjan112233/jarvis-offline-voice-assistant
cd jarvis-offline-voice-assistant
powershell -ExecutionPolicy Bypass -File setup.ps1   # ~2.5 GB of engine/models/voice, SHA-256 checked
.\run_jarvis.bat
```

Hardware: any 4-core x86-64 CPU and about 5 GB of free RAM (speech models ~1.2 GB, LLM ~2.5 GB). Startup takes about 40 s while the model reads its tool list. After that it's cached.

UI demo without the backend: open `ui/preview.html` (orb + panel) or `ui/panel.html` in a browser. It switches to a demo mode with sample data.

Self-test (drives the real window end to end, read-only): `set JARVIS_SELFTEST=1` and then `run_jarvis.bat`. The results go to `data/selftest.log`.

## Layout

| Path | What |
|---|---|
| `app/main.py` | desktop app: orb + panel windows, events, hotkey, reminders, microphone threads |
| `app/agent.py` | the brain: persona, streamed LLM loop, code guards, auto-memory |
| `app/router.py` | System-1 router: embeddings + rules, direct answers |
| `app/tools.py` | the 53 tools |
| `app/speech.py` | live listening: VAD, end-of-turn, streaming speech-to-text, wake word, owner-voice check |
| `app/voice.py` | text-to-speech (Piper), whisper.cpp fallback |
| `app/orb_native.py` | see-through orb window (per-pixel alpha, ctypes + numpy) |
| `app/desktop.py` | desktop control executor |
| `app/policy.py` | permission rules |
| `app/timeparse.py` | "7 pm", "Friday 10am", "in 20 minutes" → time |
| `app/winrt_ocr.ps1`, `app/winrt_media.ps1` | Windows OCR and media-session helpers |
| `ui/` | orb + panel (HTML/CSS/JS) |
| `data/` | created at runtime: memory, notes, reminders, logs (git-ignored) |

## Credits and licenses

Code: MIT, see [LICENSE](LICENSE). Third-party parts are downloaded by `setup.ps1` and are not included in this repo:

| Part | License |
|---|---|
| [Gemma 4 E2B](https://huggingface.co/google/gemma-4-E2B-it) (Google), English-trimmed | Apache-2.0 |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) b12360 | MIT |
| [bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5) (BAAI), GGUF by CompendiumLabs | MIT |
| [Nemotron Speech Streaming EN 0.6B](https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b) (NVIDIA), int8 export by sherpa-onnx | NVIDIA Open Model License |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | Apache-2.0 |
| [Silero VAD](https://github.com/snakers4/silero-vad) | MIT |
| [Smart Turn v3.2](https://huggingface.co/pipecat-ai/smart-turn-v3) (Pipecat) | BSD-2-Clause |
| [WeSpeaker](https://github.com/wenet-e2e/wespeaker) ResNet34 speaker model | Apache-2.0 |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) b5454 + base.en model (fallback) | MIT |
| [Piper](https://github.com/rhasspy/piper) + en_US-lessac-medium voice | see the voice's model card |
| [openWakeWord](https://github.com/dscripka/openWakeWord) + `hey_jarvis` model | code Apache-2.0; pre-trained models CC BY-NC-SA 4.0 (non-commercial) |
| [agent-desktop](https://github.com/vercel-labs/agent-desktop) v1.2.0 (Vercel Labs) | Apache-2.0 |
| [pywebview](https://github.com/r0x0r/pywebview) | BSD-3-Clause |
| [pycaw](https://github.com/AndreMiras/pycaw) | MIT |
