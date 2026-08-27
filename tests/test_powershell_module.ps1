<#
  Driver for tests/test_powershell_module.py - exercises the ZxNextRemote
  PowerShell module against REAL NextSyncHttpBridge instances the python
  wrapper starts (plain multi-session / bearer-token / OS-protected /
  no-Next). Prints PASS/FAIL lines; exit 0 only when everything passed.

  Runs unchanged under Windows PowerShell 5.1 and pwsh 7 - the wrapper
  launches it under every shell it finds.
#>
[Diagnostics.CodeAnalysis.SuppressMessageAttribute(
    'PSAvoidUsingWriteHost', '',
    Justification = 'test driver: PASS/FAIL console lines are the report')]
[Diagnostics.CodeAnalysis.SuppressMessageAttribute(
    'PSAvoidUsingPositionalParameters', '',
    Justification = 'Check name cond detail is the suite-wide compact test idiom')]
param(
    [Parameter(Mandatory)] [string]$ModulePath,
    [Parameter(Mandatory)] [int]$PlainPort,
    [Parameter(Mandatory)] [int]$TokenPort,
    [Parameter(Mandatory)] [int]$OspPort,
    [Parameter(Mandatory)] [int]$DeadPort,
    [Parameter(Mandatory)] [string]$Token,
    [Parameter(Mandatory)] [string]$TmpDir
)

$ErrorActionPreference = 'Stop'
$script:failures = 0

function Check([string]$name, [bool]$cond, $detail = '') {
    if ($cond) {
        Write-Host "PASS  $name"
    } else {
        $script:failures++
        Write-Host "FAIL  $name  $detail"
    }
}

function Get-Reason($err) {
    $p = $err.Exception.PSObject.Properties['Reason']
    if ($p) { return [string]$p.Value }
    return ''
}

# Runs a scriptblock that MUST throw; returns the ZxNextRemoteError reason.
function Get-ThrownReason([scriptblock]$sb) {
    try {
        & $sb | Out-Null
        return '(no error)'
    } catch {
        return Get-Reason $_
    }
}

Import-Module $ModulePath -Force

# ---- module surface ------------------------------------------------------
$cmds = Get-Command -Module ZxNextRemote
Check 'exports the three functions' `
    (@($cmds | Where-Object CommandType -eq 'Function').Count -eq 3) `
    ($cmds.Name -join ',')
Check 'exports the Connect-ZxNextRemote alias' `
    ($null -ne (Get-Command Connect-ZxNextRemote -ErrorAction SilentlyContinue))
foreach ($fn in 'New-ZxNextRemoteConnection', 'New-ZxNextRemote', 'Test-ZxNextRemoteBridge') {
    $h = Get-Help $fn
    Check "Get-Help $fn has a synopsis and examples" `
        ($h.Synopsis.Trim().Length -gt 10 -and $null -ne $h.examples) $h.Synopsis
}
Check 'about_ZxNextRemote help topic resolves' `
    ($null -ne (Get-Help about_ZxNextRemote -ErrorAction SilentlyContinue))

# ---- classes via `using module` (the documented advanced path) -----------
$sb = [scriptblock]::Create(
    "using module '$ModulePath'; [ZxNextRemote]::new([ZxNextRemoteConnection]::new('127.0.0.1', $PlainPort)).Connection.BaseUrl()")
Check 'using module: direct class instantiation' `
    ((& $sb) -eq "http://127.0.0.1:$PlainPort")

# ---- connection / reachability ------------------------------------------
$con = New-ZxNextRemoteConnection -IpAddress 127.0.0.1 -Port $PlainPort
$deadCon = New-ZxNextRemoteConnection -IpAddress 127.0.0.1 -Port $DeadPort
Check 'Test-ZxNextRemoteBridge: $null on a dead port' `
    ($null -eq (Test-ZxNextRemoteBridge $deadCon))
$st = Test-ZxNextRemoteBridge $con
Check 'Test-ZxNextRemoteBridge: status on a live bridge' `
    ($null -ne $st -and $st.Connected -and $st.Listening) ($st | Out-String)

$remote = New-ZxNextRemote -Connection $con
Check 'Remote.Test() true' ($remote.Test())
$st = $remote.Status()
Check 'Status(): typed fields' `
    ($st.Current -eq 'C' -and $st.Partitions -eq 2 -and $st.Sessions -eq 2 -and $st.Active -eq 1) `
    ($st | Out-String)

