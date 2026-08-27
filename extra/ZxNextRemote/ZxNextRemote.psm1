#Requires -Version 5.1
<#
  ZxNextRemote.psm1 - a client/proxy layer for ZX-Next-Unite's NextSync
  HTTP bridge, so a script (or an interactive prompt) can drive the SD card
  of a ZX Spectrum Next connected to the bridge without writing a single
  HTTP call.

  Wire contract: nextsync/sync/server/HTTP_BRIDGE.md in the repo (the
  bridge implementation is zxnu_http_bridge.py). Every route is called with
  &json=1 so answers come back as JSON, except the two whose success body
  is not JSON by nature: /get (the file bytes) and /help (the route list
  as plain text).

  Runs on Windows PowerShell 5.1 AND PowerShell 7+ (mac/linux included):
  - no ternary / null-coalescing / null-conditional operators,
  - all HTTP through System.Net.HttpWebRequest, which behaves identically
    on both editions and - unlike Invoke-WebRequest on 5.1 - moves binary
    bodies at full speed with no progress-bar tax,
  - pure-ASCII source, so the no-BOM/ANSI trap of powershell.exe cannot
    corrupt anything.

  Classes only become visible with `using module`; the exported functions
  (New-ZxNextRemoteConnection / New-ZxNextRemote / Test-ZxNextRemoteBridge,
  full comment-based help on each) construct the same objects for plain
  Import-Module callers - method calls on a returned object need no
  `using module`. See Get-Help about_ZxNextRemote for the class reference.
#>

Set-StrictMode -Version Latest

# ---------------------------------------------------------------------------
# Typed error. EVERY failure a caller can see is thrown as one of these, with
# .Reason naming the failure class so a script can branch without parsing
# message text. The two 401s the bridge can answer are deliberately DISTINCT
# reasons: a bearer-token refusal (fix: supply/correct the token in the
# connection) and an os-protected write refusal (fix: on the remote machine -
# it is ZX Next Remote's "OS protection" guarding that folder; the token is
# fine and reads still work).
# ---------------------------------------------------------------------------
class ZxNextRemoteError : System.Exception {
    [int]    $StatusCode   # HTTP status (0 = never reached the bridge)
    [string] $Reason       # one of the constants below
    [string] $Route        # the route that failed, e.g. '/put'
    [string] $Detail       # the bridge's own error text, when there was one

    static [string] $TokenRequired    = 'TokenRequired'    # 401, bearer token missing/wrong
    static [string] $OsProtected      = 'OsProtected'      # 401, remote OS protection blocked a write
    static [string] $SessionGone      = 'SessionGone'      # 410, the selected seat left - re-list sessions
    static [string] $NoNextConnected  = 'NoNextConnected'  # 503, bridge up but no Next in -listen
    static [string] $Timeout          = 'Timeout'          # 504, or the local HTTP timeout
    static [string] $BadRequest       = 'BadRequest'       # 400, bad arguments
    static [string] $NextRefused      = 'NextRefused'      # 502, the Next said no
    static [string] $Unsupported      = 'Unsupported'      # 501, host does not implement the verb
    static [string] $BridgeUnreachable= 'BridgeUnreachable'# no HTTP answer at all
    static [string] $ConnectionClosed = 'ConnectionClosed' # this client object was Close()d
    static [string] $HttpError        = 'HttpError'        # anything else

    ZxNextRemoteError([string]$message, [int]$statusCode, [string]$reason,
                      [string]$route, [string]$detail) : base($message) {
        $this.StatusCode = $statusCode
        $this.Reason     = $reason
        $this.Route      = $route
        $this.Detail     = $detail
    }
}

# ---------------------------------------------------------------------------
# Connection settings: where the bridge is, the optional bearer token, and
# the two timeout tiers. The bridge itself gives up after 45 s on quick
# verbs and 270 s on transfers (DEFAULT_TIMEOUT / LONG_TIMEOUT in
# zxnu_http_bridge.py), so the client tiers only need to outlast those -
# the generous LongTimeoutSec default just means the bridge's own 504,
# which names the stalled op, is what the caller sees.
#
# The bearer token is BRIDGE-SCOPED: one bridge = one connection object =
# one token. The ZxNextRemote root and every session it mints share this
# object, so the token rides every request to that bridge - seats never
# carry a token of their own.
# ---------------------------------------------------------------------------
class ZxNextRemoteConnection {
    [string] $IpAddress
    [int]    $Port = 80
    [string] $Token = ''
    [int]    $TimeoutSec = 60        # quick verbs (ls, status, ren, ...)
    [int]    $LongTimeoutSec = 900   # transfers/tree walks; outlasts the bridge's own 270 s cap

    ZxNextRemoteConnection([string]$ipAddress) {
        $this.IpAddress = $ipAddress
    }
    ZxNextRemoteConnection([string]$ipAddress, [int]$port) {
        $this.IpAddress = $ipAddress
        $this.Port = $port
    }
    ZxNextRemoteConnection([string]$ipAddress, [int]$port, [string]$token) {
        $this.IpAddress = $ipAddress
        $this.Port = $port
        $this.Token = $token
    }

    [string] BaseUrl() {
        return ('http://{0}:{1}' -f $this.IpAddress, $this.Port)
    }
    [string] ToString() {
        return $this.BaseUrl()
    }
}

