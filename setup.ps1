# Jarvis setup: downloads the engine, models and voice files (about 2.7 GB), checks every file's
# SHA-256, creates a Python venv and installs the packages. Safe to run again: files that are
# already there and match their SHA-256 are skipped.
# Run from the repo folder:   powershell -ExecutionPolicy Bypass -File setup.ps1
#   -Vision   also get the image encoder (557 MB) for look_at_screen / describe_image
#   -Dev      also install pytest + ruff (requirements-dev.txt) to run the tests

param([switch]$Vision, [switch]$Dev)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # much faster Invoke-WebRequest
$Root = $PSScriptRoot

# Python 3.13 through the "py" launcher (python.org installer); the pinned wheels are for 3.13
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { throw "The Python launcher 'py' was not found. Install Python 3.13 from python.org (tick 'py launcher')." }
& py -3.13 -c "import sys" 2>$null
if ($LASTEXITCODE -ne 0) { throw "Python 3.13 is needed (py -3.13 failed). Install it from python.org." }
$free = (Get-PSDrive ((Resolve-Path $Root).Drive.Name)).Free / 1GB
if ($free -lt 6) { Write-Warning ("Only {0:N1} GB free on this drive; setup needs about 6 GB (downloads + unpacked)." -f $free) }
$Dl = Join-Path $Root "downloads"
New-Item -ItemType Directory -Force $Dl, "$Root\engine", "$Root\models", "$Root\voice\piper", "$Root\voice\whisper", "$Root\voice\models", "$Root\bin" | Out-Null

$HF = "https://huggingface.co"
$SO = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
$Files = @(
  # url, saved as, sha256
  @("https://github.com/ggml-org/llama.cpp/releases/download/b12360/llama-b12360-bin-win-cpu-x64.zip", "$Dl\llama-cpu.zip",
    "eae1726047e0eaa707b1302929d196c999b96c2f243b21aea6e93bcebacc4e17"),
  @("$HF/Nilmon/gemma-4-E2B-en50v-GGUF/resolve/main/gemma-4-E2B-en50v-Q4_0.gguf", "$Root\models\gemma-4-E2B-en50v-Q4_0.gguf",
    "0549a38ee4b990fdfa46774b336abd800db01fa20677d6ff368dac87cb47435a"),
  @("$HF/Nilmon/gemma-4-E2B-en50v-GGUF/resolve/main/mtp-gemma-4-E2B-en50v-Q4_0.gguf", "$Root\models\mtp-gemma-4-E2B-en50v-Q4_0.gguf",
    "7e6a22ed74d17775194727d994b6a8c662446f534a1dd52ec9ce0ee7adf23857"),
  @("$HF/CompendiumLabs/bge-small-en-v1.5-gguf/resolve/main/bge-small-en-v1.5-q8_0.gguf", "$Root\models\bge-small-en-v1.5-q8_0.gguf",
    "ec38e8da142596baa913124ae50550de284b6916bf59577ef2f0cb9660c2f514"),
  @("$HF/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx", "$Root\voice\piper\en_US-lessac-medium.onnx",
    "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f"),
  @("$HF/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json", "$Root\voice\piper\en_US-lessac-medium.onnx.json",
    "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0"),
  # more voices to pick from in Settings
  @("$HF/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx", "$Root\voice\piper\en_GB-alan-medium.onnx",
    "0a309668932205e762801f1efc2736cd4b0120329622adf62be09e56339d3330"),
  @("$HF/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json", "$Root\voice\piper\en_GB-alan-medium.onnx.json",
    "c0f0d124e5895c00e7c03b35dcc8287f319a6998a365b182deb5c8e752ee8c1e"),
  @("$HF/rhasspy/piper-voices/resolve/main/en/en_GB/northern_english_male/medium/en_GB-northern_english_male-medium.onnx", "$Root\voice\piper\en_GB-northern_english_male-medium.onnx",
    "57a219ae8e638873db7d18893304be5069c42868f392bb95c3ff17f0690d0689"),
  @("$HF/rhasspy/piper-voices/resolve/main/en/en_GB/northern_english_male/medium/en_GB-northern_english_male-medium.onnx.json", "$Root\voice\piper\en_GB-northern_english_male-medium.onnx.json",
    "69557ed3d974463453e9b0c09dd99a7ed0e52b8b87b64b357dbeeb2540a97d47"),
  @("$HF/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx", "$Root\voice\piper\en_US-ryan-high.onnx",
    "b3990d7606e183ec8dbfba70a4607074f162de1a0c412e0180d1ff60bb154eca"),
  @("$HF/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx.json", "$Root\voice\piper\en_US-ryan-high.onnx.json",
    "c6d3b98f08315cb4bebf0d49d50fc4ff491b503c64b940cd3d5ca28543b48011"),
  @("https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-bin-x64.zip", "$Dl\whisper-bin-x64.zip",
    "6ba69e3482d7826214f90a6a9c84ca07782aec1e1d0c6a7c30c994fd5d816ccb"),
  @("$HF/ggerganov/whisper.cpp/resolve/main/ggml-base.en-q8_0.bin", "$Root\voice\whisper\ggml-base.en-q8_0.bin",
    "a4d4a0768075e13cfd7e19df3ae2dbc4a68d37d36a7dad45e8410c9a34f8c87e"),
  @("https://github.com/vercel-labs/agent-desktop/releases/download/v1.2.0/agent-desktop-v1.2.0-x86_64-pc-windows-msvc.tar.gz", "$Dl\agent-desktop.tar.gz",
    "a4199f741a441cedd349bf56c5eba01783b1261258b498efeb7ec9945220c3e8"),
  # live speech: streaming speech-to-text, voice activity, end-of-turn, owner-voice check
  @("$SO/asr-models/sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25.tar.bz2", "$Dl\nemotron-stt.tar.bz2",
    "78e2b79fcf7271553a74402a76b771b09ea40117a39566a79f52235b23db6358"),
  @("$SO/asr-models/silero_vad.onnx", "$Root\voice\models\silero_vad.onnx",
    "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"),
  @("$HF/pipecat-ai/smart-turn-v3/resolve/main/smart-turn-v3.2-cpu.onnx", "$Root\voice\models\smart-turn-v3.2-cpu.onnx",
    "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f"),
  @("$SO/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx", "$Root\voice\models\wespeaker_resnet34_LM.onnx",
    "e9848563da86f263117134dfd7ad63c92355b37de492b55e325400c9d9c39012")
)
if ($Vision) {
  # Gemma 4 E2B's own image encoder (ggml-org); the trimmed text model can use it unchanged
  $Files += ,@("$HF/ggml-org/gemma-4-E2B-it-GGUF/resolve/main/mmproj-gemma-4-E2B-it-Q8_0.gguf", "$Root\models\mmproj-gemma-4-E2B-it-Q8_0.gguf",
    "042cc2561806cd3b5cb5efb25cab76911e3100c43cc09d854904f921924b1429")
}

