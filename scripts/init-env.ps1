# Creates .env from .env.example (if needed), adds settings that newer versions introduced
# (e.g. the time-series database password) and replaces every CHANGE_ME value with a
# generated secret. Existing non-placeholder values are kept. Works with Windows PowerShell 5.1
# and PowerShell 7.
#
#   powershell -ExecutionPolicy Bypass -File scripts\init-env.ps1
#
# The generated sign-in passwords are printed once; they are also stored in .env.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root '.env'
$example = Join-Path $root '.env.example'

if (-not (Test-Path $envFile)) {
    if (-not (Test-Path $example)) { throw ".env.example not found in $root" }
    Copy-Item $example $envFile
    Write-Host "Created .env from .env.example"
}

# append keys that exist in .env.example but not yet in .env (upgrades keep working)
$present = @{}
foreach ($l in [System.IO.File]::ReadAllLines($envFile)) {
    if ($l -match '^\s*([A-Z0-9_]+)\s*=') { $present[$Matches[1]] = $true }
}
$missing = @()
foreach ($l in [System.IO.File]::ReadAllLines($example)) {
    if ($l -match '^\s*([A-Z0-9_]+)\s*=' -and -not $present.ContainsKey($Matches[1])) { $missing += $l }
}
if ($missing.Count -gt 0) {
    $current = [System.IO.File]::ReadAllText($envFile)
    if ($current.Length -gt 0 -and -not $current.EndsWith("`n")) { $current += "`n" }
    $current += "`n# --- added by init-env (new in this version) ---`n" + (($missing -join "`n") + "`n")
    [System.IO.File]::WriteAllText($envFile, $current, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Added new settings to .env: $((($missing | ForEach-Object { ($_ -split '=')[0] }) -join ', '))"
}

$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()

function New-RandomBytes([int]$count) {
    $bytes = New-Object byte[] $count
    $rng.GetBytes($bytes)
    return $bytes
}

function New-Alnum([int]$length) {
    $alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    while ($true) {
        $sb = New-Object System.Text.StringBuilder
        while ($sb.Length -lt $length) {
            foreach ($b in (New-RandomBytes 64)) {
                if ($b -lt 248 -and $sb.Length -lt $length) { [void]$sb.Append($alphabet[$b % 62]) }
            }
        }
        $s = $sb.ToString()
        # the password policy needs three character classes
        if ($s -cmatch '[a-z]' -and $s -cmatch '[A-Z]' -and $s -match '[0-9]') { return $s }
    }
}

$generated = @{}
$lines = [System.IO.File]::ReadAllLines($envFile)
$out = foreach ($line in $lines) {
    if ($line -match '^\s*([A-Z0-9_]+)\s*=(.*)$' -and $Matches[2] -like '*CHANGE_ME*') {
        $name = $Matches[1]
        if ($name -eq 'DBM_MASTER_KEY') {
            $value = [Convert]::ToBase64String((New-RandomBytes 32))
        } elseif ($name -eq 'POSTGRES_PASSWORD') {
            $value = New-Alnum 32
        } else {
            $value = New-Alnum 20
        }
        $generated[$name] = $value
        "$name=$value"
    } else {
        $line
    }
}

# UTF-8 without BOM, LF line endings (what docker compose expects)
$text = ($out -join "`n") + "`n"
[System.IO.File]::WriteAllText($envFile, $text, (New-Object System.Text.UTF8Encoding($false)))

if ($generated.Count -eq 0) {
    Write-Host ".env has no CHANGE_ME values left; nothing changed."
    exit 0
}

Write-Host ""
Write-Host "Filled $($generated.Count) value(s) in .env: $(($generated.Keys | Sort-Object) -join ', ')"
if ($generated.ContainsKey('DBM_BOOTSTRAP_ADMIN_PASSWORD')) {
    Write-Host "  Sign in as admin with:        $($generated['DBM_BOOTSTRAP_ADMIN_PASSWORD'])"
}
if ($generated.ContainsKey('DBM_SAMPLE_MONITOR_PASSWORD')) {
    Write-Host "  dbm_monitor target password:  $($generated['DBM_SAMPLE_MONITOR_PASSWORD'])"
}
Write-Host ""
Write-Host "Back up DBM_MASTER_KEY separately: stored database credentials cannot be decrypted without it."
if ($generated.ContainsKey('POSTGRES_PASSWORD')) {
    Write-Host ""
    Write-Host "If you already started the stack once with the placeholder values, recreate the database"
    Write-Host "volume so the new password applies (this deletes DB Manager's own data):"
    Write-Host "  docker compose down -v"
}
Write-Host "Then start:  docker compose up --build"
