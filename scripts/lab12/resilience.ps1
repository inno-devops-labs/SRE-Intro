param([ValidateSet('failover','restart')][string]$Experiment, [string]$WindowName='')
$ErrorActionPreference='Stop'
$localData=[Environment]::GetFolderPath('LocalApplicationData')
$evidence=Join-Path $localData 'Temp\sre-lab12-evidence'
if (-not (Test-Path -LiteralPath $evidence)) { New-Item -ItemType Directory -Path $evidence | Out-Null }
$logProcesses=@()
if (-not $WindowName) { $WindowName=$Experiment }
$old=@((kubectl get pods -l app=gateway -o json | ConvertFrom-Json).items |
  Where-Object { -not $_.metadata.deletionTimestamp -and $_.status.containerStatuses[0].ready })
if ($old.Count -ne 5) { throw 'Require five Ready non-terminating gateway pods first' }
$oldIds=@($old | ForEach-Object {$_.metadata.uid})
$old | ForEach-Object { [pscustomobject]@{kind='original';name=$_.metadata.name;uid=$_.metadata.uid;created=$_.metadata.creationTimestamp} | ConvertTo-Json -Compress }
kubectl exec lab12-client -- python /work/stage.py start $WindowName
$timer=[Diagnostics.Stopwatch]::StartNew()
try {
  if ($Experiment -eq 'restart') {
    foreach ($pod in $old) {
      $name=$pod.metadata.name
      $logProcesses+=Start-Process kubectl -ArgumentList @('logs',$name,'--follow','--timestamps','--since=1s') -WindowStyle Hidden -PassThru -RedirectStandardOutput "$evidence\restart-$name.log" -RedirectStandardError "$evidence\restart-$name-error.log"
    }
    [pscustomobject]@{kind='restart_command';timestamp=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json -Compress
    # Explicit official plugin path also works when kubectl's Windows PATH
    # lookup cannot resolve the Unicode user directory.
    & (Join-Path $localData 'Temp\sre-lab11-tools\kubectl-argo-rollouts.exe') restart gateway
    if ($LASTEXITCODE -ne 0) { throw 'Restart command failed' }
  } else {
    $event=(kubectl get pods -l app=events -o json | ConvertFrom-Json).items | Where-Object {-not $_.metadata.deletionTimestamp -and $_.status.containerStatuses[0].ready} | Select-Object -First 1
    foreach ($victim in @($old[0],$event)) {
      [pscustomobject]@{kind='delete_command';timestamp=[DateTime]::UtcNow.ToString('o');name=$victim.metadata.name;uid=$victim.metadata.uid} | ConvertTo-Json -Compress
      kubectl delete pod $victim.metadata.name --wait=false
    }
  }
  $deadline=[DateTime]::UtcNow.AddSeconds(480)
  do {
    $pods=@((kubectl get pods -l 'app in (gateway,events)' -o json | ConvertFrom-Json).items)
    $states=@($pods | ForEach-Object { [pscustomobject]@{
      name=$_.metadata.name;uid=$_.metadata.uid;app=$_.metadata.labels.app
      created=$_.metadata.creationTimestamp;deleting=$_.metadata.deletionTimestamp
      ready=(@($_.status.conditions | Where-Object {$_.type -eq 'Ready' -and $_.status -eq 'True'}).Count -gt 0)
      phase=$_.status.phase
    }})
    $slices=@((kubectl get endpointslices -o json | ConvertFrom-Json).items | Where-Object {$_.metadata.labels.'kubernetes.io/service-name' -in @('gateway','events')} | ForEach-Object { [pscustomobject]@{service=$_.metadata.labels.'kubernetes.io/service-name';endpoints=$_.endpoints} })
    [pscustomobject]@{kind='poll';timestamp=[DateTime]::UtcNow.ToString('o');elapsed_s=[Math]::Round($timer.Elapsed.TotalSeconds,3);pods=$states;endpoints=$slices} | ConvertTo-Json -Depth 15 -Compress
    $readyGateway=@($states | Where-Object {$_.app -eq 'gateway' -and $_.ready -and -not $_.deleting})
    $readyEvents=@($states | Where-Object {$_.app -eq 'events' -and $_.ready -and -not $_.deleting})
    $allReplaced=($Experiment -ne 'restart' -or @($states | Where-Object {$_.uid -in $oldIds}).Count -eq 0)
    if ($readyGateway.Count -eq 5 -and $readyEvents.Count -eq 2 -and $allReplaced) { break }
    Start-Sleep -Seconds 1
  } while ([DateTime]::UtcNow -lt $deadline)
  if ($readyGateway.Count -ne 5 -or $readyEvents.Count -ne 2 -or -not $allReplaced) { throw 'Recovery deadline exceeded' }
  [pscustomobject]@{kind='recovered';timestamp=[DateTime]::UtcNow.ToString('o');elapsed_s=[Math]::Round($timer.Elapsed.TotalSeconds,3);gatewayReady=$readyGateway.Count;eventsReady=$readyEvents.Count} | ConvertTo-Json -Compress
  Start-Sleep -Seconds 10
} finally {
  foreach ($process in $logProcesses) { if (-not $process.HasExited) { Stop-Process -Id $process.Id } }
  kubectl exec lab12-client -- python /work/stage.py end $WindowName
}
