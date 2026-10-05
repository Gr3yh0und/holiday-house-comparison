param(
    [switch]$Rollback,
    [int]$Steps = 1,
    [ValidateSet('ftp')]  # the homelab local target is published by /deploy local, not this script
    [string]$Target = 'ftp'
)

$ConfigFile = "$PSScriptRoot\deploy.config"
$LocalFile  = "$PSScriptRoot\public\index.html"
$AuthDir    = "$PSScriptRoot\deploy_auth"
$AuthStateFile  = "$AuthDir\.auth_state"
$AuthSecretFile = "$AuthDir\auth_secret.php"
$AuthCookieSeconds = 30 * 24 * 60 * 60

# Same rollback mechanism as deploy.ps1 (see there for the full explanation),
# kept in its own "test" release line so a test rollback can never touch prod.
$ReleasesDir = "$PSScriptRoot\releases\test"
$KeepReleases = 5

function Get-Releases {
    if (-not (Test-Path $ReleasesDir)) { return @() }
    Get-ChildItem -Path $ReleasesDir -Directory | Sort-Object Name -Descending
}

function Save-ReleaseSnapshot([string]$ContentFile, [string]$PageName) {
    New-Item -ItemType Directory -Force -Path $ReleasesDir | Out-Null
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')

    # Sequence number is the sort key, not the timestamp alone -- see
    # deploy.ps1 for the full explanation.
    $nextSeq = 1
    foreach ($existing in (Get-Releases)) {
        $existingSeq = ($existing.Name -split '-', 2)[0]
        if ($existingSeq -match '^\d+$' -and ([int]$existingSeq + 1) -gt $nextSeq) {
            $nextSeq = [int]$existingSeq + 1
        }
    }
    $releaseDir = Join-Path $ReleasesDir ("{0:D6}-{1}" -f $nextSeq, $stamp)
    New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
    Copy-Item $ContentFile (Join-Path $releaseDir $PageName)

    $releases = Get-Releases
    if ($releases.Count -gt $KeepReleases) {
        $releases | Select-Object -Skip $KeepReleases | Remove-Item -Recurse -Force
    }
}

if (-not (Test-Path $ConfigFile)) {
    Write-Error "deploy.config not found. Copy deploy.config.template to deploy.config and fill in your credentials."
    exit 1
}

$config = @{}
Get-Content $ConfigFile | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
        $config[$matches[1].Trim()] = $matches[2].Trim()
    }
}

$requiredKeys = @('SITE_PASSWORD', 'FTP_HOST', 'FTP_USER', 'FTP_PASS', 'FTP_REMOTE_PATH')
foreach ($key in $requiredKeys) {
    if (-not $config.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($config[$key])) {
        Write-Error "$ConfigFile is missing key: $key"
        exit 1
    }
}
if ($config['SITE_PASSWORD'] -ceq 'change-me') {
    Write-Error "$ConfigFile's SITE_PASSWORD is still the template placeholder -- set a real passphrase."
    exit 1
}

$remoteBase = "ftp://$($config['FTP_HOST'])$($config['FTP_REMOTE_PATH'])"

function Send-File([string]$Src, [string]$Name) {
    curl.exe --silent --show-error `
        --ftp-create-dirs `
        -T $Src `
        "$remoteBase/$Name" `
        --user "$($config['FTP_USER']):$($config['FTP_PASS'])"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Deployment failed uploading $Name."
        exit $LASTEXITCODE
    }
}

# Best-effort delete of a stale remote file -- see deploy.ps1 for why. Ignore
# failures: the file may already be gone, and a missing DELE target must not
# abort an otherwise-successful deploy.
function Remove-RemoteFile([string]$Name) {
    curl.exe --silent --show-error `
        --user "$($config['FTP_USER']):$($config['FTP_PASS'])" `
        -Q "DELE $($config['FTP_REMOTE_PATH'])/$Name" `
        "ftp://$($config['FTP_HOST'])/" 2>$null | Out-Null
}

# health-test.json (not health.json -- prod and test share the same remote
# path, differentiated only by filename) is deliberately
# unauthenticated, deployed plain, never through the PHP gate. See deploy.ps1
# for the WEBAPP_PROJECT_STANDARD.md §6a reference.
function Publish-HealthBestEffort {
    $healthFile = "$PSScriptRoot\public\health.json"
    if (-not (Test-Path $healthFile)) { return }
    curl.exe --silent --show-error --ftp-create-dirs -T $healthFile `
        "$remoteBase/health-test.json" --user "$($config['FTP_USER']):$($config['FTP_PASS'])" 2>$null | Out-Null
}

