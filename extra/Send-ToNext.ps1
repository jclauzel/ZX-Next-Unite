<#
  Send-ToNext.ps1 — push a build to a real ZX Spectrum Next, from VS Code.

  Pairs with extra/nextdev.txt (the Next-side loop). The cycle:

    1. The Next boots into nextdev (autoexec.bas) and hands itself to
       either ZX Next Remote flavour (httpbridge or n2n — both carry the
       NextSync Listener), whose Listener dials out to Unite's Remote
       Explorer listen server (port 2048) and waits. This script never
       talks to the Next directly: it drives that Listener session
       through Unite's HTTP bridge.
    2. This script polls /status every 2 s until that Next appears.
    3. It PUTs the file, then VERIFIES it: /sum gives the 16-bit additive
       checksum and byte count of the file as it landed, which is compared
       against the same sum computed here. A transfer is only ever called
       a success when those match.
    4. /forceexit tells the Next to leave listen mode and end the
       application; it soft-resets, boots, finds the pushed file waiting
       and runs it. Total: save in VS Code, run the task, watch it on
       real hardware.

  EXIT CODES, so a VS Code task (or CI) can branch on the outcome:
      0  sent and VERIFIED on the Next
      1  configuration problem (missing/……invalid .cfg, missing file)
      2  bridge refused the token (HTTP 401)
      3  the send itself failed (bridge/Next reported an error)
      4  sent, but VERIFICATION FAILED — the bytes on the Next differ
      5  timed out waiting for a Next, or interrupted

  Usage:
      .\Send-ToNext.ps1                        # uses Send-ToNext.cfg beside this script
      .\Send-ToNext.ps1 -Config my.cfg
      .\Send-ToNext.ps1 -File build\game.nex   # override the cfg's file for one run
      .\Send-ToNext.ps1 -TimeoutSeconds 120    # give up waiting after 2 minutes

  The first run writes a commented Send-ToNext.cfg and stops, so there is
  something to edit rather than a wall of switches to guess at.
#>
[CmdletBinding()]
param(
    [string]$Config = "",
    [string]$File = "",
    [int]$TimeoutSeconds = 0
)

$ErrorActionPreference = "Stop"

if (-not $Config) {
    $Config = Join-Path $PSScriptRoot "Send-ToNext.cfg"
}

# --- the sample cfg, written once so the first run has something to edit --
$SAMPLE = @'
# Send-ToNext.cfg - settings for extra\Send-ToNext.ps1
#
# key = value, '#' starts a comment. Every key below is optional except
# bridge_ip and file.

# Where ZX-Next-Unite's NextSync HTTP bridge is listening. This is the PC
# running Unite - NOT the Next. Port 80 is the bridge default (Settings ->
# "Enable NextSync HTTP bridge").
bridge_ip   = 127.0.0.1
bridge_port = 80

# Optional shared secret. Set this ONLY if Unite's Settings has
# "Require bearer token" enabled - paste the same token here. Leave it
# empty (or delete the line) for an unprotected bridge.
token       =

# The build to push. Relative paths resolve against this .cfg's folder,
# so a repo-local "build\game.nex" travels with the project.
file        = build\game.nex

# Where it lands on the Next. nextdev.txt expects /dev/incoming.nex - it
# moves that file aside and runs it after the Next resets.
remote_path = /dev/incoming.nex

# After a verified send, tell the Next to leave listen mode and exit its
# application, so nextdev's loop runs the file. "no" leaves the Next in
# the Listener (handy while pushing several files in a row).
forceexit_after_send = yes

# How long to wait for a Next to appear on the bridge, in seconds.
# 0 = wait for ever (Ctrl-C stops).
wait_timeout = 0
'@

if (-not (Test-Path -LiteralPath $Config)) {
    Set-Content -LiteralPath $Config -Value $SAMPLE -Encoding UTF8
    Write-Host "Created $Config" -ForegroundColor Yellow
    Write-Host "Edit it (bridge_ip and file at least), then run this again." -ForegroundColor Yellow
    exit 1
}

