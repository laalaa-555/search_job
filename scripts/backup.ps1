$ErrorActionPreference = 'Stop'
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    New-Item -ItemType Directory -Path 'data/backups' -Force | Out-Null
    $taskBackupPath = Join-Path (Get-Location) ('data/backups/jobs-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.dump')
    docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/jobfinder.dump'
    if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed' }
    docker compose cp db:/tmp/jobfinder.dump $taskBackupPath
    if ($LASTEXITCODE -ne 0) { throw 'Backup copy failed' }
    Write-Output "Backup saved: $taskBackupPath"
} finally { Pop-Location }
