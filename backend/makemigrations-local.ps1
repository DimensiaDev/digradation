param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Message
)
$ErrorActionPreference = "Stop"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ComposeDir = Join-Path $ScriptDir ".."

# Load .env (still shared with the containerized setup, for consistent credentials)
Get-Content (Join-Path $ComposeDir ".env") | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $key, $value = $line -split "=", 2
        [System.Environment]::SetEnvironmentVariable($key.Trim(), $value.Trim(), "Process")
    }
}
$env:POSTGRES_HOST = "localhost"  # native install, always localhost — no container networking at all
# 5433, matching docker-compose.override.yml.dev. Never 5432: a developer may
# already be running PostgreSQL there, and autogenerating against the wrong
# database produces a migration for somebody else's schema.
if (-not $env:POSTGRES_PORT) { $env:POSTGRES_PORT = "5433" }

Write-Host "Generating migration: $Message"
poetry -C $ScriptDir run alembic revision --autogenerate -m $Message
if ($LASTEXITCODE -ne 0) {
    throw "alembic revision --autogenerate failed with exit code $LASTEXITCODE"
}

# Write-Host "Applying migrations"
# poetry -C $ScriptDir run alembic upgrade head
# if ($LASTEXITCODE -ne 0) {
#     throw "alembic upgrade head failed with exit code $LASTEXITCODE"
# }

Write-Host "Done."
