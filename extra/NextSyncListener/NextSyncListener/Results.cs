using System;
using System.Collections.Generic;

namespace NextSync.Listener
{
    /// <summary>How a command ended. Every result carries one.</summary>
    public enum NextSyncStatus
    {
        /// <summary>The Next did what was asked.</summary>
        Ok,

        /// <summary>The Next answered a plain <c>'F'</c>: the file did not open, the folder is not empty, the rename was refused...</summary>
        Failed,

        /// <summary>The Next refused with the marked <c>'F'</c>+<c>OSP</c>: ZX Next Remote's OS protection (see <see cref="OsProtection"/>).</summary>
        OsProtected,

        /// <summary>The listener on the Next predates this opcode (it answered with silence and polled again).</summary>
        NotSupported,

        /// <summary>The reply never completed: the link dropped, the reply timed out, or a frame was malformed.</summary>
        ConnectionDropped,

        /// <summary>The command was taken off the queue before it ran: a cancellation token, <see cref="NextSyncListener.Stop()"/>, or the Next it was queued for left.</summary>
        Cancelled,

        /// <summary>A local problem before a byte moved (a file that could not be read, a folder that could not be created).</summary>
        LocalError
    }

    /// <summary>The common part of every command result.</summary>
    public class NextSyncResult
    {
        /// <summary>How the command ended.</summary>
        public NextSyncStatus Status { get; internal set; }

        /// <summary>A human-readable reason when <see cref="Status"/> is not <see cref="NextSyncStatus.Ok"/> (English, protocol wording).</summary>
        public string Error { get; internal set; }

        /// <summary>True when <see cref="Status"/> is <see cref="NextSyncStatus.Ok"/>.</summary>
        public bool Ok
        {
            get { return Status == NextSyncStatus.Ok; }
        }

        /// <summary>True when the far side's OS protection refused the operation.</summary>
        public bool IsOsProtected
        {
            get { return Status == NextSyncStatus.OsProtected; }
        }

        public override string ToString()
        {
            return Ok ? "Ok" : Status + ": " + Error;
        }
    }

    /// <summary>One entry of a directory listing.</summary>
    public sealed class RemoteEntry
    {
        public RemoteEntry(string name, bool isDirectory, long size)
        {
            Name = name;
            IsDirectory = isDirectory;
            Size = size;
        }

        /// <summary>The name as the Next reported it (no path).</summary>
        public string Name { get; private set; }

        /// <summary>Flags bit 0 of the entry.</summary>
        public bool IsDirectory { get; private set; }

        /// <summary>The 32-bit little-endian size the Next reported (0 for a directory).</summary>
        public long Size { get; private set; }

        public override string ToString()
        {
            return IsDirectory ? "<DIR> " + Name : Size + " " + Name;
        }
    }

    /// <summary>Result of <see cref="NextSyncListener.ListAsync"/>.</summary>
    public sealed class ListingResult : NextSyncResult
    {
        public ListingResult() { Entries = new List<RemoteEntry>(); }

        /// <summary>The path that was listed.</summary>
        public string Path { get; internal set; }

        /// <summary>The entries, directories first then by name (case-insensitive), as the Remote Explorer shows them.</summary>
        public IList<RemoteEntry> Entries { get; internal set; }
    }

    /// <summary>One file received by a get.</summary>
    public sealed class ReceivedFile
    {
        /// <summary>The name the Next sent in its <c>'N'</c> block (a full Next path for a directory get).</summary>
        public string RemoteName { get; internal set; }

        /// <summary>Where it was written, when the get targeted a local directory; null for an in-memory get.</summary>
        public string LocalPath { get; internal set; }

        /// <summary>The bytes, when the get was in-memory; null when written to disk.</summary>
        public byte[] Data { get; internal set; }

        /// <summary>How many bytes arrived for this file.</summary>
        public long Length { get; internal set; }
    }

    /// <summary>Result of <see cref="NextSyncListener.GetAsync"/> / <see cref="NextSyncListener.GetBytesAsync"/>.</summary>
    public sealed class GetResult : NextSyncResult
    {
        public GetResult() { Files = new List<ReceivedFile>(); }

        /// <summary>The path that was fetched (a file or a folder).</summary>
        public string RemotePath { get; internal set; }

        /// <summary>Every file the Next streamed back, in arrival order.</summary>
        public IList<ReceivedFile> Files { get; internal set; }

        /// <summary>Total payload bytes received.</summary>
        public long BytesReceived { get; internal set; }

        /// <summary>The first file's bytes for an in-memory get of one file (null otherwise).</summary>
        public byte[] Data
        {
            get { return Files.Count > 0 ? Files[0].Data : null; }
        }

        /// <summary>The last file written for a get to a directory (null otherwise).</summary>
        public string LastLocalPath
        {
            get { return Files.Count > 0 ? Files[Files.Count - 1].LocalPath : null; }
        }
    }