# ---- sessions (the user-sketch surface) ----------------------------------
$sessions = $remote.Session()
Check 'Session().Current counts the connected Nexts' ($sessions.Current -eq 2)
Check 'Session().current works lowercase too' ($sessions.current -eq 2)
Check 'Session().List carries sid/addr/name/label' `
    ($sessions.List.Count -eq 2 -and $sessions.List[0].Sid -eq 1 `
     -and $sessions.List[1].Sid -eq 2 -and $sessions.List[1].Name -eq 'N-Go' `
     -and $sessions.List[0].Active) `
    ($sessions.List | Out-String)
Check 'Session().Max is the seat count' ($sessions.Max -eq 4)

$s1 = $remote.ManageSession($sessions.List[0])
$s2 = $remote.ManageSession(2)
$names1 = @($s1.Ls() | ForEach-Object Name)
$names2 = @($s2.Ls('/') | ForEach-Object Name)
Check 'ManageSession(info): seat 1 sees its own listing' `
    ($names1 -contains 'one.txt' -and -not ($names1 -contains 'two.txt')) ($names1 -join ',')
Check 'ManageSession(sid): seat 2 sees the OTHER listing' `
    ($names2 -contains 'two.txt' -and -not ($names2 -contains 'one.txt')) ($names2 -join ',')
