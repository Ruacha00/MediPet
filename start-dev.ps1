<#
.SYNOPSIS
Starts the MediPet API, Web application, and optionally a Compose-managed PostgreSQL.

.DESCRIPTION
Synchronizes locked dependencies, applies migrations, runs the idempotent fictional
development seed, waits for the API and Web application to become live, and then
supervises both processes in one terminal.

.EXAMPLE
.\start-dev.ps1

Starts a Compose-managed development database because no database URL was supplied.

.EXAMPLE
.\start-dev.ps1 -DatabaseUrl 'postgresql://medipet:password@localhost/medipet' -OpenBrowser

Uses an existing PostgreSQL database and opens the Web application after startup.
#>
[CmdletBinding()]
param(
    [string]$DatabaseUrl = $env:MEDIPET_DATABASE_URL,
    [string]$ManagementToken = $env:MEDIPET_MANAGEMENT_TOKEN,
    [ValidateRange(1, 65535)]
    [int]$ApiPort = 8000,
    [ValidateRange(1, 65535)]
    [int]$WebPort = 3000,
    [ValidateRange(1, 65535)]
    [int]$DatabasePort = 5432,
    [ValidateRange(5, 600)]
    [int]$StartupTimeoutSeconds = 60,
    [switch]$SkipMigrations,
    [switch]$SkipSeed,
    [switch]$StopDatabaseOnExit,
    [switch]$OpenBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$utf8Encoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8Encoding
$OutputEncoding = $utf8Encoding

$repositoryRoot = $PSScriptRoot
$apiDirectory = Join-Path $repositoryRoot "apps\api"
$webDirectory = Join-Path $repositoryRoot "apps\web"
$apiEnvironmentFile = Join-Path $apiDirectory ".env"
$composeFile = Join-Path $repositoryRoot "infra\compose.yaml"
$composeProjectName = "medipet-dev"
$managedDatabase = [string]::IsNullOrWhiteSpace($DatabaseUrl)
$managedDatabaseShutdownRequired = $false
$dockerCommand = $null
$apiService = $null
$webService = $null

if ($ApiPort -eq $WebPort) {
    throw "API and Web ports must be different."
}
if ($managedDatabase -and $DatabasePort -eq $ApiPort) {
    throw "PostgreSQL and API ports must be different. Pass -DatabasePort or -ApiPort with another port."
}
if ($managedDatabase -and $DatabasePort -eq $WebPort) {
    throw "PostgreSQL and Web ports must be different. Pass -DatabasePort or -WebPort with another port."
}

function Get-RequiredCommand {
    param(
        [Parameter(Mandatory)]
        [string]$Name,
        [Parameter(Mandatory)]
        [string]$InstallHint
    )

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "$Name was not found on PATH. $InstallHint"
    }
    return $command
}

function Get-ListeningProcess {
    param(
        [Parameter(Mandatory)]
        [int]$Port
    )

    try {
        $connectionParameters = @{
            LocalPort = $Port
            State = "Listen"
            ErrorAction = "Stop"
        }
        $connection = Get-NetTCPConnection @connectionParameters | Select-Object -First 1
    }
    catch {
        return $null
    }

    if ($null -eq $connection) {
        return $null
    }

    $processName = "unknown process"
    try {
        $processName = (Get-Process -Id $connection.OwningProcess -ErrorAction Stop).ProcessName
    }
    catch {
        # The process can disappear between the connection and process queries.
    }

    return [pscustomobject]@{
        Id = $connection.OwningProcess
        Name = $processName
    }
}

function Assert-PortAvailable {
    param(
        [Parameter(Mandatory)]
        [int]$Port,
        [Parameter(Mandatory)]
        [string]$Purpose,
        [Parameter(Mandatory)]
        [string]$OverrideParameter
    )

    $owner = Get-ListeningProcess -Port $Port
    if ($null -ne $owner) {
        throw "$Purpose port $Port is already used by $($owner.Name) (PID $($owner.Id)). Stop that process or pass $OverrideParameter with another port."
    }
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory)]
        [string]$FilePath,
        [Parameter(Mandatory)]
        [string[]]$Arguments,
        [Parameter(Mandatory)]
        [string]$WorkingDirectory,
        [Parameter(Mandatory)]
        [string]$Description
    )

    Write-Host "==> $Description"
    Push-Location $WorkingDirectory
    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Description failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}