    /// <summary>Result of a put.</summary>
    public sealed class PutResult : NextSyncResult
    {
        public PutResult() { }

        /// <summary>The remote path the file was sent to (a trailing <c>/</c> in the request had the local name appended).</summary>
        public string RemotePath { get; internal set; }

        /// <summary>Bytes the Next had pulled when the put ended (the file size on success).</summary>
        public long BytesSent { get; internal set; }

        /// <summary>The file's size.</summary>
        public long BytesTotal { get; internal set; }

        /// <summary>How often the Next asked for the same packet again (a checksum failure on its side).</summary>
        public int Retries { get; internal set; }

        /// <summary>How often the Next started the file over (a packet-number mismatch).</summary>
        public int Restarts { get; internal set; }

        /// <summary>True when <see cref="NextSyncListener.VerifyPuts"/> was on and the Next's CRC-32 matched the bytes sent.</summary>
        public bool Verified { get; internal set; }

        /// <summary>Why a successful put was NOT verified (old listener, no answer...), or the verification verdict.</summary>
        public string VerificationNote { get; internal set; }

        /// <summary>True when the verification found the copy on the Next different from the bytes sent (the put then reports <see cref="NextSyncStatus.Failed"/>).</summary>
        public bool CrcMismatch { get; internal set; }
    }

    /// <summary>Result of mkdir / rmdir / rm / rename / release.</summary>
    public sealed class StatusResult : NextSyncResult
    {
        public StatusResult() { }

        /// <summary>The operation: <c>mkdir</c>, <c>rmdir</c>, <c>rm</c>, <c>rename</c>, <c>release</c>.</summary>
        public string Operation { get; internal set; }

        /// <summary>The path the operation named (the old path for a rename; empty for release).</summary>
        public string Path { get; internal set; }
    }

    /// <summary>Result of <see cref="NextSyncListener.GetDrivesAsync"/> (<c>'W'</c>, dot v5.1+).</summary>
    public sealed class DrivesResult : NextSyncResult
    {
        public DrivesResult() { Mounted = new List<string>(); }

        /// <summary>The Next's current drive letter, e.g. <c>"C"</c>.</summary>
        public string Current { get; internal set; }

        /// <summary>Every mounted drive letter, e.g. <c>["C", "M"]</c>.</summary>
        public IList<string> Mounted { get; internal set; }
    }

    /// <summary>Result of <see cref="NextSyncListener.GetFreeSpaceAsync"/> (<c>'Z'</c>, dot v5.2+).</summary>
    public sealed class FreeSpaceResult : NextSyncResult
    {
        public FreeSpaceResult() { }

        /// <summary>The drive letter asked about, normalised (<c>"m:"</c> becomes <c>"M"</c>); empty for the current drive.</summary>
        public string Drive { get; internal set; }

        /// <summary>Free bytes (the Next reports free 512-byte blocks; this is blocks * 512).</summary>
        public long FreeBytes { get; internal set; }
    }

    /// <summary>Result of <see cref="NextSyncListener.QueryIdentAsync"/> (<c>'Y'</c>, dot v5.8+ / ZX Next Remote 1.0.2+).</summary>
    public sealed class IdentResult : NextSyncResult
    {
        public IdentResult() { }

        /// <summary>Who answers the session and which build; null when the listener predates the query.</summary>
        public NextIdent Ident { get; internal set; }
    }

    /// <summary>Result of <see cref="NextSyncListener.GetCrc32Async"/> (<c>'K'</c>, dot v5.9.2+ / ZX Next Remote 1.0.8+ / QLNextRemote 0.1.0+).</summary>
    public sealed class CrcResult : NextSyncResult
    {
        public CrcResult() { }

        /// <summary>The file asked about.</summary>
        public string Path { get; internal set; }

        /// <summary>The 8 upper-case hex digits as the Next answered them.</summary>
        public string Crc32Hex { get; internal set; }

        /// <summary>The same value as a number (compare with <see cref="Crc32.Compute(byte[])"/>).</summary>
        public uint Crc32 { get; internal set; }
    }

    /// <summary>Result of <see cref="NextSyncListener.CopyOnNextAsync"/> (<c>'C'</c>, rcpy, dot v5.2+).</summary>
    public sealed class CopyResult : NextSyncResult
    {
        public CopyResult() { }

        public string Source { get; internal set; }

        public string Destination { get; internal set; }

        /// <summary>How many named progress blocks arrived, i.e. how many file copies the Next started.</summary>
        public int FilesCopied { get; internal set; }
    }

    /// <summary>Result of <see cref="NextSyncListener.GetSizeAsync"/> (<c>'S'</c>, rfsize, dot v5.2+).</summary>
    public sealed class SizeResult : NextSyncResult
    {
        public SizeResult() { }

        public string Path { get; internal set; }

        public long Files { get; internal set; }

        public long Directories { get; internal set; }