foreach ($f in $Files) {
  $url, $out, $sha = $f
  if (Test-Path $out) {
    if ((Get-FileHash $out -Algorithm SHA256).Hash -eq $sha.ToUpper()) { Write-Host "have  $(Split-Path $out -Leaf)"; continue }
    Remove-Item $out
  }
  Write-Host "get   $url"
  Invoke-WebRequest $url -OutFile $out -UseBasicParsing
  $got = (Get-FileHash $out -Algorithm SHA256).Hash
  if ($got -ne $sha.ToUpper()) { Remove-Item $out; throw "SHA-256 mismatch for $out (got $got)" }
}

Write-Host "unpack engine, speech-to-text and desktop control"
Expand-Archive "$Dl\llama-cpu.zip" "$Root\engine" -Force
Expand-Archive "$Dl\whisper-bin-x64.zip" "$Root\voice\whisper" -Force
tar -xzf "$Dl\agent-desktop.tar.gz" -C "$Root\bin"
tar -xjf "$Dl\nemotron-stt.tar.bz2" -C "$Root\voice\models"

function Invoke-Checked($what) {   # native programs don't stop the script on failure by themselves
  & $args[0] $args[1..($args.Count - 1)]
  if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}
$Py = "$Root\.venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { Invoke-Checked "creating the venv" py -3.13 -m venv "$Root\.venv" }
Invoke-Checked "pip upgrade" $Py -m pip install --upgrade pip
Invoke-Checked "installing requirements.txt" $Py -m pip install -r "$Root\requirements.txt"
if ($Dev) { Invoke-Checked "installing requirements-dev.txt" $Py -m pip install -r "$Root\requirements-dev.txt" }
Invoke-Checked "wake-word model download" $Py -c "import openwakeword.utils as u; u.download_models(['hey_jarvis'])"

# quick check that everything is where the app looks for it
$need = "$Root\engine\llama-server.exe", "$Root\bin\agent-desktop.exe", "$Root\models\gemma-4-E2B-en50v-Q4_0.gguf"
$need | Where-Object { -not (Test-Path $_) } | ForEach-Object { throw "missing after setup: $_" }
if (-not (Get-ChildItem "$Root\voice\models" -Directory -Filter "sherpa-onnx-nemotron-*")) { throw "missing after setup: Nemotron speech model" }

Write-Host "`nSetup done. Start Jarvis with run_jarvis.bat (the first start takes about a minute)."
