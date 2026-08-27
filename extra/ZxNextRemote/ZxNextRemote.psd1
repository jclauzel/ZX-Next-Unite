# Module manifest for ZxNextRemote - a PowerShell client for ZX-Next-Unite's
# NextSync HTTP bridge. See PowerShell/PowerShellHelperClass.md in the repo
# for install/uninstall and a walkthrough, and Get-Help about_ZxNextRemote
# for the class reference.
@{
    RootModule           = 'ZxNextRemote.psm1'
    ModuleVersion        = '1.0.0'
    GUID                 = '8d34e7e8-8253-4ed0-b05f-00ef9a6bac30'
    Author               = 'Julien Clauzel'
    CompanyName          = 'ZX-Next-Unite'
    Copyright            = '(c) Julien Clauzel. MIT License.'
    Description          = 'Client for the ZX-Next-Unite NextSync HTTP bridge: list the connected ZX Spectrum Nexts and drive their SD cards (ls/get/put/ren/rcpy/...) from PowerShell 5.1+ or 7+.'
    PowerShellVersion    = '5.1'
    CompatiblePSEditions = @('Desktop', 'Core')
    FunctionsToExport    = @('New-ZxNextRemoteConnection', 'New-ZxNextRemote',
                             'Test-ZxNextRemoteBridge')
    CmdletsToExport      = @()
    VariablesToExport    = @()
    AliasesToExport      = @('Connect-ZxNextRemote')
    PrivateData          = @{
        PSData = @{
            Tags       = @('ZXSpectrumNext', 'NextSync', 'retrocomputing')
            LicenseUri = 'https://github.com/jclauzel/ZX-Next-Unite/blob/main/LICENSE'
            ProjectUri = 'https://github.com/jclauzel/ZX-Next-Unite'
        }
    }
}
