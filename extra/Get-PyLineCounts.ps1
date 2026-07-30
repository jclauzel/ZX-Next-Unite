<#
.SYNOPSIS
    List every .py file with its line count (like
    `type file.py | Measure-Object -Line`, but for all .py files).

.EXAMPLE
    .\Get-PyLineCounts.ps1
    .\Get-PyLineCounts.ps1 -Recurse
    .\Get-PyLineCounts.ps1 -Path tests -Recurse
#>
[CmdletBinding()]
param(
    [string]$Path = '.',
    [switch]$Recurse
)

$results = Get-ChildItem -Path $Path -Filter *.py -File -Recurse:$Recurse |
    ForEach-Object {
        [pscustomobject]@{
            Lines = (Get-Content -LiteralPath $_.FullName | Measure-Object -Line).Lines
            File  = (Resolve-Path -LiteralPath $_.FullName -Relative)
        }
    } | Sort-Object Lines -Descending

$results | Format-Table -AutoSize

$total = ($results | Measure-Object -Property Lines -Sum).Sum
"Total: {0:N0} lines across {1} file(s)" -f $total, @($results).Count