# ---------------------------------------------------------------------------
# The one place that speaks HTTP. Static so the classes below need no
# instance plumbing; private in spirit (not part of the documented surface).
# ---------------------------------------------------------------------------
class ZxNextBridgeHttp {
    static [string] $TokenHeader   = 'ZXNEXTUNITE-BRIDGE-TOKEN'
    static [string] $SessionHeader = 'ZXNEXTUNITE-BRIDGE-SESSION'

    # Root a LOCAL path against PowerShell's current location. The .NET
    # File API resolves relative paths against the PROCESS working
    # directory, which PowerShell deliberately does not move on
    # Set-Location - so without this, Put('build\game.nex', ...) reads
    # from wherever the process started, silently pushing a stale file
    # when one happens to exist there. ProviderPath also normalises the
    # separators, so a backslashed relative path works under pwsh on
    # linux/macOS too.
    static [string] ResolveLocal([string]$path) {
        if ([System.IO.Path]::DirectorySeparatorChar -eq '/') {
            # linux/macOS: a backslashed relative path from a shared
            # Windows sample is a literal filename char - normalise it.
            $path = $path.Replace([char]92, '/')
        }
        if ([System.IO.Path]::IsPathRooted($path)) { return $path }
        return [System.IO.Path]::Combine((Get-Location).ProviderPath, $path)
    }

    # Percent-encode one query VALUE (paths carry spaces, '&', '#', ...).
    static [string] Esc([string]$value) {
        return [uri]::EscapeDataString($value)
    }

    # One request. Returns byte[] when $rawBytes, else the parsed JSON
    # (PSCustomObject). Throws ZxNextRemoteError for every failure.
    static [object] Invoke([ZxNextRemoteConnection]$con, [string]$route,
                           [string]$method, [byte[]]$body,
                           [int]$timeoutSec, [bool]$rawBytes,
                           [object]$sessionId) {
        $shortRoute = $route
        $q = $route.IndexOf('?')
        if ($q -ge 0) { $shortRoute = $route.Substring(0, $q) }

        $req = $null
        $resp = $null
        try {
            $req = [System.Net.WebRequest]::Create($con.BaseUrl() + $route)
            $req.Method = $method
            $req.Timeout = $timeoutSec * 1000
            $req.ReadWriteTimeout = $timeoutSec * 1000
            # LAN-only service by contract (HTTP_BRIDGE.md): never hairpin
            # through a corporate proxy.
            $req.Proxy = $null
            if ($null -eq $body) {
                # Flask answers the Expect: 100-continue dance slowly; skip
                # it for bodyless requests. Requests WITH a body keep the
                # handshake: a token-refusing bridge then answers its 401
                # BEFORE the upload, instead of resetting the connection
                # mid-body (which would misclassify as BridgeUnreachable).
                $req.ServicePoint.Expect100Continue = $false
            }
            if ($con.Token) {
                $req.Headers[[ZxNextBridgeHttp]::TokenHeader] = $con.Token
            }
            if ($null -ne $sessionId) {
                $req.Headers[[ZxNextBridgeHttp]::SessionHeader] = [string]$sessionId
            }
            if ($null -ne $body) {
                $req.ContentType = 'application/octet-stream'
                $req.ContentLength = $body.Length
                $out = $req.GetRequestStream()
                try {
                    $out.Write($body, 0, $body.Length)
                } finally {
                    $out.Close()
                }
            }
            $resp = $req.GetResponse()
            $bytes = [ZxNextBridgeHttp]::ReadAll($resp)
            if ($rawBytes) { return $bytes }
            return [ZxNextBridgeHttp]::ParseJson($bytes, $shortRoute)
        } catch [ZxNextRemoteError] {
            throw    # already typed (e.g. ParseJson) - never re-wrap
        } catch [System.Net.WebException] {
            $we = $_.Exception
            if ($null -ne $we.Response) {
                $status = [int]$we.Response.StatusCode
                $errBytes = [ZxNextBridgeHttp]::ReadAll($we.Response)
                $detail = [ZxNextBridgeHttp]::ErrorText($errBytes)
                throw [ZxNextBridgeHttp]::Classify($status, $detail, $shortRoute)
            }
            if ($we.Status -eq [System.Net.WebExceptionStatus]::Timeout) {
                throw [ZxNextRemoteError]::new(
                    ('{0}: no answer within {1} s' -f $shortRoute, $timeoutSec),
                    0, [ZxNextRemoteError]::Timeout, $shortRoute, $we.Message)
            }
            throw [ZxNextRemoteError]::new(
                ('{0}: the bridge at {1} could not be reached ({2})' -f
                    $shortRoute, $con.BaseUrl(), $we.Message),
                0, [ZxNextRemoteError]::BridgeUnreachable, $shortRoute, $we.Message)
        } catch {
            # pwsh 7's HttpWebRequest shim streams the body: a transfer
            # dropped MID-BODY throws IOException/HttpRequestException out
            # of the read, never WebException. Keep the typed-error
            # contract - every failure a caller sees is a ZxNextRemoteError.
            $inner = $_.Exception
            $reason = [ZxNextRemoteError]::BridgeUnreachable
            if ($inner.Message -match '(?i)timed? ?out|cancell?ed') {
                $reason = [ZxNextRemoteError]::Timeout
            }
            throw [ZxNextRemoteError]::new(
                ('{0}: the transfer failed mid-flight ({1})' -f
                    $shortRoute, $inner.Message),
                0, $reason, $shortRoute, $inner.Message)
        } finally {
            if ($null -ne $resp) { $resp.Close() }
        }
    }

