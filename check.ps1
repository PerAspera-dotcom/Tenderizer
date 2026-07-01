# Confirm all green: Postgres, API, and web dev server are up.
$ok = $true

function Test-Green($Name, $Check) {
    try {
        if (& $Check) {
            Write-Host "[OK]   $Name" -ForegroundColor Green
            return $true
        }
    } catch {}
    Write-Host "[FAIL] $Name" -ForegroundColor Red
    return $false
}

$ok = (Test-Green "Postgres (docker container running)" {
    (docker compose ps postgres --format json 2>$null | ConvertFrom-Json).State -eq "running"
}) -and $ok

$ok = (Test-Green "Postgres (accepting connections on 5432)" {
    $c = New-Object System.Net.Sockets.TcpClient
    $c.Connect("localhost", 5432)
    $connected = $c.Connected
    $c.Close()
    $connected
}) -and $ok

$ok = (Test-Green "API (http://localhost:8000/api/health-check)" {
    (Invoke-RestMethod -Uri "http://localhost:8000/api/health-check" -TimeoutSec 5).status -eq "ok"
}) -and $ok

$ok = (Test-Green "Web (http://localhost:5173)" {
    (Invoke-WebRequest -Uri "http://localhost:5173" -TimeoutSec 5 -UseBasicParsing).StatusCode -eq 200
}) -and $ok

if ($ok) {
    Write-Host "`nAll green." -ForegroundColor Green
    exit 0
} else {
    Write-Host "`nSomething's not up." -ForegroundColor Red
    exit 1
}