function Test-ManagedDatabaseRunning {
    param(
        [Parameter(Mandatory)]
        [string]$DockerExecutable
    )

    Push-Location $repositoryRoot
    try {
        $services = @(& $DockerExecutable compose --project-name $composeProjectName --file $composeFile ps --status running --services 2>$null)
        if ($LASTEXITCODE -ne 0) {
            return $false
        }
        return ($services -contains "postgres")
    }
    finally {
        Pop-Location
    }
}

function Get-ManagedDatabasePublishedPort {
    param(
        [Parameter(Mandatory)]
        [string]$DockerExecutable
    )

    Push-Location $repositoryRoot
    try {
        $bindings = @(& $DockerExecutable compose --project-name $composeProjectName --file $composeFile port postgres 5432 2>$null)
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to inspect the running Compose-managed PostgreSQL port."
        }

        $ports = @(
            foreach ($binding in $bindings) {
                if ([string]$binding -notmatch ":(\d+)$") {
                    throw "Unexpected PostgreSQL port binding reported by Docker: $binding"
                }
                [int]$Matches[1]
            }
        ) | Sort-Object -Unique
        if ($ports.Count -ne 1) {
            throw "Expected one published PostgreSQL port, but Docker reported: $($bindings -join ', ')"
        }
        return [int]$ports[0]
    }
    finally {
        Pop-Location
    }
}

function Test-HttpEndpoint {
    param(
        [Parameter(Mandatory)]
        [string]$Uri
    )

    $response = $null
    try {
        $request = [System.Net.HttpWebRequest]::Create($Uri)
        $request.Method = "GET"
        $request.Timeout = 2000
        $request.ReadWriteTimeout = 2000
        $request.AllowAutoRedirect = $true
        $response = $request.GetResponse()
        $statusCode = [int]$response.StatusCode
        return $statusCode -ge 200 -and $statusCode -lt 400
    }
    catch {
        return $false
    }
    finally {
        if ($null -ne $response) {
            $response.Close()
        }
    }
}

function Receive-ServiceOutput {
    param(
        [Parameter(Mandatory)]
        [hashtable]$Service
    )

    $items = @(Receive-Job -Job $Service.Job -ErrorAction SilentlyContinue)
    foreach ($item in $items) {
        $text = [string]$item
        if ($text -match "^__MEDIPET_SUPERVISOR_PID=(\d+)$") {
            $Service.ProcessId = [int]$Matches[1]
            continue
        }
        foreach ($line in @($text -split [Environment]::NewLine)) {
            if (-not [string]::IsNullOrWhiteSpace($line)) {
                Write-Host "[$($Service.Prefix)] $line"
            }
        }
    }
}

function Assert-ServiceRunning {
    param(
        [Parameter(Mandatory)]
        [hashtable]$Service
    )

    if ($Service.Job.State -in @("Completed", "Failed", "Stopped", "Disconnected")) {
        Receive-ServiceOutput -Service $Service
        $reason = $Service.Job.ChildJobs[0].JobStateInfo.Reason
        if ($null -ne $reason) {
            throw "$($Service.Prefix) service stopped during startup: $($reason.Message)"
        }
        throw "$($Service.Prefix) service stopped during startup."
    }
}

function Wait-ForApplications {
    param(
        [Parameter(Mandatory)]
        [hashtable]$Api,
        [Parameter(Mandatory)]
        [hashtable]$Web,
        [Parameter(Mandatory)]
        [string]$ApiHealthUri,
        [Parameter(Mandatory)]
        [string]$WebUri,
        [Parameter(Mandatory)]
        [int]$TimeoutSeconds
    )

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $apiLive = $false
    $webLive = $false

    while ($stopwatch.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        Receive-ServiceOutput -Service $Api
        Receive-ServiceOutput -Service $Web
        Assert-ServiceRunning -Service $Api
        Assert-ServiceRunning -Service $Web

        if (-not $apiLive) {
            $apiLive = Test-HttpEndpoint -Uri $ApiHealthUri
        }
        if (-not $webLive) {
            $webLive = Test-HttpEndpoint -Uri $WebUri
        }
        if ($apiLive -and $webLive) {
            return
        }
        Start-Sleep -Milliseconds 500
    }

    $missing = @()
    if (-not $apiLive) { $missing += "API" }
    if (-not $webLive) { $missing += "Web" }
    throw "Startup timed out after $TimeoutSeconds seconds while waiting for $($missing -join ' and ')."
}