Check 'a stale sid answers SessionGone (410)' `
    ((Get-ThrownReason { $remote.ManageSession(99).Ls() }) -eq 'SessionGone')

# ---- listing entries -----------------------------------------------------
$games = $s1.Ls('/games')
$boot = $games | Where-Object Name -eq 'boot.bas'
Check 'Ls: entry fields + pre-joined path' `
    ($null -ne $boot -and -not $boot.Dir -and $boot.Size -gt 0 `
     -and $boot.Path -eq '/games/boot.bas') ($games | Out-String)
Check 'Exists: present file' ($s1.Exists('/games/boot.bas'))
Check 'Exists: absent file' (-not $s1.Exists('/games/nope.bin'))
Check 'Exists: directory' ($s1.Exists('/games'))

# ---- get / put / sum / verify -------------------------------------------
$bytes = $s1.Get('/games/boot.bas')
Check 'Get(path): exact bytes' `
    ([System.Text.Encoding]::ASCII.GetString($bytes) -eq 'ten chars!' * 5) $bytes.Length
$bytes2 = $s1.Get($boot)
Check 'Get(entry): same bytes' (@(Compare-Object $bytes $bytes2).Count -eq 0)
$local = Join-Path $TmpDir 'fetched.bin'
[void]$s1.Get($boot, $local)
Check 'Get(entry, localPath): saved' `
    ((Get-Item $local).Length -eq $bytes.Length)
$gamesDir = $s1.Ls('/') | Where-Object { $_.Dir -and $_.Name -eq 'games' }
Check 'Get(dir entry) refuses locally' `
    ((Get-ThrownReason { $s1.Get($gamesDir) }) -eq 'BadRequest')
Check 'Get of a folder path: bridge answers BadRequest' `
    ((Get-ThrownReason { $s1.Get('/games') }) -eq 'BadRequest')

$push = Join-Path $TmpDir 'push.bin'
$data = [byte[]](0..255 + 255..0)
[System.IO.File]::WriteAllBytes($push, $data)
$put = $s1.Put($push, '/incoming/push.bin')
Check 'Put(file): result names path and size' `
    ($put.Path -eq '/incoming/push.bin' -and $put.Bytes -eq $data.Length) ($put | Out-String)
$sum = $s1.Sum('/incoming/push.bin')
$localSum = 0
foreach ($b in $data) { $localSum += $b }
Check 'Sum: size and sum16 as read back' `
    ($sum.Bytes -eq $data.Length -and $sum.Sum16 -eq ($localSum -band 0xFFFF)) ($sum | Out-String)
Check 'Verify: true for an intact push' ($s1.Verify($push, '/incoming/push.bin'))
[System.IO.File]::WriteAllBytes($push, [byte[]](1..100))
Check 'Verify: false once the local file differs' `
    (-not $s1.Verify($push, '/incoming/push.bin'))
$put2 = $s1.Put([byte[]](65, 66, 67), '/incoming/abc.bin')
Check 'Put(bytes): uploads from memory' `
    ($put2.Bytes -eq 3 -and [System.Text.Encoding]::ASCII.GetString($s1.Get('/incoming/abc.bin')) -eq 'ABC')

# ---- review-pinned regressions -------------------------------------------
# ResolveLocal's separator normalisation is PLATFORM-GATED: the branch only
# runs where the separator is '/', so Windows never executes it. It shipped
# broken once for exactly that reason (a lost backslash made it
# .Replace('', '/'), which throws mid-Deploy on linux/macOS only). Calling
# it directly means whichever platform runs this suite covers ITS branch.
$sbResolve = [scriptblock]::Create(
    "using module '$ModulePath'; [ZxNextBridgeHttp]::ResolveLocal('sub\rel.bin')")
$resolved = & $sbResolve
Check 'ResolveLocal roots a backslashed relative path (no throw on any platform)' `
    ([System.IO.Path]::IsPathRooted($resolved)) $resolved
# An already-rooted path is returned untouched. Built from GetTempPath()
# so this source carries no literal backslash of its own - the very thing
# that broke the line it guards.
$sbAbs = [scriptblock]::Create(
    "using module '$ModulePath'; [ZxNextBridgeHttp]::ResolveLocal([IO.Path]::GetTempPath() + 'x.bin')")
Check 'ResolveLocal leaves an absolute path alone' `
    ((& $sbAbs) -eq ([IO.Path]::GetTempPath() + 'x.bin')) (& $sbAbs)

# Relative LOCAL paths resolve against the PowerShell location, not the
# process CWD (the .NET File API's default) - Set-Location must be honoured.
Push-Location $TmpDir
try {
    [System.IO.File]::WriteAllBytes((Join-Path $TmpDir 'rel-src.bin'), [byte[]](9, 8, 7))
    $relPut = $s1.Put('rel-src.bin', '/incoming/rel.bin')
    Check 'Put(relative local path) honours Set-Location' ($relPut.Bytes -eq 3)
    Check 'Verify(relative local path) too' ($s1.Verify('rel-src.bin', '/incoming/rel.bin'))
    [void]$s1.Get('/incoming/rel.bin', 'rel-out.bin')
    Check 'Get(remote, relative local) lands in the PS location' `
        (Test-Path (Join-Path $TmpDir 'rel-out.bin'))
} finally {
    Pop-Location
}
# Ls() entries feed straight into Sum() and Rm() (the documented pattern) -
# without the typed overloads they would string-coerce into a garbage path.
$relEntry = $s1.Ls('/incoming') | Where-Object Name -eq 'rel.bin'
Check 'Sum(entry): the documented pattern works' `
    (($s1.Sum($relEntry)).Bytes -eq 3)
Check 'Rm(entry): the documented pattern works' `
    (($s1.Rm($relEntry)).Path -eq '/incoming/rel.bin')
Check 'Sum(dir entry) refuses locally' `
    ((Get-ThrownReason { $s1.Sum(($s1.Ls('/') | Where-Object { $_.Dir } | Select-Object -First 1)) }) -eq 'BadRequest')
# Exists() semantics at the edges: roots exist; a missing PARENT is $false,
# not a NextRefused error.
Check 'Exists("/") is true' ($s1.Exists('/'))
Check 'Exists of a path under a missing parent is $false, not an error' `
    (-not $s1.Exists('/no-such-dir/whatever.bin'))