    static [byte[]] ReadAll([System.Net.WebResponse]$resp) {
        $ms = New-Object System.IO.MemoryStream
        $stream = $resp.GetResponseStream()
        try {
            $stream.CopyTo($ms)
        } finally {
            $stream.Close()
        }
        return $ms.ToArray()
    }

    static [object] ParseJson([byte[]]$bytes, [string]$route) {
        $text = [System.Text.Encoding]::UTF8.GetString($bytes)
        try {
            return ($text | ConvertFrom-Json)
        } catch {
            throw [ZxNextRemoteError]::new(
                ('{0}: the bridge answered something that is not JSON' -f $route),
                200, [ZxNextRemoteError]::HttpError, $route, $text)
        }
    }

    # Pull the human error line out of an error body - JSON {"error": ...}
    # when json=1 was on the request, 'ERR ...' text otherwise.
    static [string] ErrorText([byte[]]$bytes) {
        $text = [System.Text.Encoding]::UTF8.GetString($bytes).Trim()
        if ($text.StartsWith('{')) {
            try {
                $j = $text | ConvertFrom-Json
                if ($j.PSObject.Properties['error']) { return [string]$j.error }
            } catch {
                # Not JSON after all - fall through to the text shapes.
                Write-Debug ('ErrorText: not JSON ({0})' -f $_.Exception.Message)
            }
        }
        if ($text.StartsWith('ERR ')) { return $text.Substring(4) }
        return $text
    }

    # HTTP status -> typed reason. The 401 split is the load-bearing one:
    # the bridge answers 401 both for a missing/wrong bearer token AND for
    # a write the remote machine's OS protection refused - only the body
    # tells them apart ('os-protected: ...' is the OSP marker, see
    # RE_OSP_ERROR in zxnu_workers.py and the note in HTTP_BRIDGE.md).
    static [ZxNextRemoteError] Classify([int]$status, [string]$detail,
                                        [string]$route) {
        $reason = [ZxNextRemoteError]::HttpError
        $advice = ''
        if ($status -eq 401) {
            if ($detail -match 'os-protected') {
                $reason = [ZxNextRemoteError]::OsProtected
                $advice = ('the remote machine refused the write (OS ' +
                           'protection). Adjust the "OS protection" setting ' +
                           'or its folder list ON THAT MACHINE - the token ' +
                           'is fine, and reads still work.')
            } else {
                $reason = [ZxNextRemoteError]::TokenRequired
                $advice = ('the bridge requires a bearer token (or the one ' +
                           'supplied is wrong). Copy the token from ' +
                           'ZX-Next-Unite -> Settings -> "Require bearer ' +
                           'token" into the connection.')
            }
        } elseif ($status -eq 410) {
            $reason = [ZxNextRemoteError]::SessionGone
            $advice = ('that session has left the bridge. Call Session() ' +
                       'again and pick a live seat.')
        } elseif ($status -eq 503) {
            $reason = [ZxNextRemoteError]::NoNextConnected
            $advice = ('the bridge is up but no Next is connected - run ' +
                       '.sync5 -listen (or a ZX Next Remote Listener) on ' +
                       'the Next first.')
        } elseif ($status -eq 504) {
            $reason = [ZxNextRemoteError]::Timeout
            $advice = 'the Next did not answer in time.'
        } elseif ($status -eq 400) {
            $reason = [ZxNextRemoteError]::BadRequest
            $advice = 'the bridge rejected the arguments.'
        } elseif ($status -eq 501) {
            $reason = [ZxNextRemoteError]::Unsupported
            $advice = 'this bridge host does not implement that verb.'
        } elseif ($status -eq 502) {
            $reason = [ZxNextRemoteError]::NextRefused
            $advice = 'the Next reported a failure.'
        }
        $msg = ('{0}: HTTP {1} - {2}' -f $route, $status, $detail)
        if ($advice) { $msg = $msg + ' [' + $advice + ']' }
        return [ZxNextRemoteError]::new($msg, $status, $reason, $route, $detail)
    }
}

# ---------------------------------------------------------------------------
# One entry of a directory listing. Path is pre-joined so an entry can be
# handed straight back to Get()/Rm()/Sum() - the user's
# $session.get($list[0]) just works.
# ---------------------------------------------------------------------------
class ZxNextRemoteFileEntry {
    [bool]   $Dir
    [long]   $Size
    [string] $Name
    [string] $Path

    [string] ToString() {
        $kind = 'F'
        if ($this.Dir) { $kind = 'D' }
        return ('{0}  {1,10}  {2}' -f $kind, $this.Size, $this.Name)
    }
}

# One seat on the bridge (a connected Next), as /sessions reports it.
class ZxNextSessionInfo {
    [int]    $Sid
    [string] $Addr
    [string] $Name
    [string] $Label
    [bool]   $Active

    [string] ToString() {
        return $this.Label
    }
}