function Stop-ServiceJobTree {
    param(
        [hashtable]$Service
    )

    if ($null -eq $Service) {
        return
    }

    Receive-ServiceOutput -Service $Service
    if ($Service.Job.State -eq "Running" -and $null -ne $Service.ProcessId) {
        $taskkill = Get-Command "taskkill.exe" -ErrorAction SilentlyContinue
        if ($null -ne $taskkill) {
            & $taskkill.Source /PID $Service.ProcessId /T /F 2>$null | Out-Null
        }
    }
    Stop-Job -Job $Service.Job -ErrorAction SilentlyContinue
    Receive-ServiceOutput -Service $Service
    Remove-Job -Job $Service.Job -Force -ErrorAction SilentlyContinue
}

function Start-ServiceJob {
    param(
        [Parameter(Mandatory)]
        [string]$Name,
        [Parameter(Mandatory)]
        [string]$WorkingDirectory,
        [Parameter(Mandatory)]
        [string]$Executable,
        [Parameter(Mandatory)]
        [string[]]$Arguments,
        [Parameter(Mandatory)]
        [hashtable]$Environment
    )

    $configuration = @{
        WorkingDirectory = $WorkingDirectory
        Executable = $Executable
        Arguments = $Arguments
        Environment = $Environment
    }
    return Start-Job -Name $Name -ScriptBlock {
        param($Configuration)
        $jobEncoding = [System.Text.UTF8Encoding]::new($false)
        [Console]::OutputEncoding = $jobEncoding
        $OutputEncoding = $jobEncoding
        foreach ($entry in $Configuration.Environment.GetEnumerator()) {
            [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
        }
        Set-Location $Configuration.WorkingDirectory
        Write-Output "__MEDIPET_SUPERVISOR_PID=$PID"
        $commandArguments = @($Configuration.Arguments)
        & $Configuration.Executable @commandArguments 2>&1
        exit $LASTEXITCODE
    } -ArgumentList $configuration
}

$environmentNames = @(
    "MEDIPET_ENVIRONMENT",
    "MEDIPET_DATABASE_URL",
    "MEDIPET_WEB_ORIGIN",
    "MEDIPET_MANAGEMENT_TOKEN",
    "MEDIPET_POSTGRES_PORT",
    "NEXT_PUBLIC_MEDIPET_API_URL",
    "PYTHONPATH"
)
$originalEnvironment = @{}
foreach ($name in $environmentNames) {
    $originalEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

try {
    Assert-PortAvailable -Port $ApiPort -Purpose "API" -OverrideParameter "-ApiPort"
    Assert-PortAvailable -Port $WebPort -Purpose "Web" -OverrideParameter "-WebPort"

    $uvCommand = Get-RequiredCommand -Name "uv" -InstallHint "Install uv before starting MediPet."
    $nodeCommand = Get-RequiredCommand -Name "node" -InstallHint "Install Node.js 22 before starting MediPet."
    $corepackCommand = Get-RequiredCommand -Name "corepack" -InstallHint "Install Node.js 22 with Corepack before starting MediPet."

    $nodeVersion = [string](& $nodeCommand.Source --version)
    if ($LASTEXITCODE -ne 0 -or $nodeVersion -notmatch "^v(\d+)") {
        throw "Unable to determine the installed Node.js version. Install Node.js 22."
    }
    if ([int]$Matches[1] -ne 22) {
        throw "MediPet requires Node.js 22; found $nodeVersion."
    }

    if ($managedDatabase) {
        if (-not (Test-Path -LiteralPath $composeFile)) {
            throw "Missing Compose configuration: $composeFile"
        }
        $dockerCommand = Get-RequiredCommand -Name "docker" -InstallHint "Install and start Docker Desktop, or pass -DatabaseUrl for an external PostgreSQL database."
        $dockerInfo = @(& $dockerCommand.Source info --format "{{.ServerVersion}}" 2>&1)
        if ($LASTEXITCODE -ne 0) {
            throw "Docker is installed but its engine is unavailable. Start Docker Desktop, or pass -DatabaseUrl for an external PostgreSQL database."
        }

        $env:MEDIPET_POSTGRES_PORT = [string]$DatabasePort
        $managedDatabaseRunning = Test-ManagedDatabaseRunning -DockerExecutable $dockerCommand.Source
        if ($managedDatabaseRunning) {
            $publishedDatabasePort = Get-ManagedDatabasePublishedPort -DockerExecutable $dockerCommand.Source
            if ($publishedDatabasePort -ne $DatabasePort) {
                Assert-PortAvailable -Port $DatabasePort -Purpose "PostgreSQL" -OverrideParameter "-DatabasePort"
            }
        }
        else {
            Assert-PortAvailable -Port $DatabasePort -Purpose "PostgreSQL" -OverrideParameter "-DatabasePort"
        }
        $composeArguments = @(
            "compose",
            "--project-name", $composeProjectName,
            "--file", $composeFile,
            "up", "--detach", "--wait",
            "--wait-timeout", [string]$StartupTimeoutSeconds,
            "postgres"
        )
        $managedDatabaseShutdownRequired = [bool]$StopDatabaseOnExit
        Invoke-CheckedCommand -FilePath $dockerCommand.Source -Arguments $composeArguments -WorkingDirectory $repositoryRoot -Description "Starting the Compose-managed development PostgreSQL"
        $DatabaseUrl = "postgresql://medipet:medipet-development-only@127.0.0.1:$DatabasePort/medipet"
    }
    elseif ($StopDatabaseOnExit) {
        Write-Warning "-StopDatabaseOnExit is ignored because an external database URL was supplied."
    }

    Invoke-CheckedCommand -FilePath $uvCommand.Source -Arguments @("sync", "--frozen") -WorkingDirectory $apiDirectory -Description "Synchronizing locked API dependencies"
    Invoke-CheckedCommand -FilePath $corepackCommand.Source -Arguments @("pnpm", "install", "--frozen-lockfile") -WorkingDirectory $webDirectory -Description "Synchronizing locked Web dependencies"

    $env:MEDIPET_ENVIRONMENT = "development"
    $env:MEDIPET_DATABASE_URL = $DatabaseUrl
    $env:MEDIPET_WEB_ORIGIN = "http://localhost:$WebPort"
    $env:NEXT_PUBLIC_MEDIPET_API_URL = "http://localhost:$ApiPort"
    $env:PYTHONPATH = Join-Path $apiDirectory "src"
    if (-not [string]::IsNullOrWhiteSpace($ManagementToken)) {
        $env:MEDIPET_MANAGEMENT_TOKEN = $ManagementToken
    }

    if (-not $SkipMigrations) {
        Invoke-CheckedCommand -FilePath $uvCommand.Source -Arguments @("run", "alembic", "upgrade", "head") -WorkingDirectory $apiDirectory -Description "Applying database migrations"
    }
    else {
        Write-Host "==> Skipping database migrations; the existing schema is unchanged."
    }

    if (-not $SkipSeed) {
        Invoke-CheckedCommand -FilePath $uvCommand.Source -Arguments @("run", "python", "-m", "medipet.persistence.seed") -WorkingDirectory $apiDirectory -Description "Seeding fictional development data and capabilities"
    }
    else {
        Write-Host "==> Skipping the development seed; existing data and capability state are unchanged."
    }

    $modelProcessVariables = @(
        "MEDIPET_LLM_BASE_URL",
        "MEDIPET_LLM_API_KEY",
        "MEDIPET_LLM_MODEL"
    )
    $hasProcessModelConfig = ($modelProcessVariables | Where-Object {
        [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($_, "Process"))
    }).Count -eq 0
    if (-not (Test-Path -LiteralPath $apiEnvironmentFile) -and -not $hasProcessModelConfig) {
        Write-Warning "Model configuration is missing. The applications will start, but /ready and chat requests will remain unavailable. Copy apps/api/.env.example to apps/api/.env and configure the model to become ready."
    }

    $childEnvironment = @{
        MEDIPET_ENVIRONMENT = $env:MEDIPET_ENVIRONMENT
        MEDIPET_DATABASE_URL = $env:MEDIPET_DATABASE_URL
        MEDIPET_WEB_ORIGIN = $env:MEDIPET_WEB_ORIGIN
        NEXT_PUBLIC_MEDIPET_API_URL = $env:NEXT_PUBLIC_MEDIPET_API_URL
        PYTHONPATH = $env:PYTHONPATH
    }
    if (-not [string]::IsNullOrWhiteSpace($ManagementToken)) {
        $childEnvironment["MEDIPET_MANAGEMENT_TOKEN"] = $ManagementToken
    }

    $apiJob = Start-ServiceJob `
        -Name "medipet-api-$PID" `
        -WorkingDirectory $apiDirectory `
        -Executable $uvCommand.Source `
        -Arguments @("run", "uvicorn", "medipet.delivery.http:app", "--app-dir", "src", "--reload", "--port", [string]$ApiPort) `
        -Environment $childEnvironment
    $apiService = @{
        Job = $apiJob
        Prefix = "API"
        ProcessId = $null
    }

    $webJob = Start-ServiceJob `
        -Name "medipet-web-$PID" `
        -WorkingDirectory $webDirectory `
        -Executable $corepackCommand.Source `
        -Arguments @("pnpm", "run", "dev", "--port", [string]$WebPort) `
        -Environment $childEnvironment
    $webService = @{
        Job = $webJob
        Prefix = "WEB"
        ProcessId = $null
    }

    $apiBaseUri = "http://localhost:$ApiPort"
    $webUri = "http://localhost:$WebPort"
    Wait-ForApplications -Api $apiService -Web $webService -ApiHealthUri "$apiBaseUri/health" -WebUri $webUri -TimeoutSeconds $StartupTimeoutSeconds

    Write-Host "MediPet API is live: $apiBaseUri"
    Write-Host "MediPet Web is live: $webUri"
    if (Test-HttpEndpoint -Uri "$apiBaseUri/ready") {
        Write-Host "MediPet API is ready for chat."
    }
    else {
        Write-Warning "MediPet API is live but not ready. Check $apiBaseUri/ready and configure the model or database as indicated."
    }

    if ($OpenBrowser) {
        Start-Process $webUri
    }
    Write-Host "Press Ctrl+C to stop API and Web."

    while ($apiService.Job.State -eq "Running" -and $webService.Job.State -eq "Running") {
        Receive-ServiceOutput -Service $apiService
        Receive-ServiceOutput -Service $webService
        Start-Sleep -Milliseconds 500
    }

    Receive-ServiceOutput -Service $apiService
    Receive-ServiceOutput -Service $webService
    if ($apiService.Job.State -ne "Running") {
        throw "The API process stopped unexpectedly."
    }
    throw "The Web process stopped unexpectedly."
}
finally {
    Stop-ServiceJobTree -Service $apiService
    Stop-ServiceJobTree -Service $webService
    if ($null -ne $apiService -or $null -ne $webService) {
        Write-Host "MediPet API and Web stopped."
    }

    if ($managedDatabaseShutdownRequired) {
        try {
            $stopArguments = @(
                "compose",
                "--project-name", $composeProjectName,
                "--file", $composeFile,
                "stop", "postgres"
            )
            Invoke-CheckedCommand -FilePath $dockerCommand.Source -Arguments $stopArguments -WorkingDirectory $repositoryRoot -Description "Stopping the Compose-managed development PostgreSQL"
        }
        catch {
            Write-Warning "Unable to stop the Compose-managed PostgreSQL: $($_.Exception.Message)"
        }
    }

    foreach ($name in $environmentNames) {
        $originalValue = $originalEnvironment[$name]
        if ($null -eq $originalValue) {
            [Environment]::SetEnvironmentVariable($name, $null, "Process")
        }
        else {
            [Environment]::SetEnvironmentVariable($name, $originalValue, "Process")
        }
    }
}