# --- read the cfg: key = value, '#' comments, blanks ignored --------------
$cfg = @{}
foreach ($line in Get-Content -LiteralPath $Config) {
    $t = $line.Trim()
    if (-not $t -or $t.StartsWith("#")) { continue }
    $eq = $t.IndexOf("=")
    if ($eq -lt 1) { continue }
    $cfg[$t.Substring(0, $eq).Trim().ToLower()] = $t.Substring($eq + 1).Trim()
}

function Get-Cfg([string]$key, [string]$default = "") {
    if ($cfg.ContainsKey($key) -and $cfg[$key]) { return $cfg[$key] }
    return $default
}
function Test-Yes([string]$v) {
    return @("1", "yes", "true", "on") -contains $v.ToLower()
}

$bridgeIp   = Get-Cfg "bridge_ip"
$bridgePort = Get-Cfg "bridge_port" "80"
$token      = Get-Cfg "token"
$remotePath = Get-Cfg "remote_path" "/dev/incoming.nex"
$doExit     = Test-Yes (Get-Cfg "forceexit_after_send" "yes")
if ($TimeoutSeconds -le 0) {
    $TimeoutSeconds = [int](Get-Cfg "wait_timeout" "0")
}

if (-not $File) { $File = Get-Cfg "file" }
if (-not $bridgeIp) {
    Write-Host "FAILED: no bridge_ip in $Config" -ForegroundColor Red
    exit 1
}
if (-not $File) {
    Write-Host "FAILED: no file to send (set 'file' in $Config, or pass -File)" -ForegroundColor Red
    exit 1
}
# Relative paths resolve against the .cfg, so a repo-local build path works
# wherever the task is launched from.
if (-not [System.IO.Path]::IsPathRooted($File)) {
    $File = Join-Path (Split-Path -Parent (Resolve-Path -LiteralPath $Config)) $File
}
if (-not (Test-Path -LiteralPath $File -PathType Leaf)) {
    Write-Host "FAILED: file not found: $File" -ForegroundColor Red
    exit 1
}

$base = "http://${bridgeIp}:${bridgePort}"
$headers = @{}
if ($token) { $headers["ZXNEXTUNITE-BRIDGE-TOKEN"] = $token }

# --- one place that talks HTTP, so 401 is named once ---------------------
function Invoke-Bridge {
    param(
        [string]$Path,
        [string]$Method = "Get",
        [byte[]]$Body = $null,
        [int]$TimeoutSec = 60
    )
    $uri = "$base$Path"
    try {
        if ($null -ne $Body) {
            return Invoke-WebRequest -Uri $uri -Method $Method -Body $Body `
                -ContentType "application/octet-stream" -Headers $headers `
                -TimeoutSec $TimeoutSec -UseBasicParsing
        }
        return Invoke-WebRequest -Uri $uri -Method $Method -Headers $headers `
            -TimeoutSec $TimeoutSec -UseBasicParsing
    } catch {
        $resp = $_.Exception.Response
        $code = 0
        if ($resp -and $resp.StatusCode) { $code = [int]$resp.StatusCode }
        if ($code -eq 401) {
            Write-Host "FAILED: the bridge refused the token (HTTP 401)." -ForegroundColor Red
            if ($token) {
                Write-Host "  The 'token' in $Config does not match Unite's Settings -> Require bearer token." -ForegroundColor Red
            } else {
                Write-Host "  The bridge requires a token; copy it from Unite's Settings into $Config." -ForegroundColor Red
            }
            exit 2
        }
        throw
    }
}

# --- 1. wait for a Next --------------------------------------------------
Write-Host "Bridge   : $base"
Write-Host "File     : $File"
Write-Host "Remote   : $remotePath"
Write-Host ""
Write-Host "Waiting for a Next to connect to the bridge..." -ForegroundColor Cyan

$started = Get-Date
$waited = $false
while ($true) {
    $ok = $false
    try {
        $st = (Invoke-Bridge "/status?json=1" -TimeoutSec 15).Content | ConvertFrom-Json
        if ($st.connected) { $ok = $true }
        elseif (-not $st.listening) {
            Write-Host "  bridge is up but NOT listening - start the Remote Explorer server in Unite." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  no answer from $base ($($_.Exception.Message))" -ForegroundColor DarkGray
    }
    if ($ok) { break }
    if ($TimeoutSeconds -gt 0 -and ((Get-Date) - $started).TotalSeconds -ge $TimeoutSeconds) {
        Write-Host "FAILED: no Next connected within $TimeoutSeconds s." -ForegroundColor Red
        exit 5
    }
    $waited = $true
    Start-Sleep -Seconds 2
}
if ($waited) { Write-Host "" }
Write-Host "Next connected." -ForegroundColor Green
# More than one Next seated? The push rides the ACTIVE seat - the first
# machine that connected, not necessarily the one just booted into
# nextdev. Warn loudly rather than landing a build on the wrong Next.
# ($st.sessions only exists on roster hosts; absent compares false.)
if ($st.sessions -gt 1) {
    Write-Host ("WARNING: {0} Nexts are connected; the push goes to the ACTIVE seat ({1})." `
        -f $st.sessions, $st.active) -ForegroundColor Yellow
}

