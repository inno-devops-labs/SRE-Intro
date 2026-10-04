param(
    [int]$Rps = 5,
    [int]$Duration = 60,
    [string]$GatewayUrl = $(if ($env:GATEWAY_URL) { $env:GATEWAY_URL } else { 'http://localhost:3080' })
)

$ErrorActionPreference = 'Stop'
if ($Rps -le 0 -or $Duration -le 0) { throw 'Rps and Duration must be positive.' }

$intervalMs = [int](1000 / $Rps)
$success = 0
$fail = 0
$started = Get-Date
$lastProgress = -1

Write-Output 'QuickTicket PowerShell Load Generator'
Write-Output "Target: $GatewayUrl | RPS: $Rps | Duration: $($Duration)s"
Write-Output '---'

while (((Get-Date) - $started).TotalSeconds -lt $Duration) {
    $status = 0
    try {
        $choice = Get-Random -Minimum 0 -Maximum 100
        if ($choice -lt 70) {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "$GatewayUrl/events" -Method Get
        } elseif ($choice -lt 90) {
            $eventId = Get-Random -Minimum 1 -Maximum 6
            $response = Invoke-WebRequest -UseBasicParsing -Uri "$GatewayUrl/events/$eventId/reserve" -Method Post -ContentType 'application/json' -Body '{"quantity":1}'
        } else {
            $eventId = Get-Random -Minimum 1 -Maximum 6
            $reserve = Invoke-RestMethod -Uri "$GatewayUrl/events/$eventId/reserve" -Method Post -ContentType 'application/json' -Body '{"quantity":1}'
            $response = Invoke-WebRequest -UseBasicParsing -Uri "$GatewayUrl/reserve/$($reserve.reservation_id)/pay" -Method Post
        }
        $status = [int]$response.StatusCode
    } catch {
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode.value__ }
    }

    if ($status -ge 200 -and $status -lt 400) { $success++ } else { $fail++ }
    $elapsed = [int]((Get-Date) - $started).TotalSeconds
    if ($elapsed -gt 0 -and ($elapsed % 10 -eq 0) -and $elapsed -ne $lastProgress) {
        $lastProgress = $elapsed
        $total = $success + $fail
        $rate = if ($total) { [Math]::Round(($fail * 100.0 / $total), 1) } else { 0 }
        Write-Output "[$($elapsed)s] requests=$total success=$success fail=$fail error_rate=$rate%"
    }
    Start-Sleep -Milliseconds $intervalMs
}

$total = $success + $fail
$errorRate = if ($total) { [Math]::Round(($fail * 100.0 / $total), 1) } else { 0 }
Write-Output '---'
Write-Output "Done. total=$total success=$success fail=$fail error_rate=$errorRate%"
