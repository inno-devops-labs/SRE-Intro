param(
    [int]$Rps = 5,
    [int]$Duration = 300,
    [string]$GatewayUrl = 'http://localhost:3080',
    [int[]]$EventIds = @(1, 3, 5)
)

$ErrorActionPreference = 'Stop'
$success = 0; $fail = 0; $reserveFail = 0
$started = Get-Date; $lastProgress = -1
$intervalMs = [int](1000 / $Rps)
Write-Output "QuickTicket payment-heavy traffic: target=$GatewayUrl rps=$Rps duration=$($Duration)s"

while (((Get-Date) - $started).TotalSeconds -lt $Duration) {
    $status = 0
    try {
        $eventId = Get-Random -InputObject $EventIds
        $reservation = Invoke-RestMethod -Uri "$GatewayUrl/events/$eventId/reserve" -Method Post -ContentType 'application/json' -Body '{"quantity":1}'
        $response = Invoke-WebRequest -UseBasicParsing -Uri "$GatewayUrl/reserve/$($reservation.reservation_id)/pay" -Method Post
        $status = [int]$response.StatusCode
    } catch {
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode.value__ }
    }
    if ($status -ge 200 -and $status -lt 400) { $success++ } else { $fail++ }
    $elapsed = [int]((Get-Date) - $started).TotalSeconds
    if ($elapsed -gt 0 -and $elapsed % 15 -eq 0 -and $elapsed -ne $lastProgress) {
        $lastProgress = $elapsed; $total = $success + $fail
        $rate = if ($total) { [Math]::Round($fail * 100.0 / $total, 1) } else { 0 }
        Write-Output "[$($elapsed)s] pay_requests=$total success=$success fail=$fail error_rate=$rate%"
    }
    Start-Sleep -Milliseconds $intervalMs
}
$total = $success + $fail
$rate = if ($total) { [Math]::Round($fail * 100.0 / $total, 1) } else { 0 }
Write-Output "Done. pay_requests=$total success=$success fail=$fail error_rate=$rate%"