# The /sessions answer: Current = how many Nexts are connected, List = the
# seats. (Property access is case-insensitive, so $sessions.current and
# $sessions.list read naturally too.)
class ZxNextSessionList {
    [int]      $Current      # number of Nexts connected right now
    [object[]] $List         # ZxNextSessionInfo[]
    [object]   $Active       # sid of the app-active seat, or $null
    [int]      $Max          # seats the host offers (4 app / 1 standalone)

    [string] ToString() {
        return ('{0} of {1} seat(s) in use' -f $this.Current, $this.Max)
    }
}

# ---------------------------------------------------------------------------
# A session-scoped proxy: every call rides ONE seat (the sid is sent as the
# ZXNEXTUNITE-BRIDGE-SESSION header), so driving Next #2 never moves the
# app's active selection. Sid $null = "the active seat" (no selector - the
# exact pre-session behaviour).
# ---------------------------------------------------------------------------
class ZxNextRemoteSession {
    hidden [object] $Owner            # the ZxNextRemote that minted this
    hidden [ZxNextRemoteConnection] $Connection
    [object] $Sid                     # [int], or $null for the active seat
    [object] $Info                    # ZxNextSessionInfo when known

    # Usually minted by ZxNextRemote.ManageSession(), but constructible
    # standalone too - and the BEARER TOKEN rides along either way: it
    # lives on the connection, and every request this session makes sends
    # it as the ZXNEXTUNITE-BRIDGE-TOKEN header. The ip/port/token forms
    # build that connection inline for one-liner scripts.
    ZxNextRemoteSession() {
        # parameterless: ManageSession() fills the fields itself
    }
    ZxNextRemoteSession([ZxNextRemoteConnection]$connection) {
        $this.Connection = $connection
    }
    ZxNextRemoteSession([ZxNextRemoteConnection]$connection, [int]$sid) {
        $this.Connection = $connection
        $this.Sid = $sid
    }
    ZxNextRemoteSession([string]$ipAddress, [int]$port, [string]$token) {
        $this.Connection = [ZxNextRemoteConnection]::new($ipAddress, $port, $token)
    }
    ZxNextRemoteSession([string]$ipAddress, [int]$port, [string]$token, [int]$sid) {
        $this.Connection = [ZxNextRemoteConnection]::new($ipAddress, $port, $token)
        $this.Sid = $sid
    }

    hidden [void] EnsureOpen() {
        if ($null -ne $this.Owner -and $this.Owner.Closed) {
            throw [ZxNextRemoteError]::new(
                'this ZxNextRemote client has been Close()d',
                0, [ZxNextRemoteError]::ConnectionClosed, '', '')
        }
    }

    hidden [object] Call([string]$route, [string]$method, [byte[]]$body,
                         [int]$timeoutSec, [bool]$rawBytes) {
        $this.EnsureOpen()
        return [ZxNextBridgeHttp]::Invoke($this.Connection, $route, $method,
                                          $body, $timeoutSec, $rawBytes,
                                          $this.Sid)
    }

    # ---- listing ----------------------------------------------------------
    [object[]] Ls() {
        return $this.Ls('/')
    }
    [object[]] Ls([string]$path) {
        $j = $this.Call(('/ls?path={0}&json=1' -f [ZxNextBridgeHttp]::Esc($path)),
                        'GET', $null, $this.Connection.TimeoutSec, $false)
        $base = $path.TrimEnd('/')
        $out = @()
        foreach ($e in $j.entries) {
            $entry = [ZxNextRemoteFileEntry]::new()
            $entry.Dir = [bool]$e.dir
            $entry.Size = [long]$e.size
            $entry.Name = [string]$e.name
            $entry.Path = $base + '/' + $e.name
            $out += $entry
        }
        return $out
    }

    # True when $path names an existing file OR directory. The bridge has no
    # stat verb, so this lists the parent and scans it - one quick round
    # trip. A parent that does not exist (the Next refuses the ls) is
    # simply $false, not an error; a ROOT ('/', 'm:', 'm:/') has no parent
    # to list, so it is probed by listing the root itself.
    [bool] Exists([string]$path) {
        $trimmed = $path.TrimEnd('/')
        if ($trimmed -eq '' -or $trimmed -match '^[A-Za-z]:$') {
            try {
                [void]$this.Ls($path)
                return $true
            } catch [ZxNextRemoteError] {
                if ($_.Exception.Reason -eq [ZxNextRemoteError]::NextRefused) { return $false }
                throw
            }
        }
        $slash = $trimmed.LastIndexOf('/')
        $parent = '/'
        $leaf = $trimmed
        if ($slash -ge 0) {
            $parent = $trimmed.Substring(0, $slash)
            if ($parent -eq '' -or $parent -match '^[A-Za-z]:$') { $parent = $parent + '/' }
            $leaf = $trimmed.Substring($slash + 1)
        }
        $entries = $null
        try {
            $entries = $this.Ls($parent)
        } catch [ZxNextRemoteError] {
            # A parent the Next cannot list means the path cannot exist.
            # Anything else (token, session gone, no Next, ...) stays loud.
            if ($_.Exception.Reason -eq [ZxNextRemoteError]::NextRefused) { return $false }
            throw
        }
        foreach ($e in $entries) {
            if ($e.Name -ieq $leaf) { return $true }
        }
        return $false
    }

