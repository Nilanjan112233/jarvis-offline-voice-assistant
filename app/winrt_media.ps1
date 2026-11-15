# Jarvis helper: Windows media sessions (what's playing in Chrome/Spotify/etc.) - list or control. Read-only except play/pause/next/prev.
# Usage: powershell -File winrt_media.ps1 -Action list|play|pause|toggle|next|previous|stop [-Match "text"]
param([string]$Action = "list", [string]$Match = "")
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime] | Out-Null
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$sessions = @($mgr.GetSessions())
$current = $mgr.GetCurrentSession()
$rows = @()
foreach ($s in $sessions) {
    $p = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
    $st = $s.GetPlaybackInfo().PlaybackStatus
    $rows += [pscustomobject]@{ app = $s.SourceAppUserModelId; title = $p.Title; artist = $p.Artist; status = "$st"; s = $s;
                                current = ($current -ne $null -and $s.SourceAppUserModelId -eq $current.SourceAppUserModelId) }
}
if ($Action -eq "list") {
    if (-not $rows) { "NONE"; exit }
    foreach ($r in $rows) { "$($r.status)`t$($r.app)`t$($r.title)`t$($r.artist)`t$($r.current)" }
    exit
}
# pick the session: matching title/app > playing (for pause) > paused (for play) > current
$pick = $null
if ($Match) { $pick = $rows | Where-Object { "$($_.title) $($_.artist) $($_.app)" -like "*$Match*" } | Select-Object -First 1 }
if (-not $pick -and ($Action -in @("pause", "stop"))) { $pick = $rows | Where-Object { $_.status -eq "Playing" } | Select-Object -First 1 }
if (-not $pick -and ($Action -eq "play")) { $pick = $rows | Where-Object { $_.status -eq "Paused" } | Select-Object -First 1 }
if (-not $pick) { $pick = $rows | Where-Object { $_.current } | Select-Object -First 1 }
if (-not $pick) { $pick = $rows | Select-Object -First 1 }
if (-not $pick) { "NONE"; exit }
$s = $pick.s
$ok = switch ($Action) {
    "play" { Await ($s.TryPlayAsync()) ([bool]) }
    "pause" { Await ($s.TryPauseAsync()) ([bool]) }
    "toggle" { Await ($s.TryTogglePlayPauseAsync()) ([bool]) }
    "next" { Await ($s.TrySkipNextAsync()) ([bool]) }
    "previous" { Await ($s.TrySkipPreviousAsync()) ([bool]) }
    "stop" { Await ($s.TryStopAsync()) ([bool]) }
}
Start-Sleep -Milliseconds 400
"$ok`t$($s.GetPlaybackInfo().PlaybackStatus)`t$($pick.app)`t$($pick.title)`t$($pick.artist)"
