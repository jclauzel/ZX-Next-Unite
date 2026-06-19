<#
  build.ps1 - build syncdev.dot (NextSync bidirectional / Sync4 dot command).

  Produces syncdev.dot and copies it to server\dot\syncdev.

  Two things that MUST hold or the dot misbehaves on hardware:
    * --sdcccall 0   : matches the stack calling convention of the hand-written
                       assembly (crt0.s/std.s/esxdos.s/uart.s). Without it,
                       memcpy/fopen/fread get garbage params -> hang / "?" output.
    * size <= 8192   : a .dot command loads at $2000 and must fit in $2000-$3FFF.

  Usage:
    .\build.ps1                 # build with SDCC from PATH or C:\Program Files\SDCC
    .\build.ps1 -SdccBin "D:\sdcc\bin"   # use a specific SDCC bin directory
    .\build.ps1 -Keep           # keep intermediate .rel/.ihx files
#>
[CmdletBinding()]
param(
    [string]$SdccBin = "",
    [switch]$Keep
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

# --- locate the SDCC toolchain ------------------------------------------------
if (-not $SdccBin) {
    if (Get-Command sdcc -ErrorAction SilentlyContinue) {
        $SdccBin = Split-Path (Get-Command sdcc).Source
    } elseif (Test-Path "C:\Program Files\SDCC\bin\sdcc.exe") {
        $SdccBin = "C:\Program Files\SDCC\bin"
    } else {
        throw "Could not find sdcc. Put it on PATH or pass -SdccBin <dir>."
    }
}
$sdcc    = Join-Path $SdccBin "sdcc.exe"
$sdasz80 = Join-Path $SdccBin "sdasz80.exe"
$ihx2bin = Join-Path $PSScriptRoot "..\tools\ihx2bin.exe"
foreach ($t in @($sdcc, $sdasz80, $ihx2bin)) {
    if (-not (Test-Path $t)) { throw "Missing tool: $t" }
}
# sdcc shells out to sdcpp / cc1, which it resolves via PATH - make sure the
# SDCC bin dir is on PATH or compilation fails with "cannot execute 'cc1'".
$env:PATH = "$SdccBin;" + $env:PATH
Write-Host "SDCC: $sdcc"

$CFLAGS = @("-mz80","--sdcccall","0","--no-std-crt0","--nostdlib","--opt-code-speed","--peep-asm","--peep-return")
$LFLAGS = @("-mz80","--sdcccall","0","--no-std-crt0","--opt-code-speed","--nostdlib","--code-loc","0x2100","-Wl","-b_HEADER=0x2000")
$ASM = @("crt0","std","esxdos","uart")
$C   = @("gfx","nextsync")
$REL = @("crt0.rel","std.rel","esxdos.rel","uart.rel","gfx.rel","nextsync.rel")

# --- assemble -----------------------------------------------------------------
Write-Host "Assembling..."
foreach ($s in $ASM) {
    & $sdasz80 -xlos -g "$s.rel" "$s.s"
    if ($LASTEXITCODE -ne 0) { throw "assemble failed: $s.s" }
}

# --- compile ------------------------------------------------------------------
Write-Host "Compiling..."
foreach ($c in $C) {
    & $sdcc -c -o "$c.rel" "$c.c" @CFLAGS
    if ($LASTEXITCODE -ne 0) { throw "compile failed: $c.c" }
}

# --- link & convert -----------------------------------------------------------
Write-Host "Linking..."
& $sdcc @LFLAGS @REL
if ($LASTEXITCODE -ne 0 -or -not (Test-Path crt0.ihx)) { throw "link failed" }

& $ihx2bin crt0.ihx syncdev.dot | Out-Null
if (-not (Test-Path syncdev.dot)) { throw "ihx2bin failed" }

# --- deploy -------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path "server\dot" | Out-Null
Copy-Item syncdev.dot "server\dot\syncdev" -Force

# --- clean intermediates ------------------------------------------------------
if (-not $Keep) {
    Get-ChildItem *.rel,*.ihx,*.lst,*.sym,crt0.map,crt0.lk,crt0.noi,crt0.adb -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

# --- report -------------------------------------------------------------------
$sz = (Get-Item syncdev.dot).Length
Write-Host ""
Write-Host ("syncdev.dot = {0} bytes (0x{0:X4})" -f $sz)
if ($sz -le 8192) {
    Write-Host ("OK - fits the 8KB dot limit (margin {0} bytes)." -f (8192 - $sz)) -ForegroundColor Green
    Write-Host "Deployed to server\dot\syncdev"
} else {
    Write-Host ("FAIL - OVER the 8KB dot limit by {0} bytes; it will crash on load." -f ($sz - 8192)) -ForegroundColor Red
    exit 1
}
