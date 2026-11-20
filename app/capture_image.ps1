# Makes a small JPEG for Jarvis's local vision model: the front window, the whole screen, or a resized copy of an image file.
# Read-only for your files: the JPEG goes to the temp folder given in -Out. Nothing is sent anywhere.
# Usage: capture_image.ps1 -Mode front|screen|file [-Path image.png] -Out C:\...\jarvis_view.jpg [-MaxSide 1024]
param([string]$Mode = "front", [string]$Path = "", [string]$Out, [int]$MaxSide = 1024)
$ErrorActionPreference = "Stop"
try {
    Add-Type -AssemblyName System.Drawing, System.Windows.Forms
    Add-Type @"
using System; using System.Runtime.InteropServices;
public class JW { [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; } }
"@
    if ($Mode -eq "file") {
        $src = [System.Drawing.Image]::FromFile((Resolve-Path -LiteralPath $Path).Path)
    } else {
        if ($Mode -eq "front") {
            $r = New-Object JW+RECT
            [void][JW]::GetWindowRect([JW]::GetForegroundWindow(), [ref]$r)
            $x, $y, $w, $h = $r.L, $r.T, ($r.R - $r.L), ($r.B - $r.T)
        }
        if ($Mode -ne "front" -or $w -lt 50 -or $h -lt 50) {
            $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
            $x, $y, $w, $h = $b.X, $b.Y, $b.Width, $b.Height
        }
        $src = New-Object System.Drawing.Bitmap $w, $h
        $g = [System.Drawing.Graphics]::FromImage($src); $g.CopyFromScreen($x, $y, 0, 0, $src.Size); $g.Dispose()
    }
    $k = [Math]::Min(1.0, $MaxSide / [Math]::Max($src.Width, $src.Height))
    $nw, $nh = [int]($src.Width * $k), [int]($src.Height * $k)
    $dst = New-Object System.Drawing.Bitmap $nw, $nh
    $g = [System.Drawing.Graphics]::FromImage($dst)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.DrawImage($src, 0, 0, $nw, $nh); $g.Dispose(); $src.Dispose()
    $codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq "image/jpeg" }
    $ep = New-Object System.Drawing.Imaging.EncoderParameters 1
    $ep.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter ([System.Drawing.Imaging.Encoder]::Quality), 85L
    $dst.Save($Out, $codec, $ep); $dst.Dispose()
    "OK $nw x $nh"
} catch {
    "ERROR $($_.Exception.Message)"
}