        /// <summary>Total bytes (48 bits on the wire: size_hi * 2^32 + size_lo).</summary>
        public long Bytes { get; internal set; }
    }

    /// <summary>Result of <see cref="NextSyncListener.DeleteTreeAsync"/>.</summary>
    public sealed class DeleteTreeResult : NextSyncResult
    {
        public DeleteTreeResult() { }

        public string Root { get; internal set; }

        public int FilesDeleted { get; internal set; }

        public int FoldersDeleted { get; internal set; }

        /// <summary>How many files or folders the Next refused to remove (the walk carries on past a plain refusal; an OS-protection refusal ends it).</summary>
        public int Failures { get; internal set; }
    }

    /// <summary>Result of <see cref="NextSyncListener.QuitAsync"/>.</summary>
    public sealed class QuitResult : NextSyncResult
    {
        public QuitResult() { }

        /// <summary>True when the marked quit (<c>'Q'</c>+<c>'X'</c>: leave listen mode AND end the application) was sent.</summary>
        public bool ExitApplication { get; internal set; }
    }

    /// <summary>
    /// The far responder's identity from the <c>'Y'</c> version query:
    /// <c>("sync", "5.9.14")</c> for the .sync5 dot, <c>("httpbridge"/"n2n"/"zxnt", "1.8.0")</c>
    /// for a ZX Next Remote listener, <c>("qlnextremote"/"qlnt", "1.7.0")</c> for a QL job.
    /// </summary>
    public sealed class NextIdent
    {
        public NextIdent(string type, string version)
        {
            Type = type ?? "";
            Version = version ?? "";
        }

        /// <summary>The listener type, as sent (e.g. <c>sync</c>).</summary>
        public string Type { get; private set; }

        /// <summary>The build number, as sent (e.g. <c>5.9.14</c>).</summary>
        public string Version { get; private set; }

        /// <summary>True when this listener is known to answer the <c>'K'</c> CRC-32 op (see <see cref="CrcFloors"/>).</summary>
        public bool AnswersCrc
        {
            get { return CrcFloors.PeerAnswersCrc(Type, Version); }
        }

        public override string ToString()
        {
            return Type + " " + Version;
        }
    }

    /// <summary>
    /// Listener builds that answer the <c>'K'</c> (crc) op, by <c>'Y'</c> ident
    /// type - a hand-kept twin of <c>RE_CRC_FLOORS</c> in ZX-Next-Unite's
    /// <c>zxnu_workers.py</c>: the .sync5 dot from 5.9.2, ZX Next Remote
    /// (httpbridge / n2n) from 1.0.8, ZX Next Transfer (zxnt) from 1.6.0,
    /// QLNextRemote and QL Next Transfer (qlnt) from 0.1.0. An older listener
    /// meets <c>'K'</c> with silence, so it is never asked blind.
    /// </summary>
    public static class CrcFloors
    {
        private static readonly Dictionary<string, int[]> Floors = new Dictionary<string, int[]>
        {
            { "sync", new[] { 5, 9, 2 } },
            { "httpbridge", new[] { 1, 0, 8 } },
            { "n2n", new[] { 1, 0, 8 } },
            { "zxnt", new[] { 1, 6, 0 } },
            { "qlnextremote", new[] { 0, 1, 0 } },
            { "qlnt", new[] { 0, 1, 0 } },
        };

        /// <summary>Parse a dotted version into integers; null when any part is not a number (or the string is empty).</summary>
        public static int[] ParseVersion(string version)
        {
            if (version == null) return null;
            var text = version.Trim();
            if (text.Length == 0) return null;
            var parts = text.Split('.');
            var numbers = new int[parts.Length];
            for (int i = 0; i < parts.Length; i++)
            {
                int n;
                if (!int.TryParse(parts[i], System.Globalization.NumberStyles.None,
                                  System.Globalization.CultureInfo.InvariantCulture, out n))
                    return null;
                numbers[i] = n;
            }
            return numbers;
        }

        /// <summary>Compare two parsed versions the way Python compares int tuples (a shorter prefix sorts first).</summary>
        public static int Compare(int[] a, int[] b)
        {
            int n = Math.Min(a.Length, b.Length);
            for (int i = 0; i < n; i++)
            {
                if (a[i] != b[i]) return a[i] < b[i] ? -1 : 1;
            }
            return a.Length.CompareTo(b.Length);
        }

        /// <summary>
        /// True when an ident names a listener that answers <c>'K'</c>. An unknown
        /// type, an empty or unparseable version => false (never probe blind).
        /// </summary>
        public static bool PeerAnswersCrc(string type, string version)
        {
            if (type == null) return false;
            int[] floor;
            if (!Floors.TryGetValue(type.Trim().ToLowerInvariant(), out floor)) return false;
            var v = ParseVersion(version);
            return v != null && Compare(v, floor) >= 0;
        }
    }
}
