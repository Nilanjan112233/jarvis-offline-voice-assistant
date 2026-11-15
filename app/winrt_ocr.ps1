# Jarvis helper: read the text on screen with Windows' built-in offline OCR (nothing leaves the PC, nothing is saved).
# Usage: powershell -File winrt_ocr.ps1 [-Window "title part"]   (no -Window = the foreground window; "screen" = whole screen)
param([string]$Window = "", [string]$Save = "")
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime, System.Drawing, System.Windows.Forms
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public class JW {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr h, int i);
  [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr h, uint cmd);
  [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h, int a, out int v, int s);
  public static bool Cloaked(IntPtr h) { int v; DwmGetWindowAttribute(h, 14, out v, 4); return v != 0; }
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr l);
  public struct RECT { public int L, T, R, B; }
  public static string Title(IntPtr h) { var s = new StringBuilder(300); GetWindowText(h, s, 300); return s.ToString(); }
}
"@
[JW]::SetProcessDPIAware() | Out-Null
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }

# which area
$h = [IntPtr]::Zero
if ($Window -and $Window -ne "screen") {
    $script:found = [IntPtr]::Zero
    [JW]::EnumWindows({ param($w, $l) if ([JW]::IsWindowVisible($w) -and -not [JW]::IsIconic($w) -and ([JW]::Title($w) -like "*$Window*")) { $script:found = $w; return $false }; return $true }, [IntPtr]::Zero) | Out-Null
    $h = $script:found
    if ($h -eq [IntPtr]::Zero) { "NO WINDOW: no visible window has '$Window' in its title."; exit }
} elseif ($Window -ne "screen") {
    # the top normal window that isn't Jarvis itself (its panel is usually in front while you talk to it)
    $script:found = [IntPtr]::Zero
    [JW]::EnumWindows({ param($w, $l)
        $t = [JW]::Title($w); $ex = [JW]::GetWindowLong($w, -20)
        if ([JW]::IsWindowVisible($w) -and -not [JW]::IsIconic($w) -and -not [JW]::Cloaked($w) -and $t -and ($ex -band 0x80) -eq 0 -and
            $t -notmatch '^(Jarvis|Jarvis orb|Program Manager)$') { $r0 = New-Object JW+RECT; [JW]::GetWindowRect($w, [ref]$r0) | Out-Null
            if (($r0.R - $r0.L) -gt 200 -and ($r0.B - $r0.T) -gt 150) { $script:found = $w; return $false } }
        return $true }, [IntPtr]::Zero) | Out-Null
    $h = $script:found
}
if ($h -ne [IntPtr]::Zero) {
    $r = New-Object JW+RECT; [JW]::GetWindowRect($h, [ref]$r) | Out-Null
    $x, $y, $w, $hh = $r.L, $r.T, ($r.R - $r.L), ($r.B - $r.T); $title = [JW]::Title($h)
} else {
    $b = [System.Windows.Forms.SystemInformation]::VirtualScreen; $x, $y, $w, $hh = $b.Left, $b.Top, $b.Width, $b.Height; $title = "whole screen"
}
if ($w -le 0 -or $hh -le 0) { "NO WINDOW: nothing to read."; exit }
$bmp = New-Object System.Drawing.Bitmap $w, $hh
$g = [System.Drawing.Graphics]::FromImage($bmp)
if ($h -ne [IntPtr]::Zero) {      # the window's own picture, even if something covers it
    $hdc = $g.GetHdc(); $okp = [JW]::PrintWindow($h, $hdc, 2); $g.ReleaseHdc($hdc)
    if (-not $okp) { $g.CopyFromScreen($x, $y, 0, 0, $bmp.Size) }
} else { $g.CopyFromScreen($x, $y, 0, 0, $bmp.Size) }
$g.Dispose()
# 2x bigger (OCR reads small UI text much better), within the OCR size limit
$k = [Math]::Min(2.0, 9000.0 / [Math]::Max($w, $hh))
$big = New-Object System.Drawing.Bitmap ([int]($w * $k)), ([int]($hh * $k))
$g2 = [System.Drawing.Graphics]::FromImage($big); $g2.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
# dark theme? OCR reads dark text on a light background best -> grayscale, and invert if the window is mostly dark
$sum = 0; $n = 0
for ($sx = 0; $sx -lt $w; $sx += [Math]::Max(1, [int]($w / 40))) { for ($sy = 0; $sy -lt $hh; $sy += [Math]::Max(1, [int]($hh / 40))) {
    $c = $bmp.GetPixel($sx, $sy); $sum += ($c.R + $c.G + $c.B) / 3; $n++ } }
$dark = ($n -gt 0) -and (($sum / $n) -lt 110)
$m = if ($dark) { @(@(-0.3, -0.3, -0.3, 0, 0), @(-0.59, -0.59, -0.59, 0, 0), @(-0.11, -0.11, -0.11, 0, 0), @(0, 0, 0, 1, 0), @(1, 1, 1, 0, 1)) }
     else { @(@(0.3, 0.3, 0.3, 0, 0), @(0.59, 0.59, 0.59, 0, 0), @(0.11, 0.11, 0.11, 0, 0), @(0, 0, 0, 1, 0), @(0, 0, 0, 0, 1)) }
$cm = New-Object System.Drawing.Imaging.ColorMatrix(,[single[][]]$m)
$ia = New-Object System.Drawing.Imaging.ImageAttributes; $ia.SetColorMatrix($cm)
$g2.DrawImage($bmp, (New-Object System.Drawing.Rectangle 0, 0, $big.Width, $big.Height), 0, 0, $w, $hh, [System.Drawing.GraphicsUnit]::Pixel, $ia)
$g2.Dispose(); $bmp.Dispose()
if ($Save) { $big.Save($Save, [System.Drawing.Imaging.ImageFormat]::Png) }   # debugging only
$ms = New-Object System.IO.MemoryStream; $big.Save($ms, [System.Drawing.Imaging.ImageFormat]::Bmp); $big.Dispose()
$ms.Position = 0
[Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
$ras = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($ms)
$dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($ras)) ([Windows.Graphics.Imaging.BitmapDecoder])
$sb = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $eng) { "NO OCR: Windows text recognition isn't installed for your language."; exit }
$res = Await ($eng.RecognizeAsync($sb)) ([Windows.Media.Ocr.OcrResult])
"WINDOW: $title"
foreach ($line in $res.Lines) { $line.Text }