    # ---- download ---------------------------------------------------------
    [byte[]] Get([string]$remotePath) {
        return [byte[]]$this.Call(
            ('/get?path={0}' -f [ZxNextBridgeHttp]::Esc($remotePath)),
            'GET', $null, $this.Connection.LongTimeoutSec, $true)
    }
    [System.IO.FileInfo] Get([string]$remotePath, [string]$localPath) {
        $bytes = $this.Get($remotePath)
        $localPath = [ZxNextBridgeHttp]::ResolveLocal($localPath)
        [System.IO.File]::WriteAllBytes($localPath, $bytes)
        return (Get-Item -LiteralPath $localPath)
    }
    [byte[]] Get([ZxNextRemoteFileEntry]$entry) {
        if ($entry.Dir) {
            throw [ZxNextRemoteError]::new(
                ('{0} is a directory - list it with Ls() and fetch file by file' -f $entry.Path),
                0, [ZxNextRemoteError]::BadRequest, '/get', '')
        }
        return $this.Get($entry.Path)
    }
    [System.IO.FileInfo] Get([ZxNextRemoteFileEntry]$entry, [string]$localPath) {
        if ($entry.Dir) {
            throw [ZxNextRemoteError]::new(
                ('{0} is a directory - list it with Ls() and fetch file by file' -f $entry.Path),
                0, [ZxNextRemoteError]::BadRequest, '/get', '')
        }
        return $this.Get($entry.Path, $localPath)
    }

    # ---- upload -----------------------------------------------------------
    [psobject] Put([byte[]]$data, [string]$remotePath) {
        $j = $this.Call(('/put?path={0}&json=1' -f [ZxNextBridgeHttp]::Esc($remotePath)),
                        'POST', $data, $this.Connection.LongTimeoutSec, $false)
        return [pscustomobject]@{ Path = [string]$j.path; Bytes = [long]$j.bytes }
    }
    [psobject] Put([string]$localPath, [string]$remotePath) {
        $bytes = [System.IO.File]::ReadAllBytes(
            [ZxNextBridgeHttp]::ResolveLocal($localPath))
        return $this.Put($bytes, $remotePath)
    }

    # ---- verification -----------------------------------------------------
    [psobject] Sum([string]$remotePath) {
        $j = $this.Call(('/sum?path={0}&json=1' -f [ZxNextBridgeHttp]::Esc($remotePath)),
                        'GET', $null, $this.Connection.LongTimeoutSec, $false)
        return [pscustomobject]@{
            Path  = [string]$j.path
            Bytes = [long]$j.bytes
            Sum16 = [int]$j.sum16
        }
    }

    # End-to-end check of an uploaded file: compares the LOCAL size and
    # 16-bit additive checksum against what /sum reads back off the Next.
    [bool] Verify([string]$localPath, [string]$remotePath) {
        $bytes = [System.IO.File]::ReadAllBytes(
            [ZxNextBridgeHttp]::ResolveLocal($localPath))
        $local = 0
        foreach ($b in $bytes) { $local += $b }
        $local = $local -band 0xFFFF
        $remote = $this.Sum($remotePath)
        return ($remote.Bytes -eq $bytes.Length -and $remote.Sum16 -eq $local)
    }

    [psobject] Sum([ZxNextRemoteFileEntry]$entry) {
        if ($entry.Dir) {
            throw [ZxNextRemoteError]::new(
                ('{0} is a directory - Sum() reads one file (RfSize() sizes a tree)' -f $entry.Path),
                0, [ZxNextRemoteError]::BadRequest, '/sum', '')
        }
        return $this.Sum($entry.Path)
    }

    # ---- directory / file management -------------------------------------
    [psobject] MkDir([string]$path) {
        $j = $this.Call(('/mkdir?path={0}&json=1' -f [ZxNextBridgeHttp]::Esc($path)),
                        'GET', $null, $this.Connection.TimeoutSec, $false)
        return [pscustomobject]@{ Path = [string]$j.path }
    }
    [psobject] RmDir([string]$path) {
        $j = $this.Call(('/rmdir?path={0}&json=1' -f [ZxNextBridgeHttp]::Esc($path)),
                        'GET', $null, $this.Connection.TimeoutSec, $false)
        return [pscustomobject]@{ Path = [string]$j.path }
    }
    [psobject] RmTree([string]$path) {
        $j = $this.Call(('/rmtree?path={0}&json=1' -f [ZxNextBridgeHttp]::Esc($path)),
                        'GET', $null, $this.Connection.LongTimeoutSec, $false)
        return [pscustomobject]@{ Path = [string]$j.path }
    }
    [psobject] Rm([string]$path) {
        $j = $this.Call(('/rm?path={0}&json=1' -f [ZxNextBridgeHttp]::Esc($path)),
                        'GET', $null, $this.Connection.TimeoutSec, $false)
        return [pscustomobject]@{ Path = [string]$j.path }
    }
    [psobject] Rm([ZxNextRemoteFileEntry]$entry) {
        if ($entry.Dir) {
            throw [ZxNextRemoteError]::new(
                ('{0} is a directory - use RmDir() (empty) or RmTree()' -f $entry.Path),
                0, [ZxNextRemoteError]::BadRequest, '/rm', '')
        }
        return $this.Rm($entry.Path)
    }
    [psobject] Ren([string]$from, [string]$to) {
        $j = $this.Call(('/ren?from={0}&to={1}&json=1' -f
                            [ZxNextBridgeHttp]::Esc($from), [ZxNextBridgeHttp]::Esc($to)),
                        'GET', $null, $this.Connection.TimeoutSec, $false)
        return [pscustomobject]@{ From = [string]$j.from; To = [string]$j.to }
    }
    [psobject] Rcpy([string]$src, [string]$dst) {
        $j = $this.Call(('/rcpy?src={0}&dst={1}&json=1' -f
                            [ZxNextBridgeHttp]::Esc($src), [ZxNextBridgeHttp]::Esc($dst)),
                        'GET', $null, $this.Connection.LongTimeoutSec, $false)
        return [pscustomobject]@{
            Src = [string]$j.src; Dst = [string]$j.dst; Files = [int]$j.files
        }
    }
    [psobject] RfSize([string]$path) {
        $j = $this.Call(('/rfsize?path={0}&json=1' -f [ZxNextBridgeHttp]::Esc($path)),
                        'GET', $null, $this.Connection.LongTimeoutSec, $false)
        return [pscustomobject]@{
            Path = [string]$j.path; Files = [int]$j.files
            Dirs = [int]$j.dirs;   Bytes = [long]$j.bytes
            Human = [string]$j.human
        }
    }

