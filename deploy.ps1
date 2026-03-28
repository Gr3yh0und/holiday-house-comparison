$ConfigFile = "$PSScriptRoot\deploy.config"
$LocalFile  = "$PSScriptRoot\public\index.html"

if (-not (Test-Path $ConfigFile)) {
    Write-Error "deploy.config not found. Copy deploy.config.template to deploy.config and fill in your credentials."
    exit 1
}

if (-not (Test-Path $LocalFile)) {
    Write-Error "public\index.html not found. Run 'python app.py' first."
    exit 1
}

# Parse key=value config file
$config = @{}
Get-Content $ConfigFile | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
        $config[$matches[1].Trim()] = $matches[2].Trim()
    }
}

$ftpUrl = "ftp://$($config['FTP_HOST'])$($config['FTP_REMOTE_PATH'])/index.html"
Write-Host "Deploying public\index.html to $("ftp://$($config['FTP_HOST'])$($config['FTP_REMOTE_PATH'])/") ..."

curl.exe --silent --show-error `
    --ftp-create-dirs `
    -T $LocalFile `
    $ftpUrl `
    --user "$($config['FTP_USER']):$($config['FTP_PASS'])"

if ($LASTEXITCODE -ne 0) {
    Write-Error "Deployment failed."
    exit $LASTEXITCODE
}

Write-Host "Done."
