<#
  build_dotn.ps1 - build the NextSync .sync command as a z88dk "dotN".

  A dotN is not bound by the 8 KB limit of a classic dot command: appmake splits
  the binary into the 8 KB page that loads at $2000 plus additional pages that
  the dotN loader allocates from NextZXOS and maps into mmu4/mmu5 at run time.

  Produces:  syncdev  (the dotN command)  and copies it to ..\server\dot\syncdev

  Usage:
    .\build_dotn.ps1                       # z88dk from C:\z88dk (or $env:Z88DK_DIR)
    .\build_dotn.ps1 -Z88dkDir "D:\z88dk"  # use a specific z88dk install
    .\build_dotn.ps1 -Keep                 # keep intermediates
#>
[CmdletBinding()]
param(
    [string]$Z88dkDir = "",
    [switch]$Keep
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

# --- locate z88dk ------------------------------------------------------------
if (-not $Z88dkDir) {
    if ($env:Z88DK_DIR -and (Test-Path (Join-Path $env:Z88DK_DIR "bin\zcc.exe"))) {
        $Z88dkDir = $env:Z88DK_DIR
    } elseif (Test-Path "C:\z88dk\bin\zcc.exe") {
        $Z88dkDir = "C:\z88dk"
    } else {
        throw "Could not find z88dk. Pass -Z88dkDir <dir> or set Z88DK_DIR."
    }
}
$Z88dkDir = $Z88dkDir.TrimEnd('\')
$zcc = Join-Path $Z88dkDir "bin\zcc.exe"
if (-not (Test-Path $zcc)) { throw "Missing zcc: $zcc" }

$env:ZCCCFG = Join-Path $Z88dkDir "lib\config\"
$env:PATH   = (Join-Path $Z88dkDir "bin") + ";" + $env:PATH
Write-Host "z88dk: $Z88dkDir"

# --- clean previous outputs --------------------------------------------------
Get-ChildItem -File -ErrorAction SilentlyContinue `
    syncdev, syncdev_*.bin, *.o, *.map, *.lis, zcc_opt.def,
    anim_head, anim_head.asm |
    Remove-Item -Force -ErrorAction SilentlyContinue

# --- anim.c -> primary dot page ----------------------------------------------
# The main bank's free space doubles as the C stack (~0.5 KB left below
# REGISTER_SP), while the primary 8 KB dot page has ~5 KB unused after the
# crt+clib. zsdcc offers no per-file section control (#pragma codeseg and
# --codeseg are silently ignored), so: compile anim.c to asm, retarget its
# code/const sections at code_dot / rodata_dot — the dotn memory model's
# head-page sections reserved for user dot-resident content, placed AFTER the
# crt+clib chains (crt_memory_model_dotn.inc) — and let the link assemble the
# patched anim_head.asm instead of the C file (zproject.lst references it).
# NEVER retarget at CODE itself: that appends into the middle of the crt's
# startup fall-through chain (CODE -> code_crt_main -> ...) and crashes the
# Next the moment the dot starts. The sprite-state bss/data bytes stay in the
# main bank.
Write-Host "Generating anim_head.asm (anim.c code+consts -> primary dot page)..."
& $zcc +zxn -startup=30 -clib=sdcc_iy -SO3 --max-allocs-per-node200000 `
    --opt-code-size -pragma-include:zpragma.inc -a anim.c -o anim_head
if ($LASTEXITCODE -ne 0) { throw "anim.c asm generation failed (exit $LASTEXITCODE)." }
(Get-Content anim_head) `
    -replace '^\s*SECTION\s+code_compiler\s*$', "`tSECTION code_dot" `
    -replace '^\s*SECTION\s+rodata_compiler\s*$', "`tSECTION rodata_dot" |
    Set-Content anim_head.asm
Remove-Item anim_head -Force

# --- build -------------------------------------------------------------------
# Flags follow the z88dk dot-command examples (ls/dzx7):
#   +zxn                 target ZX Spectrum Next
#   -startup=30          NextZXOS dot-command crt (becomes 798 with -subtype=dotn)
#   -clib=sdcc_iy        newlib compiled by zsdcc, IY reserved
#   -subtype=dotn        emit a dotN (banked) command
#   -create-app          run appmake to produce the final file
Write-Host "Building syncdev (dotN)..."
& $zcc +zxn -startup=30 -clib=sdcc_iy -SO3 --max-allocs-per-node200000 `
    --opt-code-size "@zproject.lst" -o syncdev -pragma-include:zpragma.inc `
    -subtype=dotn -Cz"--clean" -create-app -m
if ($LASTEXITCODE -ne 0) { throw "zcc build failed (exit $LASTEXITCODE)." }
if (-not (Test-Path syncdev)) { throw "build produced no 'syncdev' file." }

# --- deploy ------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path "..\server\dot" | Out-Null
Copy-Item syncdev "..\server\dot\syncdev" -Force

# --- report ------------------------------------------------------------------
$sz = (Get-Item syncdev).Length
Write-Host ""
Write-Host ("syncdev (dotN) = {0} bytes (0x{0:X})" -f $sz) -ForegroundColor Green
Write-Host "No 8 KB limit: appmake pages the overflow into mmu4/mmu5 at run time."
Write-Host "Deployed to ..\server\dot\syncdev"

if (-not $Keep) {
    Get-ChildItem -File -ErrorAction SilentlyContinue `
        *.o, zcc_opt.def, syncdev_*.bin, anim_head.asm |
        Remove-Item -Force -ErrorAction SilentlyContinue
}