    # ---- drive info -------------------------------------------------------
    [psobject] Drives() {
        $j = $this.Call('/drives?json=1', 'GET', $null,
                        $this.Connection.TimeoutSec, $false)
        return [pscustomobject]@{
            Current = [string]$j.current
            Drives = @($j.drives)
            Partitions = [int]$j.partitions
        }
    }
    [psobject] Free() {
        return $this.Free('')
    }
    [psobject] Free([string]$drive) {
        $route = '/free?json=1'
        if ($drive) { $route = ('/free?drive={0}&json=1' -f [ZxNextBridgeHttp]::Esc($drive)) }
        $j = $this.Call($route, 'GET', $null, $this.Connection.TimeoutSec, $false)
        return [pscustomobject]@{
            Drive = [string]$j.drive
            FreeBytes = [long]$j.free_bytes
            FreeHuman = [string]$j.free_human
        }
    }

    # ---- leaving -listen --------------------------------------------------
    # NOTE: /forceexit takes no session selector - it ends EVERY seated
    # session (the stop is a broadcast). Kept here for the user's
    # convenience, with the same meaning as ZxNextRemote.ForceExit().
    [void] ForceExit() {
        $this.EnsureOpen()
        [void][ZxNextBridgeHttp]::Invoke($this.Connection, '/forceexit?json=1',
                                         'GET', $null,
                                         $this.Connection.TimeoutSec, $false,
                                         $null)
    }

    [string] ToString() {
        if ($null -ne $this.Info) { return ('session {0}' -f $this.Info.Label) }
        if ($null -ne $this.Sid)  { return ('session #{0}' -f $this.Sid) }
        return 'session (active seat)'
    }
}

# ---------------------------------------------------------------------------
# The client root: one per bridge. Instantiate once, list the seats with
# Session(), bind one with ManageSession(), then drive that Next.
# ---------------------------------------------------------------------------
class ZxNextRemote {
    [ZxNextRemoteConnection] $Connection
    [bool] $Closed = $false

    ZxNextRemote([ZxNextRemoteConnection]$connection) {
        $this.Connection = $connection
    }
    # The samples' pattern: set $bearer = '' at the top of the script, then
    # always pass it here. A non-empty bearer token is written onto the
    # connection and rides EVERY request from then on (the
    # ZXNEXTUNITE-BRIDGE-TOKEN header - sessions minted by ManageSession
    # inherit it too, since they share this connection). '' means "no
    # token": whatever the connection already carries is left untouched.
    ZxNextRemote([ZxNextRemoteConnection]$connection, [string]$bearerToken) {
        $this.Connection = $connection
        if ($bearerToken) { $connection.Token = $bearerToken }
    }
    ZxNextRemote([string]$ipAddress, [int]$port, [string]$bearerToken) {
        $this.Connection = [ZxNextRemoteConnection]::new($ipAddress, $port, $bearerToken)
    }

    hidden [void] EnsureOpen() {
        if ($this.Closed) {
            throw [ZxNextRemoteError]::new(
                'this ZxNextRemote client has been Close()d',
                0, [ZxNextRemoteError]::ConnectionClosed, '', '')
        }
    }

    hidden [object] Call([string]$route) {
        $this.EnsureOpen()
        return [ZxNextBridgeHttp]::Invoke($this.Connection, $route, 'GET',
                                          $null, $this.Connection.TimeoutSec,
                                          $false, $null)
    }

    # Is the bridge reachable at all? Never throws.
    [bool] Test() {
        try {
            [void]$this.Status()
            return $true
        } catch {
            return $false
        }
    }

    # GET /status - the bridge's health line, typed.
    [psobject] Status() {
        $j = $this.Call('/status?json=1')
        $sessions = $null
        $active = $null
        if ($j.PSObject.Properties['sessions']) { $sessions = [int]$j.sessions }
        if ($j.PSObject.Properties['active'])   { $active = $j.active }
        return [pscustomobject]@{
            Listening  = [bool]$j.listening
            Connected  = [bool]$j.connected
            Current    = [string]$j.current
            Drives     = @($j.drives)
            Partitions = [int]$j.partitions
            InFlight   = [int]$j.inflight
            Sessions   = $sessions   # $null on a single-session host
            Active     = $active     # $null on a single-session host
        }
    }

