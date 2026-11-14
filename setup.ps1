# Jarvis setup: downloads the engine, models and voice files (about 2 GB), checks every file's
# SHA-256, creates a Python venv and installs the packages.
# Run from the repo folder:   powershell -ExecutionPolicy Bypass -File setup.ps1

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # much faster Invoke-WebRequest
$Root = $PSScriptRoot
$Dl = Join-Path $Root "downloads"
New-Item -ItemType Directory -Force $Dl, "$Root\engine", "$Root\models", "$Root\voice\piper", "$Root\voice\whisper", "$Root\bin" | Out-Null

$HF = "https://huggingface.co"
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
  @("https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-bin-x64.zip", "$Dl\whisper-bin-x64.zip",
    "6ba69e3482d7826214f90a6a9c84ca07782aec1e1d0c6a7c30c994fd5d816ccb"),
  @("$HF/ggerganov/whisper.cpp/resolve/main/ggml-base.en-q8_0.bin", "$Root\voice\whisper\ggml-base.en-q8_0.bin",
    "a4d4a0768075e13cfd7e19df3ae2dbc4a68d37d36a7dad45e8410c9a34f8c87e"),
  @("https://github.com/vercel-labs/agent-desktop/releases/download/v1.2.0/agent-desktop-v1.2.0-x86_64-pc-windows-msvc.tar.gz", "$Dl\agent-desktop.tar.gz",
    "a4199f741a441cedd349bf56c5eba01783b1261258b498efeb7ec9945220c3e8")
)

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

if (-not (Test-Path "$Root\.venv")) { py -3.13 -m venv "$Root\.venv" }
& "$Root\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$Root\.venv\Scripts\python.exe" -m pip install -r "$Root\requirements.txt"
& "$Root\.venv\Scripts\python.exe" -c "import openwakeword.utils as u; u.download_models(['hey_jarvis'])"

Write-Host "`nSetup done. Start Jarvis with run_jarvis.bat"
