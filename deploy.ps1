param(
    [switch]$Rollback,
    [int]$Steps = 1
)

$ConfigFile = "$PSScriptRoot\deploy.config"
$LocalFile  = "$PSScriptRoot\public\index.html"
$AuthDir    = "$PSScriptRoot\deploy_auth"
$AuthStateFile  = "$AuthDir\.auth_state"
$AuthSecretFile = "$AuthDir\auth_secret.php"
$AuthCookieSeconds = 30 * 24 * 60 * 60

# ── Rollback ─────────────────────────────────────────────────────────────────
# Every deploy snapshots the exact gated bytes it uploads (the .php page after
# the auth-gate prefix, not the pre-gate public\index.html) so a rollback can
# never republish a page missing its auth check. FTP has no atomic flip, so
# this re-uploads a known-good snapshot verbatim rather than rebuilding.
$ReleasesDir = "$PSScriptRoot\releases\prod"
$KeepReleases = 5

function Get-Releases {
    if (-not (Test-Path $ReleasesDir)) { return @() }
    Get-ChildItem -Path $ReleasesDir -Directory | Sort-Object Name -Descending
}

function Save-ReleaseSnapshot([string]$ContentFile, [string]$PageName) {
    New-Item -ItemType Directory -Force -Path $ReleasesDir | Out-Null
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')

    # The sort key is a monotonic sequence number, not the timestamp alone --
    # see deploy.sh for the full explanation of why disambiguating by "does
    # this directory already exist" breaks once an earlier same-second
    # directory has been pruned away (its name becomes free again, sorts
    # *before* every suffixed name as a plain string, and a later deploy
    # reusing it would look older than it is and get pruned as the oldest
    # release instead of the newest one).
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

# Parse key=value config file
$config = @{}
Get-Content $ConfigFile | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
        $config[$matches[1].Trim()] = $matches[2].Trim()
    }
}

foreach ($key in @('FTP_HOST', 'FTP_USER', 'FTP_PASS', 'FTP_REMOTE_PATH', 'SITE_PASSWORD')) {
    if (-not $config.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($config[$key])) {
        Write-Error "$ConfigFile is missing key: $key"
        exit 1
    }
}
if ($config['SITE_PASSWORD'] -eq 'change-me') {
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

if ($Rollback) {
    $releases = Get-Releases
    if ($releases.Count -le $Steps) {
        Write-Error "Only $($releases.Count) release(s) saved locally under $ReleasesDir -- cannot go back $Steps."
        exit 1
    }
    $target = $releases[$Steps]
    $page = Join-Path $target.FullName "index.php"
    if (-not (Test-Path $page)) {
        Write-Error "$($target.FullName) has no saved page -- nothing to roll back to."
        exit 1
    }
    Write-Host "Rolling back to release $($target.Name) ..."
    Send-File $page "index.php"
    Write-Host "Done. Live site now serving release $($target.Name)."
    Write-Host "Note: only the page is restored -- auth gate files are untouched (they don't change per-release)."
    exit 0
}

if (-not (Test-Path $LocalFile)) {
    Write-Error "public\index.html not found. Run 'python app.py' first."
    exit 1
}

# ── Auth gate ────────────────────────────────────────────────────────────────
# Same mechanism as deploy.sh (see there for the full explanation): the
# plaintext SITE_PASSWORD never leaves this process, only a salted SHA-256
# hash is written to auth_secret.php. Salt and cookie secret persist across
# deploys in .auth_state so re-deploying doesn't invalidate saved logins.
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

if (Test-Path $AuthStateFile) {
    $authState = @{}
    Get-Content $AuthStateFile | ForEach-Object {
        if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
            $authState[$matches[1].Trim()] = $matches[2].Trim()
        }
    }
    $authSalt = $authState['AUTH_SALT']
    $authCookieSecret = $authState['AUTH_COOKIE_SECRET']
} else {
    $authSalt = New-RandomHex 16
    $authCookieSecret = New-RandomHex 32
    "AUTH_SALT=$authSalt`nAUTH_COOKIE_SECRET=$authCookieSecret`n" | Set-Content -NoNewline $AuthStateFile
}

$pwHash = Get-Sha256Hex "$authSalt$($config['SITE_PASSWORD'])"

@"
<?php
// Generated by deploy.ps1 from $ConfigFile's SITE_PASSWORD -- do not edit,
// do not commit. Re-run deploy.ps1 after changing SITE_PASSWORD to update it.
define('SITE_PASSWORD_SALT', '$authSalt');
define('SITE_PASSWORD_HASH', '$pwHash');
define('AUTH_COOKIE_NAME', 'hhc_auth');
define('AUTH_COOKIE_SECRET', '$authCookieSecret');
define('AUTH_COOKIE_SECONDS', $AuthCookieSeconds);
"@ | Set-Content -NoNewline $AuthSecretFile

# Pages deploy as .php, never .html -- so the gate runs before any content is sent.
$gatedPage = New-TemporaryFile
"<?php require __DIR__ . '/_auth_gate.php'; ?>`n" + (Get-Content $LocalFile -Raw) | Set-Content -NoNewline $gatedPage

Write-Host "Deploying to $remoteBase/ ..."

Send-File $gatedPage "index.php"
Send-File "$AuthDir\_auth_gate.php" "_auth_gate.php"
Send-File $AuthSecretFile "auth_secret.php"
Send-File "$AuthDir\login.php" "login.php"
Send-File "$AuthDir\robots.txt" "robots.txt"

# Snapshot the exact gated bytes just uploaded -- not public\index.html, which
# has no auth gate and isn't what's actually live.
Save-ReleaseSnapshot $gatedPage "index.php"

Remove-Item $gatedPage -ErrorAction SilentlyContinue
Write-Host "Done."