    # GET /sessions - who is seated. .Current = number of connected Nexts,
    # .List = the seats (ZxNextSessionInfo[]).
    [ZxNextSessionList] Session() {
        $j = $this.Call('/sessions?json=1')
        $list = @()
        foreach ($s in $j.sessions) {
            $info = [ZxNextSessionInfo]::new()
            $info.Sid = [int]$s.sid
            $info.Addr = [string]$s.addr
            $info.Name = [string]$s.name
            $info.Label = [string]$s.label
            $info.Active = [bool]$s.active
            $list += $info
        }
        $out = [ZxNextSessionList]::new()
        $out.Current = [int]$j.count
        $out.List = $list
        $out.Active = $j.active
        $out.Max = [int]$j.max
        return $out
    }

    # Bind one seat: every call on the returned session object rides that
    # sid and never moves the app's active selection.
    [ZxNextRemoteSession] ManageSession([ZxNextSessionInfo]$info) {
        $s = $this.NewSession()
        $s.Sid = $info.Sid
        $s.Info = $info
        return $s
    }
    [ZxNextRemoteSession] ManageSession([int]$sid) {
        $s = $this.NewSession()
        $s.Sid = $sid
        return $s
    }
    # No argument: the ACTIVE seat (no selector on the wire - requests
    # follow the app's own selection, the pre-session behaviour).
    [ZxNextRemoteSession] ManageSession() {
        return $this.NewSession()
    }

    hidden [ZxNextRemoteSession] NewSession() {
        $this.EnsureOpen()
        $s = [ZxNextRemoteSession]::new()
        $s.Owner = $this
        $s.Connection = $this.Connection
        $s.Sid = $null
        $s.Info = $null
        return $s
    }

    # GET /forceexit - tell the connected Next(s) to leave -listen and exit
    # cleanly to BASIC. A broadcast: EVERY seated session ends.
    [void] ForceExit() {
        [void]$this.Call('/forceexit?json=1')
    }

    # GET /help - the bridge's own route reference (plain text).
    [string] Help() {
        $this.EnsureOpen()
        $bytes = [ZxNextBridgeHttp]::Invoke($this.Connection, '/help', 'GET',
                                            $null, $this.Connection.TimeoutSec,
                                            $true, $null)
        return [System.Text.Encoding]::UTF8.GetString([byte[]]$bytes)
    }

    # Disconnect this CLIENT from the bridge - deliberately NOT a
    # /forceexit: nothing is sent, the Next stays in -listen, and only this
    # object refuses further calls. (HTTP is stateless; there is no server
    # session to tear down.)
    [void] Close() {
        $this.Closed = $true
    }

    [string] ToString() {
        $state = 'open'
        if ($this.Closed) { $state = 'closed' }
        return ('ZxNextRemote({0}, {1})' -f $this.Connection.BaseUrl(), $state)
    }
}

# ===========================================================================
# Exported functions - the Import-Module surface (classes need `using
# module`; these construct the same objects for everyone else).
# ===========================================================================

function New-ZxNextRemoteConnection {
    <#
    .SYNOPSIS
        Describes where a ZX-Next-Unite NextSync HTTP bridge is listening.
    .DESCRIPTION
        Builds the connection-settings object every ZxNextRemote client is
        constructed from: the bridge machine's address and port, the
        optional bearer token (ZX-Next-Unite -> Settings -> "Require bearer
        token"), and the two timeout tiers (quick verbs / long transfers).

        Nothing is contacted yet - this only describes the endpoint.
    .PARAMETER IpAddress
        Address of the machine running the bridge (the PC running
        ZX-Next-Unite or nextsync5.py -w) - NOT the Next itself.
    .PARAMETER Port
        The bridge's TCP port. 80 by default, matching the bridge.
    .PARAMETER Token
        The shared secret, when the bridge has "Require bearer token" on.
        Sent as the ZXNEXTUNITE-BRIDGE-TOKEN header on every request.
        Omit (or pass '') for an unprotected bridge.
    .PARAMETER TimeoutSec
        Client-side timeout for quick verbs (ls, status, ren, mkdir, ...).
        Default 60 s - the bridge itself gives up after 45 s.
    .PARAMETER LongTimeoutSec
        Client-side timeout for transfers and tree walks (get, put, sum,
        rcpy, rfsize, rmtree). Default 900 s - comfortably past the
        bridge's own 270 s transfer cap, so its 504 (which names the
        stalled op) is what a caller sees, never a silent client drop.
    .EXAMPLE
        $con = New-ZxNextRemoteConnection -IpAddress 10.0.0.8
    .EXAMPLE
        $con = New-ZxNextRemoteConnection -IpAddress 10.0.0.8 -Port 8080 -Token $env:ZXNU_TOKEN
    .OUTPUTS
        ZxNextRemoteConnection
    .LINK
        New-ZxNextRemote
    .LINK
        about_ZxNextRemote
    #>
    # New-* trips PSUseShouldProcessForStateChangingFunctions, but this
    # builds an in-memory settings object - nothing on the system changes,
    # so a -WhatIf would be dead weight.
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute(
        'PSUseShouldProcessForStateChangingFunctions', '',
        Justification = 'constructs an in-memory object; no system state is touched')]
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, Position = 0)]
        [string]$IpAddress,
        [Parameter(Position = 1)]
        [int]$Port = 80,
        [string]$Token = '',
        [int]$TimeoutSec = 0,
        [int]$LongTimeoutSec = 0
    )
    $con = [ZxNextRemoteConnection]::new($IpAddress, $Port, $Token)
    if ($TimeoutSec -gt 0) { $con.TimeoutSec = $TimeoutSec }
    if ($LongTimeoutSec -gt 0) { $con.LongTimeoutSec = $LongTimeoutSec }
    return $con
}

