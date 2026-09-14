$ErrorActionPreference = 'Stop'

$logDirectory = 'C:\virgo\ops\logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$logPath = Join-Path $logDirectory ("apply-resource-limits-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))

Start-Transcript -Path $logPath -Force
try {
    Write-Host "Starting controlled Virgo recreation at $(Get-Date -Format o)"

    docker info | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker Desktop engine is not available.'
    }

    docker compose `
        --env-file 'C:\virgo\.env' `
        -f 'C:\virgo\docker\docker-compose.yml' `
        up -d --no-build --wait --wait-timeout 180
    if ($LASTEXITCODE -ne 0) {
        throw 'Core Virgo app/PostgreSQL recreation failed.'
    }

    docker compose `
        --env-file 'C:\virgo-supervisor-readonly\virgo-supervisor-readonly-v1\.env' `
        -f 'C:\virgo-supervisor-readonly\virgo-supervisor-readonly-v1\docker\docker-compose.yml' `
        up -d --no-build --wait --wait-timeout 180
    if ($LASTEXITCODE -ne 0) {
        throw 'Virgo supervisor recreation failed.'
    }

    docker compose `
        -f 'C:\virgo-download\docker-compose.yml' `
        up -d --no-build --wait --wait-timeout 120
    if ($LASTEXITCODE -ne 0) {
        throw 'Virgo download service recreation failed.'
    }

    Write-Host 'Applied limits:'
    foreach ($containerName in @(
        'virgo-app-1',
        'virgo-postgres-1',
        'virgo-supervisor-readonly-supervisor-1',
        'virgo-download'
    )) {
        docker inspect $containerName --format '{{.Name}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}} cpus={{.HostConfig.NanoCpus}} memory={{.HostConfig.Memory}} reservation={{.HostConfig.MemoryReservation}} shares={{.HostConfig.CpuShares}}'
    }

    docker ps --filter 'name=virgo' --format 'table {{.Names}}\t{{.Status}}'
    Write-Host "Completed successfully at $(Get-Date -Format o)"
}
catch {
    Write-Error $_
    exit 1
}
finally {
    Stop-Transcript
}
