param(
    [string]$Prefix = 'http://+:18080/alerts/',
    [string]$OutputPath = "$env:TEMP\quickticket-webhooks.jsonl"
)

$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add($Prefix)
$listener.Start()
Write-Output "QuickTicket webhook receiver listening on $Prefix"
Write-Output "Events are recorded outside the repository: $OutputPath"

try {
    while ($listener.IsListening) {
        $context = $listener.GetContext()
        $reader = [System.IO.StreamReader]::new($context.Request.InputStream, $context.Request.ContentEncoding)
        $body = $reader.ReadToEnd()
        $reader.Dispose()
        $headers = @{}
        foreach ($key in $context.Request.Headers.AllKeys) { $headers[$key] = $context.Request.Headers[$key] }
        $status = ''
        try { $status = ($body | ConvertFrom-Json).status } catch { $status = 'unparsed' }
        $record = [ordered]@{ timestamp = (Get-Date).ToString('o'); status = $status; headers = $headers; body = $body }
        ($record | ConvertTo-Json -Compress -Depth 8) | Add-Content -LiteralPath $OutputPath
        Write-Output "$($record.timestamp) status=$status"
        $context.Response.StatusCode = 200
        $context.Response.Close()
    }
} finally {
    if ($listener.IsListening) { $listener.Stop() }
    $listener.Close()
}
