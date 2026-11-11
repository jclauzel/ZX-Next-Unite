# NextSync Listener for .NET

The PC half of the NextSync **`.sync5 -listen`** Remote Explorer protocol as a
C# class library: a TCP server a **ZX Spectrum Next** dials into over Wi-Fi
(the `.sync5` dot's `-listen` / `-L`, ZX Next Remote's Listener, QLNextRemote
on a Sinclair QL), after which your program drives the machine - list folders,
get and put files, mkdir / rmdir / rm / rename, mounted drives, free space,
who is listening and which build, CRC-32 computed on the Next, release, quit.

It is a port of the listener worker inside
[ZX-Next-Unite](https://github.com/jclauzel/ZX-Next-Unite)
(`run_remote_listen_server` / `_re_session` in `zxnu_workers.py`), the one
the app's Remote Explorer has run against real hardware for a long time:
same framing and checksums, same poll loop, same retry, silence and goodbye
rules. It exists so a custom Remote Explorer can be written in .NET.

| | |
|---|---|
| Targets | **`net45`** (any .NET Framework 4.5 - 4.8 app: WinForms, WPF, console) and **`netstandard2.0`** (.NET Core 2.0+, .NET 5/6/7/8+, Mono) |
| Namespace | `NextSync.Listener` |
| Assembly / NuGet package | `NextSyncListener` |
| Release asset | `NextSyncListener-<tag>.zip` on each [GitHub release](https://github.com/jclauzel/ZX-Next-Unite/releases) |
| Dependencies | none (the BCL only) |
| License | MIT, like ZX-Next-Unite |

> **Verification status.** The library is proven against an *in-process fake
> Next* that replays the dot's half of the protocol exactly as the mocks in
> ZX-Next-Unite's `tests/test_remote_listen.py` do (the executable spec the
> Python worker is held to). On **real hardware** - a ZX Spectrum Next running
> `.sync5` 5.9.14, 2026-11-11 - the sample app's run was clean: the handshake,
> the version ident, drives, a 50-entry listing, free space and the quit.
> **Put, get and CRC-32 have not been run against a real Next yet**: their
> retry framing rests on the fake Next alone, so say so yourself until you have.

## Installing

**NuGet** (once the package is published):

```
dotnet add package NextSyncListener
```

or in a `.csproj`:

```xml
<PackageReference Include="NextSyncListener" Version="9.7.*" />
```

**The release zip** `NextSyncListener-<tag>.zip` holds `net45/` and
`netstandard2.0/` folders, each with `NextSyncListener.dll`, its XML
documentation and symbols. Reference the dll from the folder that matches
your target (a .NET Framework 4.5 project takes `net45/`; everything newer
takes `netstandard2.0/`).

## Building from source

Needs the .NET SDK 8 or newer (no .NET Framework targeting pack: the
`Microsoft.NETFramework.ReferenceAssemblies` package builds the `net45`
target on Linux and macOS too).

```
cd extra/NextSyncListener
dotnet build NextSyncListener.sln
dotnet test Tests/Tests.csproj
dotnet pack NextSyncListener/NextSyncListener.csproj -c Release -p:Version=9.7.62 -o out
```

`Sample/` is a net8.0 console app that starts the listener, waits for a Next
and lists its root:

```
dotnet run --project Sample -- 2048 /games
```

## Using it

The Next drives: it polls the server every second or two, every poll is
answered with one command, and the Next pushes the reply back. Your program
sees none of that - it queues commands and gets a `Task` per command that
completes when the Next has answered.

### .NET 8 (async)

```csharp
using NextSync.Listener;

using var listener = new NextSyncListener();            // every interface, port 2048
listener.Log += (s, e) => Console.WriteLine(e.Message);
listener.Connected += (s, e) => Console.WriteLine("Next at " + e.RemoteEndPoint);
listener.Disconnected += (s, e) => Console.WriteLine("Next left: " + e.Reason);
listener.Start();
Console.WriteLine("On the Next: .sync5 -listen");

ListingResult listing = await listener.ListAsync("/games");  // waits for a Next if none is seated
if (listing.Ok)
    foreach (RemoteEntry entry in listing.Entries)
        Console.WriteLine(entry.IsDirectory ? "<DIR> " + entry.Name : entry.Size + " " + entry.Name);
else
    Console.WriteLine(listing.Status + ": " + listing.Error);

PutResult put = await listener.PutAsync(@"C:\builds\game.nex", "/games/");   // trailing / = into that folder
Console.WriteLine(put.Ok ? "sent " + put.BytesSent + " bytes" : put.Error);

CrcResult crc = await listener.GetCrc32Async("/games/game.nex");            // computed ON the Next
if (crc.Ok && crc.Crc32 == Crc32.Compute(File.ReadAllBytes(@"C:\builds\game.nex")))
    Console.WriteLine("verified");

await listener.QuitAsync();        // the Next leaves listen mode (back to BASIC)
listener.Stop();
```

### .NET Framework 4.5 (no `await` needed)

Every method returns a `Task<T>`; `.Result` / `.Wait()` block the calling
thread only - completions run on the thread pool, never on the listener's
own thread, so this cannot deadlock the session.

```csharp
using System;
using NextSync.Listener;

class Program
{
    static void Main()
    {
        using (var listener = new NextSyncListener())
        {
            listener.Log += (s, e) => Console.WriteLine(e.Message);
            listener.Start();
            Console.WriteLine("On the Next: .sync5 -listen");

            var listing = listener.ListAsync("/").Result;
            if (listing.Ok)
                foreach (var entry in listing.Entries)
                    Console.WriteLine(entry);
            else
                Console.WriteLine(listing.Status + ": " + listing.Error);

            var put = listener.PutAsync(@"C:\builds\game.nex", "/games/").Result;
            Console.WriteLine(put.Ok ? "sent " + put.BytesSent + " bytes" : put.Error);

            listener.QuitAsync().Wait();
            listener.Stop();
        }
    }
}
```

In a WinForms or WPF app the events arrive on the listener's own threads:
marshal to the UI (`BeginInvoke` / `Dispatcher.BeginInvoke`) before touching a
control. Do not `.Result` on the UI thread either, or the window freezes
until the Next answers.

## The API

| Method | Wire op | Result |
|---|---|---|
| `ListAsync(path)` | `L` | `ListingResult` - entries, directories first then by name |
| `GetAsync(remote, localDirectory)` | `G` | `GetResult` - a file, or a whole folder recreated under its own name |
| `GetBytesAsync(remote)` | `G` | `GetResult` - `Data` holds the bytes |
| `PutAsync(localFile, remote)` / `PutAsync(byte[], remote)` | `P` + the Next's `Get`/`Retry`/`Restart` pulls | `PutResult` - bytes, retries, restarts, verification |
| `MakeDirectoryAsync(path)` | `M` | `StatusResult` |
| `RemoveDirectoryAsync(path)` | `R` | `StatusResult` (an EMPTY folder only) |
| `DeleteFileAsync(path)` | `X` | `StatusResult` |
| `RenameAsync(old, new)` | `V` | `StatusResult` |
| `ReleaseAsync()` | `U` | `StatusResult` (dot 5.9+ / ZXNR 1.0.3+; path-based ops only afterwards) |
| `GetDrivesAsync()` | `W` | `DrivesResult` - current + mounted letters (dot 5.1+) |
| `GetFreeSpaceAsync(drive)` | `Z` | `FreeSpaceResult` (dot 5.2+) |
| `QueryIdentAsync()` | `Y` | `IdentResult` - `sync 5.9.14`, `n2n 1.8.0`, ... (dot 5.8+ / ZXNR 1.0.2+); cached on `Ident` |
| `GetCrc32Async(path)` | `K` | `CrcResult` (dot 5.9.2+ / ZXNR 1.0.8+ / QLNR 0.1.0+) |
| `CopyOnNextAsync(src, dst)` | `C` | `CopyResult` - a copy that never crosses the wire (dot 5.2+) |
| `GetSizeAsync(path)` | `S` | `SizeResult` - a file or a whole tree (dot 5.2+) |
| `DeleteTreeAsync(path)` | `L`/`X`/`R` walk | `DeleteTreeResult` - bottom-up, one step per poll |
| `QuitAsync(exitApplication)` | `Q` / `Q`+`X` | `QuitResult`; the session ends, the listener keeps accepting |

Every result derives from `NextSyncResult`: `Ok`, `Status`, `Error`.

| `NextSyncStatus` | Meaning |
|---|---|
| `Ok` | done |
| `Failed` | the Next answered a plain `F` (file did not open, folder not empty, rename refused...) |
| `OsProtected` | the marked `F`+`OSP` refusal: ZX Next Remote's **OS protection** - a setting on the Next, not a network problem |
| `NotSupported` | the listener predates that opcode (it answered with silence and polled on) |
| `ConnectionDropped` | the reply never completed: link dropped, timed out, malformed |
| `Cancelled` | taken off the queue: a cancellation token, `Stop()`, or the Next it was queued for left |
| `LocalError` | a local file could not be read, a local folder not created |

Events: `Connected`, `Disconnected` (with a reason), `Log` (console-style
lines with a level), `Progress` (put / get bytes, copy and size walk items).
All are raised on the listener's accept or session thread; an exception
thrown by a handler is swallowed so it cannot kill the session.

Settings on `NextSyncListener`: `MaxPayload` (512, the clone-safe chunk;
1024 is the protocol's cap), `ReplyTimeout` (60 s between reply blocks),
`CrcTimeout` (1 h: the Next reads the whole file before it answers `K`),
`PeerSilenceLimit` (620 s of silence between commands = the Next is gone),
`HandshakeTimeout` (10 s for the `Listen` keyword), `GoodbyeLinger` (2 s for
a sent `Q` to be read), `VerifyPuts` (off: every put followed by the Next's
own CRC-32 against the bytes sent; a mismatch deletes the copy and fails the
put with `CrcMismatch`).

Helpers: `NextSyncFraming` (build / parse a frame, the two checksums),
`Crc32` (the IEEE value the Next computes), `CrcFloors` (which listener
builds answer `K`), `OsProtection` (the marker and its explanation),
`RemotePaths` (where a received file lands).

## How it behaves

- **One seat.** The first Next to say `Listen` is seated; a second dialer gets
  the framed `Busy` and is closed (what the dot prints as "Server busy").
  When the seated Next leaves the listener goes on accepting.
- **Queue rule.** A command queued while no Next is connected waits for the
  next one to dial in. A command queued while a Next WAS connected fails
  with `Cancelled` if that Next leaves before it ran: it is never run on a
  different machine. A command in flight cannot be cancelled (a half-written
  file on the card is worse than a slow cancel).
- **`Stop()`** sends the seated Next a `Q` at its next poll (so it returns to
  BASIC), waits up to the grace period for that, then closes. Queued commands
  fail with `Cancelled`. The listener can be started again.
- **Puts** are served in `MaxPayload`-byte frames at the Next's pace; a
  `Retry` resends the same frame, a `Restart` serves the file from the top;
  a `PutResult` says how many of each happened. The Task completes when the
  last data frame has been served (the Next then pulls one empty frame as its
  end of file). A listener that could not create the file answers `F` (or
  `F`+`OSP`); an old dot just polls on, which is reported as `Failed` too.
- **Gets** of a folder stream every file with its full Next path; they are
  written under the fetched folder's own name (`/games/lev` lands as
  `lev/...`), never under a copy of the whole Next path, and never outside
  the destination folder whatever the name says.
- **Late polls.** A seat that re-polls while the reply to `M`, `R` or `X` is
  owed is late, not gone (it missed the command): the poll is swallowed once
  and the reply awaited. Every other opcode keeps the old reading, where the
  raw poll means "I do not know that opcode" (how an old dot answers `Y`,
  `K`, `U`, `W`, `Z`): `NotSupported`, and the session carries on.
- **Verification** (`VerifyPuts`): the ident is queried once per session if
  nobody asked, `K` is sent only to a build known to answer it, and silence,
  `F` or a malformed digest keep the file and report the put done but
  `Verified = false`.

### What is deliberately NOT in the library

The classic Sync3/Sync4 sync server, the HTTP bridge, the app's multi-seat
roster and baton, the "Sessions Off" eviction and link-loss retries, the
remote self-update macro and the QL/Next stale-seat rules. Three small
differences from the Python worker, each on purpose: a link that dies under
an optional op's reply is `ConnectionDropped`, never "not supported"; a put
whose local file cannot be read answers that poll with an idle (the worker
leaves it unanswered); `Stop()` sends a `Q` before closing.

## Releases, CI and NuGet

`.github/workflows/dotnet-nextsync.yml` builds and tests the solution on
every push or pull request touching `extra/NextSyncListener/`. On a `v*`
tag it packs the library with the tag as its version and attaches
`NextSyncListener-<tag>.zip` and the `.nupkg` to the draft GitHub release
that `release.yml` creates (non-blocking: it waits for the draft and
degrades to a warning). The package is pushed to NuGet only when the release
is **published**, through NuGet **Trusted Publishing** (OIDC, the same
pattern as the app's PyPI publish): no API key is stored anywhere. The
one-time setup is a trust policy on nuget.org (account menu → *Trusted
Publishing*): Repository Owner `jclauzel`, Repository `ZX-Next-Unite`,
Workflow File `dotnet-nextsync.yml`, Environment empty. A new policy is
active for 7 days; the first successful login makes it permanent.

The asset names stay clear of the app's self-updater contract on purpose
(no `.exe`, no `linux` `.tar.gz`).