# --- 2. send -------------------------------------------------------------
$bytes = [System.IO.File]::ReadAllBytes($File)
# The bridge's /sum is the same 16-bit additive checksum, so computing it
# here gives an end-to-end comparison rather than a hopeful HTTP 200.
$localSum = 0
foreach ($b in $bytes) { $localSum += $b }
$localSum = $localSum -band 0xFFFF

$name = Split-Path -Leaf $File
Write-Host ("Sending {0} ({1:N0} bytes)..." -f $name, $bytes.Length) -ForegroundColor Cyan
$enc = [uri]::EscapeDataString($remotePath)
try {
    $put = Invoke-Bridge "/put?path=$enc" -Method Post -Body $bytes -TimeoutSec 600
} catch {
    Write-Host "FAILED: the send did not complete - $($_.Exception.Message)" -ForegroundColor Red
    exit 3
}
if ($put.StatusCode -ne 200) {
    Write-Host "FAILED: bridge answered HTTP $($put.StatusCode): $($put.Content)" -ForegroundColor Red
    exit 3
}

# --- 3. verify: the bytes ON THE NEXT, not the bytes we hoped to send ----
Write-Host "Verifying on the Next..." -ForegroundColor Cyan
$remoteSum = -1
$remoteLen = -1
try {
    $sum = ((Invoke-Bridge "/sum?path=$enc&json=1" -TimeoutSec 300).Content | ConvertFrom-Json)
    $remoteSum = [int]$sum.sum16
    $remoteLen = [int]$sum.bytes
} catch {
    Write-Host "WARNING: could not read back the checksum - $($_.Exception.Message)" -ForegroundColor Yellow
}

if ($remoteSum -lt 0) {
    Write-Host "FAILED: sent, but the Next could not be asked to verify it." -ForegroundColor Red
    exit 4
}
if ($remoteLen -ne $bytes.Length -or $remoteSum -ne $localSum) {
    Write-Host "FAILED: VERIFICATION MISMATCH - the file on the Next is not what was sent." -ForegroundColor Red
    Write-Host ("  here : {0,8:N0} bytes  checksum 0x{1:X4}" -f $bytes.Length, $localSum) -ForegroundColor Red
    Write-Host ("  Next : {0,8:N0} bytes  checksum 0x{1:X4}" -f $remoteLen, $remoteSum) -ForegroundColor Red
    exit 4
}

Write-Host ("SENT AND VERIFIED: {0} -> {1}" -f $name, $remotePath) -ForegroundColor Green
Write-Host ("  {0:N0} bytes, checksum 0x{1:X4} - matches on both sides." -f $bytes.Length, $localSum) -ForegroundColor Green

# --- 4. hand the machine back to the loop -------------------------------
if ($doExit) {
    Write-Host "Telling the Next to exit and run it..." -ForegroundColor Cyan
    try {
        Invoke-Bridge "/forceexit" -TimeoutSec 30 | Out-Null
        Write-Host "Done - the Next is restarting into your build." -ForegroundColor Green
    } catch {
        # The push itself is already verified, so this is not a send failure.
        Write-Host "WARNING: the file is on the Next, but /forceexit failed - $($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host "  Press BREAK on the Next to leave the Listener and run it." -ForegroundColor Yellow
    }
}
exit 0
