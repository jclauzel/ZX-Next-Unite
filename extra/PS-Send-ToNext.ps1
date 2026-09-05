<#
  PS-Send-ToNext.ps1 - push a build to a real ZX Spectrum Next, from VS Code.

  The same job as Send-ToNext.ps1 (same Send-ToNext.cfg, same exit codes,
  same push -> verify -> forceexit cycle - see that script and
  extra/README.md for the Next-side setup), rebuilt on the ZxNextRemote
  PowerShell module (extra/ZxNextRemote/): all HTTP goes through the typed
  client, so token refusals, OS-protection refusals, lost sessions and
  unreachable bridges are told apart by ZxNextRemoteError.Reason instead of
  by parsing HTTP bodies. VS Code integration: PowerShell/vscode-sample/
  and the walkthrough in PowerShell/PowerShellHelperClass.md.

  The verify step asks the Next for the CRC-32 of the file it holds
  (/crc, computed ON the Next - 8 hex digits come back, not the file) and
  compares it with the CRC-32 of the bytes that were pushed. A ZX Next
  Remote older than 1.0.8 (or a .sync5 dot older than 5.9.2) does not know
  the op; the script then falls back to Send-ToNext.ps1's size + 16-bit
  checksum read-back (/sum), so the verdict is real either way.

  NEW: the -autoexec switch manages the Next-side loop file
  (c:/nextzxos/autoexec.bas <-> autoexec_.bas, the parked name) without
  pulling the SD card:

      -autoexec:On      autoexec_.bas exists  -> rename to autoexec.bas
                        (the loop runs at every boot again)
      -autoexec:Off     autoexec.bas exists   -> rename to autoexec_.bas
                        (the machine boots normally, loop parked)
      -autoexec:Deploy  autoexec.bas missing  -> send extra/autoexec.bas
                        into c:/nextzxos/ (verified - the module's
                        Verify(): CRC-32 on the Next, or the /sum
                        read-back); an existing one is never overwritten

  -autoexec on its own does ONLY that and exits - it does not also push a
  build. To do both in one run, add -AndSend (push the cfg's file) or pass
  -File explicitly. Without -autoexec the script behaves exactly like
  Send-ToNext.ps1.

  EXIT CODES (same contract as Send-ToNext.ps1):
      0  sent and VERIFIED on the Next / autoexec action done
      1  configuration problem (missing/invalid .cfg, missing file)
      2  bridge refused the token (HTTP 401)
      3  the operation failed (bridge/Next reported an error, incl. a
         write refused by the remote machine's OS protection)
      4  sent, but VERIFICATION FAILED - the bytes on the Next differ
      5  timed out waiting for a Next (wait_timeout / -TimeoutSeconds)

  Usage:
      .\PS-Send-ToNext.ps1                     # push, like Send-ToNext.ps1
      .\PS-Send-ToNext.ps1 -File build\game.nex
      .\PS-Send-ToNext.ps1 -autoexec:Deploy    # install the loop file
      .\PS-Send-ToNext.ps1 -autoexec:On        # re-arm a parked loop
      .\PS-Send-ToNext.ps1 -autoexec:Off       # park the loop
      .\PS-Send-ToNext.ps1 -autoexec:Deploy -AndSend   # both in one run
#>
[Diagnostics.CodeAnalysis.SuppressMessageAttribute(
    'PSAvoidUsingWriteHost', '',
    Justification = 'interactive console script: the coloured report is the product (same convention as Send-ToNext.ps1); PS5+ Write-Host is the capturable information stream')]
[CmdletBinding()]
param(
    [string]$Config = "",
    [string]$File = "",
    [int]$TimeoutSeconds = 0,
    [ValidateSet('On', 'Off', 'Deploy')]
    [string]$autoexec = "",
    [switch]$AndSend
)

$ErrorActionPreference = "Stop"

Import-Module (Join-Path $PSScriptRoot "ZxNextRemote\ZxNextRemote.psd1") -Force

# The module's typed failure reason, or '' for any other exception.
function Get-ZxReason($err) {
    $p = $err.Exception.PSObject.Properties['Reason']
    if ($p) { return [string]$p.Value }
    return ''
}

# Named exits for the two refusals every stage can hit. Anything else is the
# caller's to shape into its own stage-specific message.
function Exit-OnRefusal($err, [string]$doing) {
    switch (Get-ZxReason $err) {
        'TokenRequired' {
            Write-Host "FAILED: the bridge refused the token (HTTP 401)." -ForegroundColor Red
            Write-Host "  Match the 'token' in the .cfg with Unite's Settings -> Require bearer token." -ForegroundColor Red
            exit 2
        }
        'OsProtected' {
            Write-Host "FAILED: $doing was refused by the remote machine's OS protection." -ForegroundColor Red
            Write-Host "  The far side is ZX Next Remote guarding that folder - adjust its" -ForegroundColor Red
            Write-Host "  'OS protection' setting (or its folder list) ON THAT MACHINE." -ForegroundColor Red
            exit 3
        }
    }
}

if (-not $Config) {
    $Config = Join-Path $PSScriptRoot "Send-ToNext.cfg"
}

# --- the sample cfg, written once so the first run has something to edit --
# (Shared with Send-ToNext.ps1: same file, same keys.)
$SAMPLE = @'
# Send-ToNext.cfg - settings for extra\Send-ToNext.ps1 / PS-Send-ToNext.ps1
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

# Where it lands on the Next. autoexec.txt expects /home/incoming.nex - it
# moves that file aside and runs it after the Next resets.
#
# This DEFAULT moved from /dev/ to /home/ (9.6.3): /dev never existed on a
# stock NextZXOS card, so it had to be created by hand before the loop would
# work at all, while /home ships with the OS. If you already have a working
# setup, the two halves must agree: either delete the remote_path line below
# to take the new default and point the Next-side loop at /home, or keep
# writing /dev/incoming.nex here and leave the card as it is. A PC writing to
# one folder while the Next watches the other fails silently - the push
# succeeds and nothing ever runs.
remote_path = /home/incoming.nex

# After a verified send, tell the Next to leave listen mode and exit its
# application, so the Next-side loop runs the file. "no" leaves the Next in
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
$bridgePort = [int](Get-Cfg "bridge_port" "80")
$token      = Get-Cfg "token"
$remotePath = Get-Cfg "remote_path" "/home/incoming.nex"
$doExit     = Test-Yes (Get-Cfg "forceexit_after_send" "yes")
if ($TimeoutSeconds -le 0) {
    $TimeoutSeconds = [int](Get-Cfg "wait_timeout" "0")
}

# -autoexec alone manages only the loop file; a push happens when the run
# also names a file (-File) or asks for the cfg's one (-AndSend). Without
# -autoexec this is exactly the classic push.
$doSend = (-not $autoexec) -or $AndSend -or ($File -ne "")

if ($doSend) {
    if (-not $File) { $File = Get-Cfg "file" }
    if (-not $File) {
        Write-Host "FAILED: no file to send (set 'file' in $Config, or pass -File)" -ForegroundColor Red
        exit 1
    }
    # Relative paths resolve against the .cfg, so a repo-local build path
    # works wherever the task is launched from.
    if (-not [System.IO.Path]::IsPathRooted($File)) {
        $File = Join-Path (Split-Path -Parent (Resolve-Path -LiteralPath $Config)) $File
    }
    if (-not (Test-Path -LiteralPath $File -PathType Leaf)) {
        Write-Host "FAILED: file not found: $File" -ForegroundColor Red
        exit 1
    }
}
if (-not $bridgeIp) {
    Write-Host "FAILED: no bridge_ip in $Config" -ForegroundColor Red
    exit 1
}

$con = New-ZxNextRemoteConnection -IpAddress $bridgeIp -Port $bridgePort -Token $token
$remote = New-ZxNextRemote -Connection $con
# The wait loop polls with ITS OWN short-timeout connection, so a black-hole
# address costs ~10 s per poll instead of the module's 60 s quick-verb
# default - keeping -TimeoutSeconds honest to within one poll.
$pollCon = New-ZxNextRemoteConnection -IpAddress $bridgeIp -Port $bridgePort -Token $token -TimeoutSec 10

# --- 1. wait for a Next --------------------------------------------------
Write-Host "Bridge   : $($con.BaseUrl())"
if ($doSend)  { Write-Host "File     : $File" }
if ($doSend)  { Write-Host "Remote   : $remotePath" }
if ($autoexec) { Write-Host "Autoexec : $autoexec" }
Write-Host ""
Write-Host "Waiting for a Next to connect to the bridge..." -ForegroundColor Cyan

$started = Get-Date
$st = $null
while ($true) {
    $st = $null
    try {
        $st = Test-ZxNextRemoteBridge $pollCon   # $null while unreachable
    } catch {
        Exit-OnRefusal $_ "/status"          # a wrong token must not poll for ever
        # Anything else is transient from the loop's seat (DNS hiccup, the
        # bridge restarting): report it and keep polling - the timeout
        # guard below bounds the wait when one is set.
        Write-Host "  no answer from $($con.BaseUrl()) ($($_.Exception.Message))" -ForegroundColor DarkGray
    }
    if ($null -ne $st -and $st.Connected) { break }
    if ($null -ne $st -and -not $st.Listening) {
        Write-Host "  bridge is up but NOT listening - start the Remote Explorer server in Unite." -ForegroundColor Yellow
    } elseif ($null -eq $st) {
        Write-Host "  no answer from $($con.BaseUrl())" -ForegroundColor DarkGray
    }
    if ($TimeoutSeconds -gt 0 -and ((Get-Date) - $started).TotalSeconds -ge $TimeoutSeconds) {
        Write-Host "FAILED: no Next connected within $TimeoutSeconds s." -ForegroundColor Red
        exit 5
    }
    Start-Sleep -Seconds 2
}
Write-Host "Next connected." -ForegroundColor Green
# More than one Next seated? Everything below rides the ACTIVE seat - the
# first machine that connected, not necessarily the one just booted into
# the loop. Warn loudly rather than landing a build on the wrong Next.
if ($null -ne $st.Sessions -and $st.Sessions -gt 1) {
    Write-Host ("WARNING: {0} Nexts are connected; this run drives the ACTIVE seat ({1})." `
        -f $st.Sessions, $st.Active) -ForegroundColor Yellow
    Write-Host "  (target a specific one from your own script: see ManageSession in the module help)" -ForegroundColor Yellow
}

$session = $remote.ManageSession()   # the active seat, like Send-ToNext.ps1

# --- 2. the -autoexec action, before any push ----------------------------
if ($autoexec) {
    $dir = "c:/nextzxos"
    $live = "$dir/autoexec.bas"
    $parked = "$dir/autoexec_.bas"
    Write-Host "Autoexec ${autoexec}: checking $dir on the Next..." -ForegroundColor Cyan
    try {
        $names = @($session.Ls($dir) | ForEach-Object { $_.Name.ToLower() })
        $hasLive = $names -contains "autoexec.bas"
        $hasParked = $names -contains "autoexec_.bas"
        switch ($autoexec) {
            'On' {
                if ($hasLive) {
                    Write-Host "Autoexec is already On (autoexec.bas present)." -ForegroundColor Green
                } elseif ($hasParked) {
                    [void]$session.Ren($parked, $live)
                    Write-Host "Autoexec On: renamed autoexec_.bas -> autoexec.bas." -ForegroundColor Green
                } else {
                    Write-Host "FAILED: neither autoexec.bas nor autoexec_.bas exists in $dir." -ForegroundColor Red
                    Write-Host "  Install the loop first with -autoexec:Deploy." -ForegroundColor Red
                    exit 3
                }
            }
            'Off' {
                if (-not $hasLive) {
                    Write-Host "Autoexec is already Off (no autoexec.bas)." -ForegroundColor Green
                } elseif ($hasParked) {
                    # Renaming onto an existing file fails on the Next, and
                    # silently deleting either copy is not this script's call.
                    Write-Host "FAILED: both autoexec.bas and autoexec_.bas exist in $dir." -ForegroundColor Red
                    Write-Host "  Remove one of them first (the parked autoexec_.bas is usually the stale one)." -ForegroundColor Red
                    exit 3
                } else {
                    [void]$session.Ren($live, $parked)
                    Write-Host "Autoexec Off: renamed autoexec.bas -> autoexec_.bas." -ForegroundColor Green
                }
            }
            'Deploy' {
                if ($hasLive) {
                    Write-Host "Autoexec already deployed (autoexec.bas present) - nothing sent." -ForegroundColor Green
                } else {
                    $local = Join-Path $PSScriptRoot "autoexec.bas"
                    if (-not (Test-Path -LiteralPath $local -PathType Leaf)) {
                        Write-Host "FAILED: $local not found (it ships in extra/; vendor it next to this script)." -ForegroundColor Red
                        exit 1
                    }
                    [void]$session.Put($local, $live)
                    if (-not $session.Verify($local, $live)) {
                        Write-Host "FAILED: autoexec.bas was sent but did not verify on the Next." -ForegroundColor Red
                        exit 4
                    }
                    Write-Host "Autoexec deployed: autoexec.bas -> $dir (verified)." -ForegroundColor Green
                    if ($hasParked) {
                        # The parked copy is THIS script's own artifact
                        # (-autoexec:Off makes it); a fresh Deploy supersedes
                        # it, and leaving it behind would make the next
                        # -autoexec:Off refuse (both files exist).
                        [void]$session.Rm($parked)
                        Write-Host "Removed the stale parked autoexec_.bas (superseded by this Deploy)." -ForegroundColor Green
                    }
                }
            }
        }
    } catch {
        Exit-OnRefusal $_ "the autoexec $autoexec action"
        Write-Host "FAILED: the autoexec $autoexec action - $($_.Exception.Message)" -ForegroundColor Red
        exit 3
    }
    if (-not $doSend) { exit 0 }
    Write-Host ""
}

# --- 3. send -------------------------------------------------------------
# The build is read ONCE and those bytes are pushed and verified: a rebuild
# landing between the Put and the read-back can then never fake a mismatch
# (Verify($File, ...) would re-read the changed file and exit 4).
$name = Split-Path -Leaf $File
$bytes = [System.IO.File]::ReadAllBytes($File)
Write-Host ("Sending {0} ({1:N0} bytes)..." -f $name, $bytes.Length) -ForegroundColor Cyan
try {
    [void]$session.Put($bytes, $remotePath)
} catch {
    Exit-OnRefusal $_ "the send"
    Write-Host "FAILED: the send did not complete - $($_.Exception.Message)" -ForegroundColor Red
    exit 3
}

# --- 4. verify: the bytes ON THE NEXT, not the bytes we hoped to send ----
# The CRC-32 is computed ON the Next (/crc): 8 hex digits come back instead
# of the whole file, so a big build verifies in seconds. A listener that
# predates the op (ZX Next Remote < 1.0.8, .sync5 dot < 5.9.2) answers
# NextRefused; the size + sum16 read-back (/sum) then takes over, so an
# older Next still gets a real verdict - just a slower one. Anything else
# going wrong (bridge gone, token, timeout) is exit 4 as before.
Write-Host "Verifying on the Next..." -ForegroundColor Cyan
$localCrc = $session.LocalCrc32($bytes)
$remoteCrc = $null
try {
    $remoteCrc = $session.Crc($remotePath).Crc32
} catch {
    $why = Get-ZxReason $_
    if ($why -ne 'NextRefused' -and $why -ne 'Unsupported') {
        Write-Host "WARNING: could not ask the Next for the CRC-32 - $($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host "FAILED: sent, but the Next could not be asked to verify it." -ForegroundColor Red
        exit 4
    }
    # A 502 from /crc has two meanings, told apart by the bridge's own text
    # (the error's Detail): a listener that predates the crc op, or a file
    # the Next could not open for hashing. Either way /sum settles it below.
    $detail = ''
    $dp = $_.Exception.PSObject.Properties['Detail']
    if ($dp) { $detail = [string]$dp.Value }
    if ($detail -match 'predates') {
        Write-Host "  (this Next's listener predates the crc op - reading the checksum back instead)" -ForegroundColor DarkGray
    } else {
        Write-Host ("  (the Next could not compute the CRC-32: {0} - reading the checksum back instead)" -f $detail) -ForegroundColor DarkGray
    }
}
if ($null -ne $remoteCrc) {
    if ($remoteCrc -ne $localCrc) {
        Write-Host "FAILED: VERIFICATION MISMATCH - the file on the Next is not what was sent." -ForegroundColor Red
        Write-Host ("  here : {0,8:N0} bytes  CRC-32 {1}" -f $bytes.Length, $localCrc) -ForegroundColor Red
        Write-Host ("  Next :                 CRC-32 {0}" -f $remoteCrc) -ForegroundColor Red
        exit 4
    }
    Write-Host ("SENT AND VERIFIED: {0} -> {1}  (CRC-32 {2}, computed on the Next)" -f $name, $remotePath, $remoteCrc) -ForegroundColor Green
} else {
    $localSum = 0
    foreach ($b in $bytes) { $localSum += $b }
    $localSum = $localSum -band 0xFFFF
    $remoteSum = $null
    try {
        $remoteSum = $session.Sum($remotePath)
    } catch {
        Write-Host "WARNING: could not read back the checksum - $($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host "FAILED: sent, but the Next could not be asked to verify it." -ForegroundColor Red
        exit 4
    }
    if ($remoteSum.Bytes -ne $bytes.Length -or $remoteSum.Sum16 -ne $localSum) {
        Write-Host "FAILED: VERIFICATION MISMATCH - the file on the Next is not what was sent." -ForegroundColor Red
        Write-Host ("  here : {0,8:N0} bytes  checksum 0x{1:X4}" -f $bytes.Length, $localSum) -ForegroundColor Red
        Write-Host ("  Next : {0,8:N0} bytes  checksum 0x{1:X4}" -f $remoteSum.Bytes, $remoteSum.Sum16) -ForegroundColor Red
        exit 4
    }
    Write-Host ("SENT AND VERIFIED: {0} -> {1}  (size + checksum 0x{2:X4}, read back)" -f $name, $remotePath, $localSum) -ForegroundColor Green
}

# --- 5. hand the machine back to the loop -------------------------------
if ($doExit) {
    Write-Host "Telling the Next to exit and run it..." -ForegroundColor Cyan
    try {
        $remote.ForceExit()
        Write-Host "Done - the Next is restarting into your build." -ForegroundColor Green
    } catch {
        # The push itself is already verified, so this is not a send failure.
        Write-Host "WARNING: the file is on the Next, but /forceexit failed - $($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host "  Press BREAK on the Next to leave the Listener and run it." -ForegroundColor Yellow
    }
}
exit 0