if ($Rollback) {
    $releases = Get-Releases
    if ($releases.Count -le $Steps) {
        Write-Error "Only $($releases.Count) release(s) saved locally under $ReleasesDir -- cannot go back $Steps."
        exit 1
    }
    $target = $releases[$Steps]
    $page = Join-Path $target.FullName "index-test.php"
    if (-not (Test-Path $page)) {
        Write-Error "$($target.FullName) has no saved page -- nothing to roll back to."
        exit 1
    }
    Write-Host "Rolling back to release $($target.Name) ..."
    Send-File $page "index-test.php"
    Write-Host "Done. Test site now serving release $($target.Name)."
    Write-Host "Note: only the page is restored -- auth gate files are untouched (they don't change per-release)."
    exit 0
}

# ── Build ────────────────────────────────────────────────────────────────────
# See deploy.ps1 for why this always builds fresh instead of trusting a
# leftover public\index.html.
$pythonBin = "python"
if (-not (Get-Command $pythonBin -ErrorAction SilentlyContinue)) { $pythonBin = "python3" }
Write-Host "Building site from cache\houses.json (running $pythonBin app.py --from-cache) ..."
& $pythonBin app.py --from-cache
if ($LASTEXITCODE -ne 0) {
    Write-Error "Site build failed -- aborting deploy."
    Publish-HealthBestEffort
    exit $LASTEXITCODE
}

if (-not (Test-Path $LocalFile)) {
    Write-Error "public\index.html not found after build."
    exit 1
}

New-Item -ItemType Directory -Force -Path $AuthDir | Out-Null

function New-RandomHex([int]$Bytes) {
    $buf = New-Object byte[] $Bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($buf)
    -join ($buf | ForEach-Object { $_.ToString('x2') })
}

function Get-Sha256Hex([string]$Text) {
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $bytes = $sha256.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Text))
    -join ($bytes | ForEach-Object { $_.ToString('x2') })
}

$pwFingerprint = Get-Sha256Hex $config['SITE_PASSWORD']

if (Test-Path $AuthStateFile) {
    $authState = @{}
    Get-Content $AuthStateFile | ForEach-Object {
        if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
            $authState[$matches[1].Trim()] = $matches[2].Trim()
        }
    }
    $authSalt = $authState['AUTH_SALT']
    $authCookieSecret = $authState['AUTH_COOKIE_SECRET']
    $storedFingerprint = $authState['STORED_PW_FINGERPRINT']
} else {
    $authSalt = New-RandomHex 16
    $authCookieSecret = New-RandomHex 32
    $storedFingerprint = ''
}

if ($storedFingerprint -cne $pwFingerprint) {
    if ($storedFingerprint) {
        Write-Host "SITE_PASSWORD changed -- rotating the auth cookie secret to invalidate existing logins."
    }
    $authCookieSecret = New-RandomHex 32
}
"AUTH_SALT=$authSalt`nAUTH_COOKIE_SECRET=$authCookieSecret`nSTORED_PW_FINGERPRINT=$pwFingerprint`n" |
    Set-Content -NoNewline $AuthStateFile

$pwHash = Get-Sha256Hex "$authSalt$($config['SITE_PASSWORD'])"

@"
<?php
// Generated by deploy-test.ps1 from $ConfigFile's SITE_PASSWORD -- do not edit,
// do not commit. Re-run deploy.ps1/deploy-test.ps1 after changing SITE_PASSWORD.
define('SITE_PASSWORD_SALT', '$authSalt');
define('SITE_PASSWORD_HASH', '$pwHash');
define('AUTH_COOKIE_NAME', 'hhc_auth');
define('AUTH_COOKIE_SECRET', '$authCookieSecret');
define('AUTH_COOKIE_SECONDS', $AuthCookieSeconds);
"@ | Set-Content -NoNewline $AuthSecretFile

$gatedPage = New-TemporaryFile
"<?php require __DIR__ . '/_auth_gate.php'; ?>`n" + (Get-Content $LocalFile -Raw) | Set-Content -NoNewline $gatedPage

Write-Host "Deploying test build to $remoteBase/index-test.php ..."

Send-File $gatedPage "index-test.php"
Send-File "$AuthDir\_auth_gate.php" "_auth_gate.php"
Send-File $AuthSecretFile "auth_secret.php"
Send-File "$AuthDir\login.php" "login.php"
Send-File "$AuthDir\robots.txt" "robots.txt"
Send-File "$PSScriptRoot\public\health.json" "health-test.json"

# Remove the unprotected index-test.html every pre-gate deploy left
# behind -- see deploy.ps1 for why this matters.
Remove-RemoteFile "index-test.html"

# Snapshot the exact gated bytes just uploaded -- not public\index.html.
Save-ReleaseSnapshot $gatedPage "index-test.php"

Remove-Item $gatedPage -ErrorAction SilentlyContinue
Write-Host "Done."