function New-ZxNextRemote {
    <#
    .SYNOPSIS
        Creates a ZxNextRemote client for a NextSync HTTP bridge.
    .DESCRIPTION
        The client root object. List the connected Nexts with .Session(),
        bind one with .ManageSession(...), then drive its SD card through
        the returned session object (.Ls() .Get() .Put() .Sum() .Ren()
        .Rcpy() .MkDir() .Rm() .RfSize() .Free() .Drives() ...).

        Every failure is thrown as a typed ZxNextRemoteError whose .Reason
        names the failure class - including the bridge's two DIFFERENT
        401s: 'TokenRequired' (bearer token missing/wrong - fix the
        connection) and 'OsProtected' (the remote machine's OS protection
        refused a write - fix it on that machine; reads still work).
        See Get-Help about_ZxNextRemote for the full class reference.
    .PARAMETER Connection
        A connection built by New-ZxNextRemoteConnection.
    .PARAMETER IpAddress
        Shortcut: build the connection inline from an address...
    .PARAMETER Port
        ... a port (default 80) ...
    .PARAMETER Token
        Optional bearer token (either parameter set): the samples' pattern
        is $bearer = '' at the top of the script, always passed here. A
        non-empty value is set on the connection and rides every request;
        '' leaves the connection's own token untouched.
    .EXAMPLE
        $remote   = New-ZxNextRemote -IpAddress 10.0.0.8
        $sessions = $remote.Session()
        "$($sessions.Current) Next(s) connected"
        $s = $remote.ManageSession($sessions.List[0])
        $s.Ls('/games') | Format-Table
    .EXAMPLE
        $con = New-ZxNextRemoteConnection 10.0.0.8 80 -Token $tok
        $s = (New-ZxNextRemote -Connection $con).ManageSession()
        $s.Put('build\game.nex', '/dev/incoming.nex')
        if (-not $s.Verify('build\game.nex', '/dev/incoming.nex')) { throw 'bad copy' }
    .OUTPUTS
        ZxNextRemote
    .LINK
        New-ZxNextRemoteConnection
    .LINK
        about_ZxNextRemote
    #>
    # Same as New-ZxNextRemoteConnection: an in-memory client object.
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute(
        'PSUseShouldProcessForStateChangingFunctions', '',
        Justification = 'constructs an in-memory object; no system state is touched')]
    [CmdletBinding(DefaultParameterSetName = 'ByConnection')]
    param(
        [Parameter(Mandatory, Position = 0, ParameterSetName = 'ByConnection')]
        [ZxNextRemoteConnection]$Connection,
        [Parameter(Mandatory, ParameterSetName = 'ByAddress')]
        [string]$IpAddress,
        [Parameter(ParameterSetName = 'ByAddress')]
        [int]$Port = 80,
        [string]$Token = ''
    )
    if ($PSCmdlet.ParameterSetName -eq 'ByAddress') {
        $Connection = [ZxNextRemoteConnection]::new($IpAddress, $Port, $Token)
    }
    return [ZxNextRemote]::new($Connection, $Token)
}

function Test-ZxNextRemoteBridge {
    <#
    .SYNOPSIS
        Asks a NextSync HTTP bridge for its status; $null when unreachable.
    .DESCRIPTION
        One /status round trip, returned as a typed object (Listening,
        Connected, Current, Drives, Partitions, InFlight, and - on the
        multi-session app host - Sessions/Active). Answers $null instead
        of throwing when the bridge cannot be reached, so it can sit in a
        wait-until-up polling loop. A token failure DOES throw (reason
        'TokenRequired') - a wrong token would otherwise poll for ever.
    .PARAMETER Connection
        A connection built by New-ZxNextRemoteConnection.
    .EXAMPLE
        while (-not ($st = Test-ZxNextRemoteBridge $con) -or -not $st.Connected) {
            Start-Sleep 2
        }
    .OUTPUTS
        psobject (the status), or $null when the bridge is unreachable.
    .LINK
        New-ZxNextRemoteConnection
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, Position = 0)]
        [ZxNextRemoteConnection]$Connection
    )
    $remote = [ZxNextRemote]::new($Connection)
    try {
        return $remote.Status()
    } catch [ZxNextRemoteError] {
        if ($_.Exception.Reason -eq [ZxNextRemoteError]::TokenRequired) { throw }
        return $null
    }
}

Set-Alias -Name Connect-ZxNextRemote -Value New-ZxNextRemote

Export-ModuleMember -Function New-ZxNextRemoteConnection, New-ZxNextRemote, `
    Test-ZxNextRemoteBridge -Alias Connect-ZxNextRemote
