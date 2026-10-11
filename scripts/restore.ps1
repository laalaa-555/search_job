param([Parameter(Mandatory=$true)][string]$BackupPath)
$ErrorActionPreference = 'Stop'
$taskRestorePath = (Resolve-Path -LiteralPath $BackupPath).Path
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    if (-not (Test-Path -LiteralPath $taskRestorePath -PathType Leaf)) { throw 'Backup file does not exist' }
    & "$PSScriptRoot/backup.ps1"
    docker compose stop api worker frontend
    if ($LASTEXITCODE -ne 0) { throw 'Failed to stop services' }
    docker compose cp $taskRestorePath db:/tmp/jobfinder-restore.dump
    if ($LASTEXITCODE -ne 0) { throw 'Restore copy failed; services remain stopped' }
    docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --exit-on-error /tmp/jobfinder-restore.dump'
    if ($LASTEXITCODE -ne 0) { throw 'Restore failed; services remain stopped. Use the backup just created to recover.' }
    docker compose up -d
    if ($LASTEXITCODE -ne 0) { throw 'Data restored, but restarting services failed' }
} finally { Pop-Location }
