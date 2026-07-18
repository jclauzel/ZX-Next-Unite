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
    .\build_dotn.ps1 -Priority Normal      # don't boost the compiler processes
    .\build_dotn.ps1 -Affinity 0x1         # pin the compile to CPU core 0

  Process boosting: every zcc run goes through Invoke-Zcc, which launches it
  with Start-Process -PassThru and raises $proc.PriorityClass (default High;
  Start-Process itself has no -Priority parameter). CAVEAT: Windows only makes
  child processes inherit *Idle/BelowNormal* priority classes, so High applies
  to the zcc driver but NOT to the zsdcc/z80asm/appmake children it spawns -
  the boost mainly helps on a loaded machine. -Affinity (a CPU mask, 0 = leave
  alone) DOES inherit to every child: 0x1 pins the whole compile to core 0,
  which serialises it - measured slower here, so it stays off by default.
#>
[CmdletBinding()]
param(
    [string]$Z88dkDir = "",
    [switch]$Keep,
    [ValidateSet("Idle", "BelowNormal", "Normal", "AboveNormal", "High", "RealTime")]
    [string]$Priority = "High",
    [long]$Affinity = 0
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
Write-Host "z88dk: $Z88dkDir  (priority: $Priority$(if ($Affinity) { ", affinity: 0x{0:X}" -f $Affinity }))"

# Run zcc boosted. Returns the exit code (callers test that, not $LASTEXITCODE:
# Start-Process doesn't set it). -NoNewWindow keeps compiler output in this
# console. Priority/affinity are set right after launch; the try/catch covers
# the race where a (failed) zcc exits before we get to touch it.
function Invoke-Zcc {
    param([Parameter(Mandatory)][string[]]$ZccArgs)
    $proc = Start-Process -FilePath $zcc -ArgumentList $ZccArgs -NoNewWindow -PassThru
    try {
        $proc.PriorityClass = $Priority
        if ($Affinity -ne 0) { $proc.ProcessorAffinity = [IntPtr]$Affinity }
    } catch {
        # Already exited (or access denied for RealTime without admin) - the
        # build itself must not fail over a boost, so just carry on unboosted.
    }
    $proc.WaitForExit()
    return $proc.ExitCode
}

# --- clean previous outputs --------------------------------------------------
Get-ChildItem -File -ErrorAction SilentlyContinue `
    syncdev, syncdev_*.bin, *.o, *.map, *.lis, zcc_opt.def,
    anim_head, anim_head.asm, free_head, free_head.asm, rcpy_head, rcpy_head.asm,
    rfsize_head, rfsize_head.asm |
    Remove-Item -Force -ErrorAction SilentlyContinue

# --- anim.c -> primary dot page ------------------------------------------------
# The main bank's free space doubles as the C stack, while the primary 8 KB
# dot page has ~5 KB unused after the crt+clib. zsdcc offers no per-file
# section control (#pragma codeseg and --codeseg are silently ignored), so:
# compile anim.c to asm, retarget its code/const sections at code_dot /
# rodata_dot — the dotn memory model's head-page sections reserved for user
# dot-resident content, placed AFTER the crt+clib chains
# (crt_memory_model_dotn.inc) — and let the link assemble the patched
# anim_head.asm instead of the C file (zproject.lst references it).
# NEVER retarget at CODE itself: that appends into the middle of the crt's
# startup fall-through chain (CODE -> code_crt_main -> ...) and crashes the
# Next the moment the dot starts. And head-page code must NEVER print (the
# ROM-print driver pages the ROM over $2000-$3FFF, i.e. over the code itself).
# (To build without the animation, see ANIM_ENABLED in nextsync.c.)
Write-Host "Generating anim_head.asm (anim.c code+consts -> primary dot page)..."
$rc = Invoke-Zcc @("+zxn", "-startup=30", "-clib=sdcc_iy", "-SO3",
    "--max-allocs-per-node200000", "--opt-code-size",
    "-pragma-include:zpragma.inc", "-a", "anim.c", "-o", "anim_head")
if ($rc -ne 0) { throw "anim.c asm generation failed (exit $rc)." }
(Get-Content anim_head) `
    -replace '^\s*SECTION\s+code_compiler\s*$', "`tSECTION code_dot" `
    -replace '^\s*SECTION\s+rodata_compiler\s*$', "`tSECTION rodata_dot" |
    Set-Content anim_head.asm
Remove-Item anim_head -Force

# --- free.c -> primary dot page (same retarget as anim.c) ---------------------
# sync_getfree/listen_free (the -listen psize/pfull free-space query, v5.2):
# ~350 bytes of 32-bit arithmetic that would otherwise eat main-bank stack
# headroom. Same head-page rules apply: no printing, esxDOS calls only.
Write-Host "Generating free_head.asm (free.c code+consts -> primary dot page)..."
$rc = Invoke-Zcc @("+zxn", "-startup=30", "-clib=sdcc_iy", "-SO3",
    "--max-allocs-per-node200000", "--opt-code-size",
    "-pragma-include:zpragma.inc", "-a", "free.c", "-o", "free_head")
if ($rc -ne 0) { throw "free.c asm generation failed (exit $rc)." }
(Get-Content free_head) `
    -replace '^\s*SECTION\s+code_compiler\s*$', "`tSECTION code_dot" `
    -replace '^\s*SECTION\s+rodata_compiler\s*$', "`tSECTION rodata_dot" |
    Set-Content free_head.asm
Remove-Item free_head -Force

# --- rcpy.c -> primary dot page (same retarget as anim.c/free.c) --------------
# listen_rcpy and friends (the -listen local-copy command, v5.2): the file/dir
# copy walk with its progress blocks. Same head-page rules: no printing,
# esxDOS calls only, no file-scope static data.
Write-Host "Generating rcpy_head.asm (rcpy.c code+consts -> primary dot page)..."
$rc = Invoke-Zcc @("+zxn", "-startup=30", "-clib=sdcc_iy", "-SO3",
    "--max-allocs-per-node200000", "--opt-code-size",
    "-pragma-include:zpragma.inc", "-a", "rcpy.c", "-o", "rcpy_head")
if ($rc -ne 0) { throw "rcpy.c asm generation failed (exit $rc)." }
(Get-Content rcpy_head) `
    -replace '^\s*SECTION\s+code_compiler\s*$', "`tSECTION code_dot" `
    -replace '^\s*SECTION\s+rodata_compiler\s*$', "`tSECTION rodata_dot" |
    Set-Content rcpy_head.asm
Remove-Item rcpy_head -Force

# --- rfsize.c -> primary dot page (same retarget) -----------------------------
# listen_rfsize (the -listen tree-size command, v5.2): rcpy's "will it fit"
# companion. Same head-page rules. NOTE: its one static dirent lands in
# main-bank bss (bss is never retargeted) - that is intentional, see rfsize.c.
Write-Host "Generating rfsize_head.asm (rfsize.c code+consts -> primary dot page)..."
$rc = Invoke-Zcc @("+zxn", "-startup=30", "-clib=sdcc_iy", "-SO3",
    "--max-allocs-per-node200000", "--opt-code-size",
    "-pragma-include:zpragma.inc", "-a", "rfsize.c", "-o", "rfsize_head")
if ($rc -ne 0) { throw "rfsize.c asm generation failed (exit $rc)." }
(Get-Content rfsize_head) `
    -replace '^\s*SECTION\s+code_compiler\s*$', "`tSECTION code_dot" `
    -replace '^\s*SECTION\s+rodata_compiler\s*$', "`tSECTION rodata_dot" |
    Set-Content rfsize_head.asm
Remove-Item rfsize_head -Force

# --- build -------------------------------------------------------------------
# Flags follow the z88dk dot-command examples (ls/dzx7):
#   +zxn                 target ZX Spectrum Next
#   -startup=30          NextZXOS dot-command crt (becomes 798 with -subtype=dotn)
#   -clib=sdcc_iy        newlib compiled by zsdcc, IY reserved
#   -subtype=dotn        emit a dotN (banked) command
#   -create-app          run appmake to produce the final file
Write-Host "Building syncdev (dotN)..."
$rc = Invoke-Zcc @("+zxn", "-startup=30", "-clib=sdcc_iy", "-SO3",
    "--max-allocs-per-node200000", "--opt-code-size", "@zproject.lst",
    "-o", "syncdev", "-pragma-include:zpragma.inc",
    "-subtype=dotn", "-Cz--clean", "-create-app", "-m")
if ($rc -ne 0) { throw "zcc build failed (exit $rc)." }
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
        *.o, zcc_opt.def, syncdev_*.bin, anim_head.asm, free_head.asm,
        rcpy_head.asm, rfsize_head.asm |
        Remove-Item -Force -ErrorAction SilentlyContinue
}