# ---- management verbs ----------------------------------------------------
Check 'MkDir' ($s1.MkDir('/newdir').Path -eq '/newdir')
Check 'Ren' ($s1.Ren('/incoming/abc.bin', '/incoming/xyz.bin').To -eq '/incoming/xyz.bin')
Check 'Ren really moved it' `
    ($s1.Exists('/incoming/xyz.bin') -and -not $s1.Exists('/incoming/abc.bin'))
Check 'Rm' ($s1.Rm('/incoming/xyz.bin').Path -eq '/incoming/xyz.bin')
Check 'RmDir' ($s1.RmDir('/newdir').Path -eq '/newdir')
$rc = $s1.Rcpy('/games', 'm:/backup/games')
Check 'Rcpy: reports the copied file count' ($rc.Files -ge 1) ($rc | Out-String)
$rf = $s1.RfSize('/games')
Check 'RfSize: files/bytes' ($rf.Files -ge 1 -and $rf.Bytes -gt 0) ($rf | Out-String)
$fr = $s1.Free('C')
Check 'Free: bytes + human' ($fr.FreeBytes -gt 0 -and $fr.FreeHuman) ($fr | Out-String)
$dr = $s1.Drives()
Check 'Drives: roster' ($dr.Current -eq 'C' -and $dr.Partitions -eq 2) ($dr | Out-String)
Check 'Help(): the route reference text' ($remote.Help() -match 'Routes')

# ---- authentication: the bearer token ------------------------------------
$tokenlessCon = New-ZxNextRemoteConnection -IpAddress 127.0.0.1 -Port $TokenPort
Check 'token bridge, no token -> TokenRequired' `
    ((Get-ThrownReason { (New-ZxNextRemote $tokenlessCon).Status() }) -eq 'TokenRequired')
$wrongCon = New-ZxNextRemoteConnection -IpAddress 127.0.0.1 -Port $TokenPort -Token 'wrong'
Check 'token bridge, wrong token -> TokenRequired' `
    ((Get-ThrownReason { (New-ZxNextRemote $wrongCon).Status() }) -eq 'TokenRequired')
Check 'Test-ZxNextRemoteBridge THROWS on a token refusal (no silent poll-for-ever)' `
    ((Get-ThrownReason { Test-ZxNextRemoteBridge $tokenlessCon }) -eq 'TokenRequired')
$goodCon = New-ZxNextRemoteConnection -IpAddress 127.0.0.1 -Port $TokenPort -Token $Token
$tokenRemote = New-ZxNextRemote $goodCon
Check 'token bridge, right token -> works' ($tokenRemote.Status().Connected)

