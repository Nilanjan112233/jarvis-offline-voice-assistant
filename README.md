# Jarvis: an offline voice assistant for Windows, running a 2B LLM on CPU

A personal assistant that runs **entirely on your own PC**: wake word, speech-to-text, a local LLM, text-to-speech, and 49 tools that act on Windows. No cloud and no GPU. It is built around a **layered "System 1 / System 2" design**, so a small 2-billion-parameter model behaves reliably:

```
 "Hey Jarvis, ..." ─► wake word (openWakeWord, ~0.4% CPU) ─► speech-to-text (whisper.cpp, base.en)
                                                                     │
        ┌────────────────────────────────────────────────────────────┘
        ▼
  System 1 router (bge-small embeddings + rules, 6–50 ms)
        ├── simple / exact things ─► run directly, no LLM (volume, timers, maths, PC facts, open apps…)
        └── everything else ─► LLM (Gemma 4 E2B, English-only trim, llama.cpp + MTP draft)
                                   │  JSON tool calls
                                   ▼
                        tools (49) ─► permission policy (in code) ─► Allow/Deny card for risky actions
                                   │
                        code guards: no "I did X" without a real tool call, numbers must come
                        from tool output, every part of a multi-part request must be covered …
                                   │
                                   ▼
                     answer ─► panel cards + spoken reply (Piper TTS)
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

## Features

- **Voice, fully offline:** a "Hey Jarvis" wake word with a two-stage check (the wake model has to fire twice, and then the transcript must contain "Jarvis", so "Hey, Java is…" is rejected), whisper.cpp speech-to-text, and a Piper neural voice. You can interrupt it, and Esc stops speech.
- **UI:** a floating animated orb and a slide-out glass panel, built with pywebview and HTML/CSS. It shows a live timeline of each step, result cards (weather, PC stats, headlines, files, currency, world clock), in-panel Allow/Deny cards, history and settings. Global hotkey: Ctrl+Alt+J.
- **49 tools:** web search, weather, news, currency, world time, PC/network info, installed apps, files (find, read, largest, sizes, write, move, delete to the Recycle Bin), sandboxed Python/PowerShell, media keys, volume, settings, screenshot, lock, clipboard, reminders/timers, lists, notes, memory, and learned "skills".
- **Desktop control without vision:** it uses apps through the Windows accessibility tree ([agent-desktop](https://github.com/vercel-labs/agent-desktop)). The LLM writes plain steps ("click File", "type X in Text editor"), and a System-1 executor matches each one to an on-screen element by fuzzy name matching, with no LLM call per step.
- **Memory:** facts like "my name is…", "I live in…" and "don't use X" are saved automatically by code, not by the model.

## Safety, enforced in code rather than in the prompt

- `app/policy.py`: read-only actions run immediately. Anything that changes something asks first. Destructive, security-changing or downloading commands and system folders are blocked. The model's text is never passed straight to a shell.
- Desktop control stays locked to the window it started on, asks before risky buttons (send, delete, buy, pay, install, sign out…), and never types into password fields.
- Text from websites, files and command output is treated as data, never as instructions.
- `tests/check_policy.py`: 28 permission-rule cases, all passing.

## Setup (Windows 10/11, Python 3.13)

```powershell
git clone https://github.com/Nilanjan112233/jarvis-offline-voice-assistant
cd jarvis-offline-voice-assistant
powershell -ExecutionPolicy Bypass -File setup.ps1   # ~2 GB of engine/models/voice, SHA-256 checked
.\run_jarvis.bat
```

Hardware: any 4-core x86-64 CPU, about 4 GB of free RAM. Startup takes about 40 s while the model reads its tool list. After that it's cached.

UI demo without the backend: open `ui/panel.html` in a browser. It switches to a demo mode with sample data.

Self-test (drives the real window end to end): `set JARVIS_SELFTEST=1` and then `run_jarvis.bat`. The results go to `data/selftest.log`.

## Layout

| Path | What |
|---|---|
| `app/main.py` | desktop app: orb + panel windows, events, hotkey, reminders, wake word |
| `app/agent.py` | the brain: persona, LLM loop, code guards, auto-memory |
| `app/router.py` | System-1 router: embeddings + rules, direct answers |
| `app/tools.py` | the 49 tools |
| `app/desktop.py` | desktop control executor |
| `app/policy.py` | permission rules |
| `app/voice.py` | wake word, speech-to-text, text-to-speech |
| `app/timeparse.py` | "7 pm", "Friday 10am", "in 20 minutes" → time |
| `ui/` | orb + panel (HTML/CSS/JS) |
| `data/` | created at runtime: memory, notes, reminders, logs (git-ignored) |

## Credits and licenses

Code: MIT, see [LICENSE](LICENSE). Third-party parts are downloaded by `setup.ps1` and are not included in this repo:

| Part | License |
|---|---|
| [Gemma 4 E2B](https://huggingface.co/google/gemma-4-E2B-it) (Google), English-trimmed | Apache-2.0 |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) b12360 | MIT |
| [bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5) (BAAI), GGUF by CompendiumLabs | MIT |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) b5454 + base.en model | MIT |
| [Piper](https://github.com/rhasspy/piper) + en_US-lessac-medium voice | see the voice's model card |
| [openWakeWord](https://github.com/dscripka/openWakeWord) + `hey_jarvis` model | code Apache-2.0; pre-trained models CC BY-NC-SA 4.0 (non-commercial) |
| [agent-desktop](https://github.com/vercel-labs/agent-desktop) v1.2.0 (Vercel Labs) | Apache-2.0 |
| [pywebview](https://github.com/r0x0r/pywebview) | BSD-3-Clause |
