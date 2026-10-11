$PlaywrightArgs = @($args)
$PlaywrightArgs = @($args)
$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$nodeBin = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin'
$nodeExe = Join-Path $nodeBin 'node.exe'
if (-not (Test-Path -LiteralPath $nodeExe)) {
    throw "Node 24 runtime was not found at $nodeExe."
}

$mongoExe = $env:MONGOD_PATH
if (-not $mongoExe -or -not (Test-Path -LiteralPath $mongoExe)) {
    $mongoExe = Get-ChildItem -Path (Join-Path $env:ProgramFiles 'MongoDB\Server\*\bin\mongod.exe') -File -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $mongoExe -or -not (Test-Path -LiteralPath $mongoExe)) {
    throw 'A local mongod.exe is required. Set MONGOD_PATH to its executable path.'
}

function Test-LocalTcpPort([int]$Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $client.Connect('127.0.0.1', $Port)
        return $true
    }
    catch [System.Net.Sockets.SocketException] {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

if (Test-LocalTcpPort 3080) {
    throw 'Port 3080 is already in use; refusing to reuse an unknown app server.'
}

$portProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$portProbe.Start()
$mongoPort = $portProbe.LocalEndpoint.Port
$portProbe.Stop()

$mongoDir = Join-Path $env:TEMP ('juristai-auth-mongo-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $mongoDir -Force | Out-Null
$mongoLog = Join-Path $mongoDir 'mongod.log'
$mongoArgs = "--dbpath `"$mongoDir`" --bind_ip 127.0.0.1 --port $mongoPort --logpath `"$mongoLog`""
$mongo = $null
$testExit = 1

try {
    $mongo = Start-Process -FilePath $mongoExe -ArgumentList $mongoArgs -WindowStyle Hidden -PassThru
    $mongoReady = $false
    for ($attempt = 0; $attempt -lt 45; $attempt++) {
        Start-Sleep -Seconds 1
        if ($mongo.HasExited) { break }
        if (Test-LocalTcpPort $mongoPort) {
            $mongoReady = $true
            break
        }
    }
    if (-not $mongoReady) {
        if (Test-Path -LiteralPath $mongoLog) { Get-Content -LiteralPath $mongoLog -Tail 50 }
        throw "The isolated MongoDB process did not listen on port $mongoPort."
    }

    $env:Path = "$nodeBin;$env:Path"
    $env:E2E_USE_MEMORY_MONGO = 'false'
    $env:MONGO_URI = "mongodb://127.0.0.1:$mongoPort/LibreChat-e2e"
    $env:E2E_CHROMIUM_CHANNEL = 'msedge'
    Push-Location $repoRoot
    try {
        & $nodeExe node_modules/playwright/cli.js test --config=e2e/playwright.config.auth-local.ts e2e/specs/mock/auth.spec.ts --workers=1 @PlaywrightArgs
        $testExit = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}
finally {
    if ($mongo -and -not $mongo.HasExited) {
        Stop-Process -Id $mongo.Id -Force
        try { Wait-Process -Id $mongo.Id -Timeout 15 -ErrorAction SilentlyContinue } catch { }
    }

    $mongoClosed = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (-not (Test-LocalTcpPort $mongoPort)) {
            $mongoClosed = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if ($mongoClosed -and (Test-Path -LiteralPath $mongoDir)) {
        for ($attempt = 0; $attempt -lt 5; $attempt++) {
            try {
                Remove-Item -LiteralPath $mongoDir -Recurse -Force -ErrorAction Stop
                break
            }
            catch {
                if ($attempt -eq 4) { throw }
                Start-Sleep -Seconds 1
            }
        }
    }
    elseif (Test-Path -LiteralPath $mongoDir) {
        Write-Warning "Port $mongoPort is still open; preserved the isolated database at $mongoDir."
    }

    if (Test-LocalTcpPort 3080) {
        throw 'The auth E2E app server is still listening on port 3080 after Playwright teardown.'
    }
}

if ($testExit -ne 0) {
    exit $testExit
}
