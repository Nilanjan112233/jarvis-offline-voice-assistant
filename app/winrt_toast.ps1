# Shows a Windows notification (bottom-right toast + Action Center) for a Jarvis reminder. Read-only otherwise.
# Usage: winrt_toast.ps1 -Title "Reminder" -Text "Go to lunch"
param([string]$Title = "Jarvis", [string]$Text = "")
$ErrorActionPreference = "Stop"
try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
    $esc = { param($s) [System.Security.SecurityElement]::Escape($s) }
    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml("<toast scenario='reminder'><visual><binding template='ToastGeneric'><text>$(& $esc $Title)</text><text>$(& $esc $Text)</text></binding></visual><audio silent='true'/><actions><action content='OK' arguments='ok' activationType='foreground'/></actions></toast>")
    $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
    # Windows PowerShell's own app id (always registered) - no install needed
    $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
    "OK"
} catch {
    "ERROR $($_.Exception.Message)"
}