# ---- the samples' bearer pattern on the ZxNextRemote ctor ----------------
$mk2 = [scriptblock]::Create(@"
using module '$ModulePath'
param([string]`$Kind)
switch (`$Kind) {
    'bearer-real'  { `$bearer = '$Token'
                     `$c = [ZxNextRemoteConnection]::new('127.0.0.1', $TokenPort)
                     [ZxNextRemote]::new(`$c, `$bearer).Status().Connected }
    'bearer-empty' { `$bearer = ''
                     `$c = [ZxNextRemoteConnection]::new('127.0.0.1', $TokenPort)
                     [ZxNextRemote]::new(`$c, `$bearer).Status() }
    'bearer-keep'  { `$bearer = ''
                     `$c = [ZxNextRemoteConnection]::new('127.0.0.1', $TokenPort, '$Token')
                     [ZxNextRemote]::new(`$c, `$bearer).Status().Connected }
    'bearer-3arg'  { [ZxNextRemote]::new('127.0.0.1', $TokenPort, '$Token').Status().Connected }
    'bearer-sess'  { `$bearer = '$Token'
                     `$c = [ZxNextRemoteConnection]::new('127.0.0.1', $TokenPort)
                     [ZxNextRemote]::new(`$c, `$bearer).ManageSession().Free('C').FreeBytes }
}
"@)
Check 'ZxNextRemote ctor (con, bearer): the token rides every request' `
    ((& $mk2 'bearer-real') -eq $true)
Check 'ZxNextRemote ctor with bearer='''' -> TokenRequired (no token sent)' `
    ((Get-ThrownReason { & $mk2 'bearer-empty' }) -eq 'TokenRequired')
Check 'bearer='''' keeps a token the connection already carries' `
    ((& $mk2 'bearer-keep') -eq $true)
Check 'ZxNextRemote ctor (ip, port, bearer)' `
    ((& $mk2 'bearer-3arg') -eq $true)
Check 'sessions minted after the ctor inherit the bearer' `
    ((& $mk2 'bearer-sess') -gt 0)
$fnRemote = New-ZxNextRemote -Connection (New-ZxNextRemoteConnection 127.0.0.1 $TokenPort) -Token $Token
Check 'New-ZxNextRemote -Connection -Token: same pattern via the function' `
    ($fnRemote.Status().Connected)

# ---- standalone ZxNextRemoteSession construction (token rides along) -----
$mk = [scriptblock]::Create(@"
using module '$ModulePath'
param([string]`$Kind)
switch (`$Kind) {
    'inline-good'  { [ZxNextRemoteSession]::new('127.0.0.1', $TokenPort, '$Token').Free('C').FreeBytes }
    'inline-bad'   { [ZxNextRemoteSession]::new('127.0.0.1', $TokenPort, 'wrong').Free('C') }
    'con-sid'      { ([ZxNextRemoteSession]::new(
                        [ZxNextRemoteConnection]::new('127.0.0.1', $PlainPort), 2).Ls() |
                            ForEach-Object Name) -join ',' }
    'con-active'   { ([ZxNextRemoteSession]::new(
                        [ZxNextRemoteConnection]::new('127.0.0.1', $PlainPort)).Ls() |
                            ForEach-Object Name) -join ',' }
}
"@)
Check 'session ctor (ip, port, token): the token reaches every request' `
    ((& $mk 'inline-good') -gt 0)
Check 'session ctor with a wrong token -> TokenRequired' `
    ((Get-ThrownReason { & $mk 'inline-bad' }) -eq 'TokenRequired')
Check 'session ctor (connection, sid): rides that seat' `
    ((& $mk 'con-sid') -match 'two\.txt')
Check 'session ctor (connection): the active seat' `
    ((& $mk 'con-active') -match 'one\.txt')

# ---- authentication: OS protection (the OTHER 401) -----------------------
$ospCon = New-ZxNextRemoteConnection -IpAddress 127.0.0.1 -Port $OspPort
$ospS = (New-ZxNextRemote $ospCon).ManageSession()
Check 'OSP bridge: a write -> OsProtected (not TokenRequired)' `
    ((Get-ThrownReason { $ospS.Put([byte[]](1, 2, 3), '/sys/x.bin') }) -eq 'OsProtected')
Check 'OSP bridge: mkdir -> OsProtected' `
    ((Get-ThrownReason { $ospS.MkDir('/sys/x') }) -eq 'OsProtected')
Check 'OSP bridge: reads still work' ($ospS.Free('C').FreeBytes -gt 0)
Check 'OSP bridge: rmtree -> Unsupported (501)' `
    ((Get-ThrownReason { $ospS.RmTree('/sys') }) -eq 'Unsupported')

# ---- no Next connected ---------------------------------------------------
# The dead port refuses TCP outright; the OSP adapter answers /status but
# its sibling on the same wrapper (see the python side) serves 503 for ops.
Check 'unreachable bridge -> BridgeUnreachable' `
    ((Get-ThrownReason { (New-ZxNextRemote $deadCon).Status() }) -eq 'BridgeUnreachable')
Check 'no Next behind the bridge -> NoNextConnected' `
    ((Get-ThrownReason { $ospS.Ren('/a', '/b') }) -eq 'NoNextConnected')

# ---- local timeout classification ---------------------------------------
$slowCon = New-ZxNextRemoteConnection -IpAddress 127.0.0.1 -Port $OspPort -TimeoutSec 1
$slowS = (New-ZxNextRemote $slowCon).ManageSession()
Check 'local timeout -> Timeout (status 0)' `
    ((Get-ThrownReason { $slowS.Free('slow') }) -eq 'Timeout')

# ---- Close() semantics ---------------------------------------------------
$closable = New-ZxNextRemote -Connection $con
$closableS = $closable.ManageSession(1)
$closable.Close()
Check 'Close(): the root refuses further calls' `
    ((Get-ThrownReason { $closable.Status() }) -eq 'ConnectionClosed')
Check 'Close(): sessions minted before the close refuse too' `
    ((Get-ThrownReason { $closableS.Ls() }) -eq 'ConnectionClosed')
Check 'Close() did not /forceexit: the bridge still answers others' `
    ($remote.Status().Connected)

# ---- forceexit (last: the fake counts the broadcasts) --------------------
$remote.ForceExit()
Check 'ForceExit(): accepted' $true

Write-Host ''
if ($script:failures -eq 0) {
    Write-Host 'DRIVER RESULT: ALL PASS'
    exit 0
}
Write-Host "DRIVER RESULT: $($script:failures) FAILURE(S)"
exit 1
