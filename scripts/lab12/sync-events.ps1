param([Parameter(Mandatory=$true)][string]$Image)
$ErrorActionPreference='Stop'
$revision=git rev-parse HEAD
$app=kubectl get application quickticket -n argocd -o json | ConvertFrom-Json
$app.PSObject.Properties.Remove('status')
$app | Add-Member -Name operation -MemberType NoteProperty -Value @{sync=@{revision=$revision;prune=$true}} -Force
$app | ConvertTo-Json -Depth 100 | kubectl replace -f -
$deadline=[DateTime]::UtcNow.AddSeconds(120)
do {
    $liveApp=kubectl get application quickticket -n argocd -o json | ConvertFrom-Json
    $deployment=kubectl get deployment events -o json | ConvertFrom-Json
    if ($liveApp.status.operationState.phase -eq 'Succeeded' -and $liveApp.status.operationState.syncResult.revision -eq $revision -and $deployment.spec.template.spec.containers[0].image -eq $Image) { break }
    Start-Sleep -Seconds 1
} while ([DateTime]::UtcNow -lt $deadline)
if ($deployment.spec.template.spec.containers[0].image -ne $Image) { throw 'Git revision not deployed' }
if ($liveApp.status.operationState.phase -ne 'Succeeded' -or $liveApp.status.operationState.syncResult.revision -ne $revision) { throw 'GitOps sync did not succeed; inspect Application operationState' }
kubectl rollout status deployment/events --timeout=120s
if ($LASTEXITCODE) { throw 'Events rollout failed' }
do {
    $pods=@((kubectl get pods -l app=events -o json | ConvertFrom-Json).items)
    $valid=@($pods | Where-Object {-not $_.metadata.deletionTimestamp -and $_.spec.containers[0].image -eq $Image -and $_.status.containerStatuses[0].ready})
    if ($pods.Count -eq 2 -and $valid.Count -eq 2) { break }
    Start-Sleep -Seconds 1
} while ([DateTime]::UtcNow -lt $deadline)
if ($pods.Count -ne 2 -or $valid.Count -ne 2) { throw 'Old consumers remain; do not contract' }
[pscustomobject]@{timestamp=[DateTime]::UtcNow.ToString('o');revision=$revision;pods=@($valid | ForEach-Object {@{name=$_.metadata.name;uid=$_.metadata.uid;image=$_.spec.containers[0].image;imageID=$_.status.containerStatuses[0].imageID;ready=$_.status.containerStatuses[0].ready}})} | ConvertTo-Json -Depth 10
