#Requires -RunAsAdministrator
# Installs Python dependencies system-wide so the Windows service (SYSTEM account) can import them.

$ErrorActionPreference = 'Stop'
$RepoDir = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $RepoDir 'logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
Start-Transcript -Path (Join-Path $LogDir 'install-deps.log') -Force | Out-Null
trap { Write-Host "ERROR: $_"; Stop-Transcript | Out-Null; exit 1 }

$Python = 'C:\Python314\python.exe'
# Install system-wide so the service (LocalSystem) can import. --ignore-installed
# bypasses the "already satisfied" check that pip makes against the admin user's
# user-site packages.
& $Python -m pip install --ignore-installed --no-user -r (Join-Path $RepoDir 'requirements.txt')

# Verify the system-wide install by running python with user-site disabled.
# pillow_heif is verified explicitly because a user-site copy will satisfy the
# Python import seen here but be invisible to the LocalSystem service.
$env:PYTHONNOUSERSITE = '1'
& $Python -c "import flask, pillow_heif, os; pillow_heif.register_heif_opener(); print('flask:', os.path.dirname(flask.__file__)); print('pillow_heif:', os.path.dirname(pillow_heif.__file__))"
Remove-Item Env:PYTHONNOUSERSITE

Stop-Transcript | Out-Null
