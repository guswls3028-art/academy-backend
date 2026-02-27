# Setup tenant 9999 for local development
# Creates tenant with code="9999", localhost domain, and superuser (e.g. admin97, password)

$ErrorActionPreference = "Stop"

Write-Host "Setting up tenant 9999 for local development..." -ForegroundColor Cyan

# Check if Docker container is running
$containerName = "academy-api"
$container = docker ps --filter "name=$containerName" --format "{{.Names}}" | Select-Object -First 1

if (-not $container) {
    Write-Host "Error: Docker container '$containerName' is not running." -ForegroundColor Red
    Write-Host "Please start the API server first." -ForegroundColor Yellow
    exit 1
}

Write-Host "Found container: $container" -ForegroundColor Gray

# Get script directory and copy Python script to container
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonScript = Join-Path $scriptDir "setup_tenant_9999.py"
$containerScriptPath = "/app/scripts/setup_tenant_9999.py"

# Copy script to container (assuming /app is the working directory)
docker cp $pythonScript "${containerName}:${containerScriptPath}"

# Execute Python script in Docker container (from /app directory)
docker exec -i $containerName python $containerScriptPath

$exitCode = $LASTEXITCODE

# Clean up
docker exec $containerName rm -f $containerScriptPath 2>$null

if ($exitCode -eq 0) {
    Write-Host "`n✓ Tenant 9999 setup completed successfully!" -ForegroundColor Green
} else {
    Write-Host "`n✗ Setup failed. Check the error messages above." -ForegroundColor Red
    exit 1
}
