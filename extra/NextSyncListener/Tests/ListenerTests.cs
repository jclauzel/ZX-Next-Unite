using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Xunit;

namespace NextSync.Listener.Tests
{
    /// <summary>A listener on a loopback port of the OS's choosing plus one fake Next seated on it.</summary>
    internal sealed class Harness : IDisposable
    {
        public readonly NextSyncListener Listener;
        public readonly FakeNext Next;
        public readonly List<string> Logs = new List<string>();
        public readonly List<NextDisconnectedEventArgs> Disconnects = new List<NextDisconnectedEventArgs>();
        public readonly List<TransferProgressEventArgs> Progress = new List<TransferProgressEventArgs>();
        public readonly ManualResetEvent ConnectedEvent = new ManualResetEvent(false);
        public readonly ManualResetEvent DisconnectedEvent = new ManualResetEvent(false);

        public Harness(Action<FakeNext> configure = null, Action<NextSyncListener> tune = null)
        {
            Listener = new NextSyncListener(IPAddress.Loopback, 0);
            Listener.Log += (s, e) => { lock (Logs) Logs.Add(e.Level + ": " + e.Message); };
            Listener.Connected += (s, e) => ConnectedEvent.Set();
            Listener.Disconnected += (s, e) => { lock (Disconnects) Disconnects.Add(e); DisconnectedEvent.Set(); };
            Listener.Progress += (s, e) => { lock (Progress) Progress.Add(e); };
            if (tune != null) tune(Listener);
            Listener.Start();
            Next = new FakeNext();
            if (configure != null) configure(Next);
            Seat(Next);
        }

        public void Seat(FakeNext next)
        {
            ConnectedEvent.Reset();
            next.Connect(Listener.LocalPort);
            Assert.True(ConnectedEvent.WaitOne(5000), "the listener never raised Connected");
            Assert.True(next.Handshaken.WaitOne(5000), "the fake never got Listening");
        }

        public bool LogContains(string fragment)
        {
            lock (Logs)
                foreach (var l in Logs) if (l.Contains(fragment)) return true;
            return false;
        }

        /// <summary>A log line is raised on the accept / session thread AFTER the wire event a test observes; give it a moment.</summary>
        public bool WaitForLog(string fragment, int ms = 3000)
        {
            var deadline = DateTime.UtcNow.AddMilliseconds(ms);
            while (DateTime.UtcNow < deadline)
            {
                if (LogContains(fragment)) return true;
                Thread.Sleep(10);
            }
            return false;
        }

        public void Dispose()
        {
            Listener.Dispose();
            Next.Dispose();
            if (Next.Error != null) throw new Exception("the fake Next failed: " + Next.Error);
        }
    }

    internal static class Wait
    {
        public static T For<T>(Task<T> task, int ms = 15000)
        {
            Assert.True(task.Wait(ms), "the command did not complete within " + ms + " ms");
            return task.Result;
        }
    }

    public class FramingTests
    {
        [Fact]
        public void Frames_match_the_python_reference_bytes()
        {
            // (len(payload)+5).to_bytes(2, "big") + payload + bytes([c0, c1, pkt])
            // - checksums computed with the Python reference for these words.
            Assert.Equal(new byte[] { 0x00, 0x0E, (byte)'L', (byte)'i', (byte)'s', (byte)'t', (byte)'e', (byte)'n', (byte)'i', (byte)'n', (byte)'g', 73, 16, 0 },
                         NextSyncFraming.Build("Listening", 0));
            Assert.Equal(new byte[] { 0x00, 0x07, (byte)'O', (byte)'k', 36, 115, 7 }, NextSyncFraming.Build("Ok", 7));
            Assert.Equal(new byte[] { 0x00, 0x06, (byte)'I', 73, 73, 0 }, NextSyncFraming.Build("I", 0));
            Assert.Equal(new byte[] { 0x00, 0x09, (byte)'B', (byte)'u', (byte)'s', (byte)'y', 61, 250, 0 }, NextSyncFraming.Build("Busy", 0));
            Assert.Equal(new byte[] { 0x00, 0x0A, (byte)'L', (byte)'a', (byte)'t', (byte)'e', (byte)'r', 78, 92, 0 }, NextSyncFraming.Build("Later", 0));
            Assert.Equal(new byte[] { 0x00, 0x09, (byte)'B', (byte)'a', (byte)'c', (byte)'k', 43, 208, 0 }, NextSyncFraming.Build("Back", 0));
            Assert.Equal(new byte[] { 0x00, 0x05, 0, 0, 255 }, NextSyncFraming.Build(new byte[0], 255));
        }

        [Fact]
        public void TryParse_round_trips_and_flags_a_bad_checksum()
        {
            var frame = NextSyncFraming.Build(new byte[] { 1, 2, 3, 250 }, 9);
            byte[] payload; byte pkt; bool csOk; int total;
            Assert.True(NextSyncFraming.TryParse(frame, 0, frame.Length, out payload, out pkt, out csOk, out total));
            Assert.Equal(new byte[] { 1, 2, 3, 250 }, payload);
            Assert.Equal(9, pkt);
            Assert.True(csOk);
            Assert.Equal(frame.Length, total);
            frame[3] ^= 0x10;
            Assert.True(NextSyncFraming.TryParse(frame, 0, frame.Length, out payload, out pkt, out csOk, out total));
            Assert.False(csOk);
            Assert.False(NextSyncFraming.TryParse(frame, 0, 3, out payload, out pkt, out csOk, out total));
        }

        [Fact]
        public void Crc32_is_the_ieee_value_the_next_computes()
        {
            Assert.Equal(0xCBF43926u, Crc32.Compute(Encoding.ASCII.GetBytes("123456789")));
            Assert.Equal("CBF43926", Crc32.ToHex(0xCBF43926u));
            Assert.Equal(0u, Crc32.Compute(new byte[0]));
        }

        [Fact]
        public void Os_protection_marker_is_recognised()
        {
            Assert.True(OsProtection.IsRefusal(Encoding.ASCII.GetBytes("FOSP")));
            Assert.True(OsProtection.IsRefusal(Encoding.ASCII.GetBytes("FOSPmore")));
            Assert.False(OsProtection.IsRefusal(Encoding.ASCII.GetBytes("F")));
            Assert.False(OsProtection.IsRefusal(Encoding.ASCII.GetBytes("OOSP")));
            Assert.False(OsProtection.IsRefusal(null));
        }

        [Fact]
        public void Crc_floors_match_the_python_table()
        {
            // The yes/no lists of tests/test_remote_listen.py's vcrc-gate check.
            var yes = new[] { "sync|5.9.2", "sync|5.10.0", "sync|5.9.10", "httpbridge|1.0.8", "n2n|1.0.9", " SYNC |5.9.2",
                              "qlnextremote|0.1.0", "qlnextremote|0.2.0", "zxnt|1.6.0", "zxnt|1.6.1", " ZXNT |2.0.0",
                              "qlnt|0.1.0", "qlnt|1.3.6", "qlnt|1.7.0", " QLNT |1.7.0" };
            var no = new[] { "sync|5.9.1", "n2n|1.0.7", "|", "sync|", "sync|x.y", "other|9.9.9",
                             "qlnextremote|0.0.1", "zxnt|1.5.9", "zxnt|", "qlnt|0.0.9", "qlnt|" };
            foreach (var p in yes)
            {
                var parts = p.Split('|');
                Assert.True(CrcFloors.PeerAnswersCrc(parts[0], parts[1]), p);
            }
            foreach (var p in no)
            {
                var parts = p.Split('|');
                Assert.False(CrcFloors.PeerAnswersCrc(parts[0], parts[1]), p);
            }
            Assert.False(CrcFloors.PeerAnswersCrc(null, null));
            Assert.True(new NextIdent("sync", "5.9.14").AnswersCrc);
        }

        [Fact]
        public void Remote_paths_keep_a_fetched_folder_under_its_own_name()
        {
            Assert.Equal("lev/boot.tap", RemotePaths.RelativeNameUnder("/games/lev", "/games/lev/boot.tap"));
            Assert.Equal("boot.tap", RemotePaths.RelativeNameUnder("/games/boot.tap", "/games/boot.tap"));
            Assert.Equal("lev/sub/b.bin", RemotePaths.RelativeNameUnder("M:/games/lev", "/games/lev/sub/b.bin"));
            Assert.Equal("other/x", RemotePaths.RelativeNameUnder("/games/lev", "other/x"));
            string root = Path.Combine(Path.GetTempPath(), "nsl-" + Guid.NewGuid().ToString("N"));
            string safe = RemotePaths.SanitizeIncomingPath(root, "c:/../../etc/passwd");
            Assert.StartsWith(Path.GetFullPath(root), safe);
            Assert.EndsWith("passwd", safe);
            Assert.Equal(Path.Combine(Path.GetFullPath(root), "received.bin"), RemotePaths.SanitizeIncomingPath(root, "/../.."));
            Assert.Equal("a.tap", RemotePaths.BaseName("/games/a.tap"));
        }

        [Fact]
        public void Verify_wait_is_a_minute_plus_size_over_15kbps_capped_at_an_hour()
        {
            Assert.Equal(60000L, ListenerSession.VerifyWaitMs(0));
            Assert.Equal(160000L, ListenerSession.VerifyWaitMs(1500000));
            Assert.Equal(3600000L, ListenerSession.VerifyWaitMs(1000000000000L));
        }
    }

    public class ListenerTests
    {
        private static readonly byte[] PutBytes = FakeNext.Repeat(Range256(), 8);     // 2048 bytes: 4 x 512 frames

        private static byte[] Range256()
        {
            var b = new byte[256];
            for (int i = 0; i < 256; i++) b[i] = (byte)i;
            return b;
        }

        [Fact]
        public void Handshake_lists_a_folder_and_names_a_missing_one()
        {
            using (var h = new Harness())
            {
                Assert.True(h.Listener.IsConnected);
                Assert.Equal(IPAddress.Loopback, h.Listener.RemoteEndPoint.Address);

                var ls = Wait.For(h.Listener.ListAsync("/"));
                Assert.True(ls.Ok, ls.Error);
                Assert.Equal("/", ls.Path);
                Assert.Equal(2, ls.Entries.Count);
                Assert.True(ls.Entries[0].IsDirectory);
                Assert.Equal("GAMES", ls.Entries[0].Name);
                Assert.Equal("boot.bas", ls.Entries[1].Name);
                Assert.Equal(1234, ls.Entries[1].Size);

                // "ls /gone" answers a single 'F' block: a missing folder is reported,
                // never a phantom empty listing, and the stream stays in sync after it.
                var gone = Wait.For(h.Listener.ListAsync("/gone"));
                Assert.Equal(NextSyncStatus.Failed, gone.Status);
                Assert.Contains("missing folder", gone.Error);

                // A protected listing is the MARKED refusal, never "folder gone".
                var sys = Wait.For(h.Listener.ListAsync("/sys"));
                Assert.Equal(NextSyncStatus.OsProtected, sys.Status);
                Assert.Contains("os-protected", sys.Error);
                Assert.Equal(new[] { "/sys" }, h.Next.LsOsp);

                var again = Wait.For(h.Listener.ListAsync("/"));
                Assert.True(again.Ok);
            }
        }

        [Fact]
        public void Listing_sorts_directories_first_then_names_case_insensitively()
        {
            using (var h = new Harness(n => n.Entries = new List<FakeNext.Entry>
            {
                new FakeNext.Entry(false, 3, "zeta.tap"),
                new FakeNext.Entry(true, 0, "beta"),
                new FakeNext.Entry(false, 1, "Alpha.bas"),
                new FakeNext.Entry(true, 0, "Alpha"),
            }))
            {
                var ls = Wait.For(h.Listener.ListAsync("/"));
                Assert.True(ls.Ok);
                Assert.Equal(new[] { "Alpha", "beta", "Alpha.bas", "zeta.tap" }, new List<RemoteEntry>(ls.Entries).ConvertAll(e => e.Name));
            }
        }

        [Fact]
        public void Get_writes_a_file_and_recreates_a_folder_tree()
        {
            string tmp = Path.Combine(Path.GetTempPath(), "nsl-get-" + Guid.NewGuid().ToString("N"));
            try
            {
                using (var h = new Harness())
                {
                    var one = Wait.For(h.Listener.GetAsync("boot.bas", tmp));
                    Assert.True(one.Ok, one.Error);
                    Assert.Single(one.Files);
                    Assert.Equal(h.Next.FileBytes, File.ReadAllBytes(one.Files[0].LocalPath));
                    Assert.Equal(h.Next.FileBytes.Length, one.BytesReceived);
                    Assert.Equal(Path.Combine(tmp, "boot.bas"), one.Files[0].LocalPath);

                    // Folder get: files recreated under the fetched folder name only
                    // (dest/lev/...), sub-structure kept, no stray "games" parent.
                    string foldl = Path.Combine(tmp, "foldl");
                    var tree = Wait.For(h.Listener.GetAsync("/games/lev", foldl));
                    Assert.True(tree.Ok, tree.Error);
                    Assert.Equal(2, tree.Files.Count);
                    Assert.Equal("AAAA", File.ReadAllText(Path.Combine(foldl, "lev", "a.bin")));
                    Assert.Equal("BBBBBB", File.ReadAllText(Path.Combine(foldl, "lev", "sub", "b.bin")));
                    Assert.False(Directory.Exists(Path.Combine(foldl, "games")));

                    // In-memory get.
                    var mem = Wait.For(h.Listener.GetBytesAsync("/games/boot.bas"));
                    Assert.True(mem.Ok);
                    Assert.Equal(h.Next.FileBytes, mem.Data);
                    Assert.Null(mem.Files[0].LocalPath);

                    // A read-protected source: the marked 'F'+OSP leads the empty
                    // walk - OsProtected, never a phantom "0 files" success.
                    var osp = Wait.For(h.Listener.GetAsync("/sys/secret.bin", tmp));
                    Assert.Equal(NextSyncStatus.OsProtected, osp.Status);
                    Assert.Empty(osp.Files);
                    Assert.Equal(new[] { "/sys/secret.bin" }, h.Next.GetOsp);

                    lock (h.Progress)
                        Assert.Contains(h.Progress, p => p.Kind == TransferKind.Get && p.RemotePath == "boot.bas" && p.BytesDone == h.Next.FileBytes.Length);
                }
            }
            finally
            {
                try { Directory.Delete(tmp, true); } catch (Exception) { }
            }
        }

        [Fact]
        public void Put_round_trips_byte_identical_through_a_forced_retry_and_restart()
        {
            using (var h = new Harness(n => { n.ForceRetryAtPacket = 1; n.ForceRestartAtPacket = 2; }))
            {
                var put = Wait.For(h.Listener.PutAsync(PutBytes, "/ho/up.bin"));
                Assert.True(put.Ok, put.Error);
                Assert.Equal("/ho/up.bin", put.RemotePath);
                Assert.Equal(PutBytes.Length, put.BytesSent);
                Assert.Equal(PutBytes.Length, put.BytesTotal);
                Assert.Equal(1, put.Retries);
                Assert.Equal(1, put.Restarts);
                Assert.False(put.Verified);
                // The Task completed on the last data frame; the fake records the
                // file after the terminating empty Get that follows.
                Assert.True(h.Next.WaitForFile("/ho/up.bin"), "the fake never saw the end of the put");
                Assert.Equal("/ho/up.bin", h.Next.LastPutPath);
                Assert.Equal(PutBytes, h.Next.LastPutBytes);
                Assert.True(h.LogContains("retry"), "the retry was traced");
                Assert.True(h.LogContains("restarted the transfer"), "the restart was traced");
                // verify_crc off: no 'K' follows the put.
                Assert.False(h.Next.SawOp("K", "/ho/up.bin"));
                lock (h.Progress)
                    Assert.Contains(h.Progress, p => p.Kind == TransferKind.Put && p.BytesDone == PutBytes.Length && p.BytesTotal == PutBytes.Length);
            }
        }

        [Fact]
        public void Put_from_a_file_appends_the_local_name_to_a_folder_path()
        {
            string tmp = Path.Combine(Path.GetTempPath(), "nsl-put-" + Guid.NewGuid().ToString("N") + ".bin");
            File.WriteAllBytes(tmp, PutBytes);
            try
            {
                using (var h = new Harness())
                {
                    var put = Wait.For(h.Listener.PutAsync(tmp, "/ho/"));
                    Assert.True(put.Ok, put.Error);
                    Assert.Equal("/ho/" + Path.GetFileName(tmp), put.RemotePath);
                    Assert.True(h.Next.WaitForFile(put.RemotePath));
                    Assert.Equal(PutBytes, h.Next.Files[put.RemotePath]);

                    // An empty file is one empty frame: the Next's first Get ends it.
                    File.WriteAllBytes(tmp, new byte[0]);
                    var empty = Wait.For(h.Listener.PutAsync(tmp, "/ho/empty.bin"));
                    Assert.True(empty.Ok, empty.Error);
                    Assert.True(h.Next.WaitForFile("/ho/empty.bin"));
                    Assert.Empty(h.Next.Files["/ho/empty.bin"]);

                    // A missing local file is a LocalError and the poll is still answered.
                    var missing = Wait.For(h.Listener.PutAsync(tmp + ".nope", "/ho/x.bin"));
                    Assert.Equal(NextSyncStatus.LocalError, missing.Status);
                    var ls = Wait.For(h.Listener.ListAsync("/"));
                    Assert.True(ls.Ok);
                }
            }
            finally
            {
                try { File.Delete(tmp); } catch (Exception) { }
            }
        }

        [Fact]
        public void Put_refusals_are_told_apart()
        {
            using (var h = new Harness())
            {
                // A put the Next rejects with a plain 'F' status block (acked, or
                // the fake's Push would have failed).
                var locked = Wait.For(h.Listener.PutAsync(PutBytes, "/locked/up.bin"));
                Assert.Equal(NextSyncStatus.Failed, locked.Status);
                Assert.Equal("/locked/up.bin", h.Next.PutFail);

                // The MARKED 'F'+OSP refusal of a protecting ZX Next Remote.
                var sys = Wait.For(h.Listener.PutAsync(PutBytes, "/sys/up.bin"));
                Assert.Equal(NextSyncStatus.OsProtected, sys.Status);
                Assert.Contains("OS protection", sys.Error);
                Assert.Equal(new[] { "/sys/up.bin" }, h.Next.PutOsp);

                // An old dot that could not open the file sends nothing and just
                // polls on: the put is reported abandoned, the poll answered.
                var abandoned = Wait.For(h.Listener.PutAsync(PutBytes, "/abandon/up.bin"));
                Assert.Equal(NextSyncStatus.Failed, abandoned.Status);
                Assert.Contains("abandoned", abandoned.Error);

                var ls = Wait.For(h.Listener.ListAsync("/"));
                Assert.True(ls.Ok, "the stream survived every refusal");
            }
        }

        [Fact]
        public void Status_verbs_report_ok_failed_and_os_protected()
        {
            using (var h = new Harness())
            {
                var mk = Wait.For(h.Listener.MakeDirectoryAsync("/ho"));
                Assert.True(mk.Ok);
                Assert.Equal("mkdir", mk.Operation);
                Assert.True(h.Next.SawOp("M", "/ho"));

                var rm = Wait.For(h.Listener.DeleteFileAsync("/x.tap"));
                Assert.True(rm.Ok);
                Assert.True(h.Next.SawOp("X", "/x.tap"));

                var rd = Wait.For(h.Listener.RemoveDirectoryAsync("/y"));
                Assert.True(rd.Ok);
                Assert.True(h.Next.SawOp("R", "/y"));

                var ren = Wait.For(h.Listener.RenameAsync("/ho/a.txt", "/ho/b.txt"));
                Assert.True(ren.Ok);
                Assert.Equal("/ho/a.txt\0/ho/b.txt", h.Next.Ren);   // old + NUL + new in one frame

                var lockedRm = Wait.For(h.Listener.DeleteFileAsync("/corrupt/locked.bin"));
                Assert.Equal(NextSyncStatus.Failed, lockedRm.Status);
                Assert.Contains("FAILED on the Next", lockedRm.Error);

                // OS protection: every status verb reports the marked refusal BY
                // NAME, not as a generic failure.
                Assert.Equal(NextSyncStatus.OsProtected, Wait.For(h.Listener.MakeDirectoryAsync("/sys/evil")).Status);
                Assert.Equal(NextSyncStatus.OsProtected, Wait.For(h.Listener.DeleteFileAsync("/sys/config/boot")).Status);
                Assert.Equal(NextSyncStatus.OsProtected, Wait.For(h.Listener.RemoveDirectoryAsync("/sys/dir")).Status);
                Assert.Equal(NextSyncStatus.OsProtected, Wait.For(h.Listener.RenameAsync("/games/x", "/sys/x")).Status);

                var ls = Wait.For(h.Listener.ListAsync("/"));
                Assert.True(ls.Ok, "the stream survived the refusals");
            }
        }

        [Fact]
        public void A_seat_that_repolls_mid_reply_is_late_not_gone()
        {
            using (var h = new Harness())
            {
                // One raw Poll before the framed 'O': swallowed, the reply lands.
                var late = Wait.For(h.Listener.MakeDirectoryAsync("/repoll"));
                Assert.True(late.Ok, late.Error);
                Assert.True(h.LogContains("re-polled while a reply was owed"));

                // Two raw Polls: the command was lost - the old verdict at once,
                // and the seat gets the catch-all's 'I' for the tail (the fake
                // asserts that itself).
                var lost = Wait.For(h.Listener.MakeDirectoryAsync("/repoll2"));
                Assert.Equal(NextSyncStatus.ConnectionDropped, lost.Status);

                var ls = Wait.For(h.Listener.ListAsync("/"));
                Assert.True(ls.Ok, "the seat polls on");
            }
        }

        [Fact]
        public void Ident_is_answered_by_a_new_listener_and_silent_on_an_old_one()
        {
            using (var h = new Harness())
            {
                var who = Wait.For(h.Listener.QueryIdentAsync());
                Assert.True(who.Ok, who.Error);
                Assert.Equal("sync", who.Ident.Type);
                Assert.Equal("9.9.9", who.Ident.Version);
                Assert.True(who.Ident.AnswersCrc);
                Assert.Equal("sync", h.Listener.Ident.Type);
            }
            using (var h = new Harness(n => n.Ident = null))
            {
                // A pre-5.8 listener ignores 'Y' and re-polls: its raw Poll fails
                // the block parse, NotSupported, and the stray bytes are re-synced
                // by the loop's catch-all idle reply.
                var who = Wait.For(h.Listener.QueryIdentAsync());
                Assert.Equal(NextSyncStatus.NotSupported, who.Status);
                Assert.Null(who.Ident);
                Assert.Null(h.Listener.Ident);
                var ls = Wait.For(h.Listener.ListAsync("/"));
                Assert.True(ls.Ok, "the session survived the unknown opcode");
            }
        }

        [Fact]
        public void Crc_match_mismatch_missing_and_silent()
        {
            using (var h = new Harness())
            {
                // The fake answers the CRC of the PATH for a file it never saw.
                var want = Crc32.Compute(Encoding.ASCII.GetBytes("/games/a.tap"));
                var crc = Wait.For(h.Listener.GetCrc32Async("/games/a.tap"));
                Assert.True(crc.Ok, crc.Error);
                Assert.Equal(Crc32.ToHex(want), crc.Crc32Hex);
                Assert.Equal(want, crc.Crc32);

                // A put, then its CRC: the Next's digest of what it stored equals
                // the local digest of what was sent - the caller's own verify.
                Assert.True(Wait.For(h.Listener.PutAsync(PutBytes, "/ho/v.bin")).Ok);
                var after = Wait.For(h.Listener.GetCrc32Async("/ho/v.bin"));
                Assert.True(after.Ok);
                Assert.Equal(Crc32.Compute(PutBytes), after.Crc32);

                // ...and a corrupted copy differs.
                Assert.True(Wait.For(h.Listener.PutAsync(PutBytes, "/corrupt/c.bin")).Ok);
                var bad = Wait.For(h.Listener.GetCrc32Async("/corrupt/c.bin"));
                Assert.True(bad.Ok);
                Assert.NotEqual(Crc32.Compute(PutBytes), bad.Crc32);

                Assert.Equal(NextSyncStatus.Failed, Wait.For(h.Listener.GetCrc32Async("/gone")).Status);
                Assert.Equal(NextSyncStatus.OsProtected, Wait.For(h.Listener.GetCrc32Async("/ospread/x")).Status);
                var malformed = Wait.For(h.Listener.GetCrc32Async("/malformed/x"));
                Assert.Equal(NextSyncStatus.Failed, malformed.Status);
                Assert.Contains("malformed", malformed.Error);
            }
            using (var h = new Harness(n => n.CrcSilent = true))
            {
                var crc = Wait.For(h.Listener.GetCrc32Async("/games/a.tap"));
                Assert.Equal(NextSyncStatus.NotSupported, crc.Status);
                Assert.True(Wait.For(h.Listener.ListAsync("/")).Ok);
            }
        }

        [Fact]
        public void Verify_after_put_checks_the_next_crc_and_deletes_a_corrupted_copy()
        {
            using (var h = new Harness(null, l => l.VerifyPuts = true))
            {
                // Nobody asked 'Y' yet: the verify self-probes once, then asks 'K'.
                var good = Wait.For(h.Listener.PutAsync(PutBytes, "/ho/good.bin"));
                Assert.True(good.Ok, good.Error);
                Assert.True(good.Verified);
                Assert.Equal(1, h.Next.CountOp("Y"));
                Assert.True(h.Next.SawOp("K", "/ho/good.bin"));
                Assert.Equal("sync", h.Listener.Ident.Type);

                // A bad SD write: the digest differs, the copy is deleted, the put
                // fails with the verdict.
                var bad = Wait.For(h.Listener.PutAsync(PutBytes, "/corrupt/bad.bin"));
                Assert.Equal(NextSyncStatus.Failed, bad.Status);
                Assert.True(bad.CrcMismatch);
                Assert.Contains("has been deleted", bad.Error);
                Assert.True(h.Next.SawOp("X", "/corrupt/bad.bin"));
                Assert.Equal(1, h.Next.CountOp("Y"));      // the ident is cached per session

                // A copy the Next refuses to delete: the verdict says so.
                var kept = Wait.For(h.Listener.PutAsync(PutBytes, "/corrupt/locked.bin"));
                Assert.Equal(NextSyncStatus.Failed, kept.Status);
                Assert.Contains("could NOT be deleted", kept.Error);
                Assert.Contains("the Next refused the delete", kept.Error);

                var ls = Wait.For(h.Listener.ListAsync("/"));
                Assert.True(ls.Ok);
            }
            using (var h = new Harness(n => n.Ident = null, l => l.VerifyPuts = true))
            {
                // An old listener: no 'K' is ever sent, the put is done but unverified.
                var put = Wait.For(h.Listener.PutAsync(PutBytes, "/ho/old.bin"));
                Assert.True(put.Ok, put.Error);
                Assert.False(put.Verified);
                Assert.Contains("predates", put.VerificationNote);
                Assert.Equal(0, h.Next.CountOp("K"));
                Assert.True(h.LogContains("verification skipped"));
            }
        }

        [Fact]
        public void Drives_free_space_copy_and_size()
        {
            using (var h = new Harness())
            {
                var drives = Wait.For(h.Listener.GetDrivesAsync());
                Assert.True(drives.Ok);
                Assert.Equal("C", drives.Current);
                Assert.Equal(new[] { "C", "M" }, drives.Mounted);

                var free = Wait.For(h.Listener.GetFreeSpaceAsync("m:"));
                Assert.True(free.Ok);
                Assert.Equal("M", free.Drive);
                Assert.Equal(2048L * 512, free.FreeBytes);
                Assert.True(h.Next.SawOp("Z", "M"));
                Assert.Equal(NextSyncStatus.Failed, Wait.For(h.Listener.GetFreeSpaceAsync("E")).Status);
                var cur = Wait.For(h.Listener.GetFreeSpaceAsync());
                Assert.True(cur.Ok);
                Assert.Equal("", cur.Drive);

                var copy = Wait.For(h.Listener.CopyOnNextAsync("/games/lev", "M:/bk/lev"));
                Assert.True(copy.Ok, copy.Error);
                Assert.Equal(1, copy.FilesCopied);
                Assert.Equal("/games/lev\0M:/bk/lev", h.Next.Rcpy[0]);
                Assert.Equal(NextSyncStatus.Failed, Wait.For(h.Listener.CopyOnNextAsync("/locked/t", "/t2")).Status);
                Assert.Equal(NextSyncStatus.OsProtected, Wait.For(h.Listener.CopyOnNextAsync("/games/a.tap", "/sys/a.tap")).Status);

                var size = Wait.For(h.Listener.GetSizeAsync("/games/lev"));
                Assert.True(size.Ok, size.Error);
                Assert.Equal(7, size.Files);
                Assert.Equal(3, size.Directories);
                Assert.Equal((1L << 32) + 512, size.Bytes);       // the 48-bit reassembly
                Assert.Equal(NextSyncStatus.Failed, Wait.For(h.Listener.GetSizeAsync("/gone")).Status);

                lock (h.Progress)
                {
                    Assert.Contains(h.Progress, p => p.Kind == TransferKind.Copy && p.Item == "M:/bk/lev");
                    Assert.Contains(h.Progress, p => p.Kind == TransferKind.Copy && p.Item == "");
                    Assert.Contains(h.Progress, p => p.Kind == TransferKind.Size && p.Item == "/games/lev");
                }
            }
        }

        [Fact]
        public void Delete_tree_walks_bottom_up_and_reports_once()
        {
            var fs = new Dictionary<string, object>
            {
                { "del", new Dictionary<string, object>
                    {
                        { "a.txt", Encoding.ASCII.GetBytes("AA") },
                        { "sub", new Dictionary<string, object>
                            {
                                { "b.txt", Encoding.ASCII.GetBytes("BB") },
                                { "empty", new Dictionary<string, object>() }
                            } }
                    } },
                { "del2", new Dictionary<string, object> { { "locked.txt", Encoding.ASCII.GetBytes("LL") } } },
                { "del3", new Dictionary<string, object>
                    {
                        { "m1.txt", Encoding.ASCII.GetBytes("M1") },
                        { "msub", new Dictionary<string, object> { { "m2.txt", Encoding.ASCII.GetBytes("M2") } } }
                    } }
            };
            using (var h = new Harness(n => n.Fs = fs))
            {
                var ok = Wait.For(h.Listener.DeleteTreeAsync("/del"));
                Assert.True(ok.Ok, ok.Error);
                Assert.False(fs.ContainsKey("del"));
                Assert.Equal(2, ok.FilesDeleted);
                Assert.Equal(3, ok.FoldersDeleted);
                Assert.Equal(0, ok.Failures);

                // An undeletable file: the tree survives, the job reports Failed once.
                var bad = Wait.For(h.Listener.DeleteTreeAsync("/del2"));
                Assert.Equal(NextSyncStatus.Failed, bad.Status);
                Assert.Equal(2, bad.Failures);        // the file, then the non-empty folder
                Assert.True(((Dictionary<string, object>)fs["del2"]).ContainsKey("locked.txt"));

                // A drive-prefixed root walks and deletes exactly like a bare one.
                var drv = Wait.For(h.Listener.DeleteTreeAsync("M:/del3"));
                Assert.True(drv.Ok, drv.Error);
                Assert.False(fs.ContainsKey("del3"));

                // A protected member ends the whole delete now.
                var osp = Wait.For(h.Listener.DeleteTreeAsync("/sys/x"));
                Assert.Equal(NextSyncStatus.OsProtected, osp.Status);

                var ls = Wait.For(h.Listener.ListAsync("/"));
                Assert.True(ls.Ok);
            }
        }

        [Fact]
        public void Release_then_quit_ends_the_session_politely()
        {
            using (var h = new Harness())
            {
                var rel = Wait.For(h.Listener.ReleaseAsync());
                Assert.True(rel.Ok, rel.Error);
                Assert.True(h.Next.Released);

                var quit = Wait.For(h.Listener.QuitAsync(exitApplication: true));
                Assert.True(quit.Ok);
                Assert.True(quit.ExitApplication);
                Assert.True(h.DisconnectedEvent.WaitOne(5000), "Disconnected was raised");
                Assert.True(h.Next.Done.WaitOne(5000));
                Assert.Equal("X", h.Next.Quit);                                  // the marked quit
                Assert.Equal("Later", Encoding.ASCII.GetString(h.Next.ByeAnswer)); // the post-quit Bye answered
                Assert.False(h.Listener.IsConnected);
                Assert.True(h.Listener.IsListening, "the listener keeps accepting");
                lock (h.Disconnects) Assert.Contains("quit", h.Disconnects[0].Reason);

                // A second Next can dial in afterwards; a plain quit sends the bare 'Q'.
                var second = new FakeNext();
                h.Seat(second);
                Assert.True(Wait.For(h.Listener.ListAsync("/")).Ok);
                Assert.True(Wait.For(h.Listener.QuitAsync()).Ok);
                Assert.True(second.Done.WaitOne(5000));
                Assert.Equal("", second.Quit);
                second.Dispose();
                Assert.Null(second.Error);
            }
        }

        [Fact]
        public void A_next_that_hangs_up_fails_its_command_and_a_new_one_can_dial_in()
        {
            using (var h = new Harness())
            {
                // Queued behind the dying command while THIS Next was seated: never
                // run on another machine.
                var dying = h.Listener.GetCrc32Async("/die/x");
                var behind = h.Listener.ListAsync("/");
                var died = Wait.For(dying);
                Assert.Equal(NextSyncStatus.ConnectionDropped, died.Status);
                Assert.True(h.DisconnectedEvent.WaitOne(5000), "Disconnected was raised");
                var orphan = Wait.For(behind);
                Assert.Equal(NextSyncStatus.Cancelled, orphan.Status);
                Assert.Contains("left before it ran", orphan.Error);
                Assert.False(h.Listener.IsConnected);
                lock (h.Disconnects) Assert.Contains("closed the connection", h.Disconnects[0].Reason);

                // Queued while NO Next is connected: waits for the next one.
                var waiting = h.Listener.ListAsync("/");
                Assert.False(waiting.Wait(300));
                var second = new FakeNext();
                h.Seat(second);
                Assert.True(Wait.For(waiting).Ok);
                second.Dispose();
                Assert.Null(second.Error);
            }
        }

        [Fact]
        public void A_second_dialer_is_told_busy()
        {
            using (var h = new Harness())
            {
                using (var s = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp))
                {
                    s.Connect(new IPEndPoint(IPAddress.Loopback, h.Listener.LocalPort));
                    s.Send(Encoding.ASCII.GetBytes("Listen"));
                    s.ReceiveTimeout = 5000;
                    var buf = new byte[64];
                    int n = s.Receive(buf);
                    byte[] payload; byte pkt; bool csOk; int total;
                    Assert.True(NextSyncFraming.TryParse(buf, 0, n, out payload, out pkt, out csOk, out total));
                    Assert.Equal("Busy", Encoding.ASCII.GetString(payload));
                }
                Assert.True(h.WaitForLog("turned away"));
                Assert.True(Wait.For(h.Listener.ListAsync("/")).Ok, "the seated Next was not disturbed");
            }
        }

        [Fact]
        public void A_client_that_is_not_a_listener_is_refused_and_the_server_lives_on()
        {
            using (var h = new Harness())
            {
                using (var s = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp))
                {
                    s.Connect(new IPEndPoint(IPAddress.Loopback, h.Listener.LocalPort));
                    s.Send(Encoding.ASCII.GetBytes("Sync4!"));
                    s.ReceiveTimeout = 5000;
                    var buf = new byte[64];
                    try { Assert.Equal(0, s.Receive(buf)); } catch (SocketException) { }
                }
                // A connect-retry probe that closes without a word is not an error.
                using (var s = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp))
                {
                    s.Connect(new IPEndPoint(IPAddress.Loopback, h.Listener.LocalPort));
                }
                Assert.True(h.WaitForLog("did not request -listen mode"));
                Assert.True(h.Listener.IsListening);
                Assert.True(Wait.For(h.Listener.ListAsync("/")).Ok);
            }
        }

        [Fact]
        public void Stop_sends_the_next_home_and_cancels_what_is_queued()
        {
            using (var h = new Harness())
            {
                Assert.True(Wait.For(h.Listener.ListAsync("/")).Ok);
                h.Listener.Stop();
                Assert.True(h.Next.Done.WaitOne(5000));
                Assert.Equal("", h.Next.Quit);
                Assert.False(h.Listener.IsListening);
                Assert.False(h.Listener.IsConnected);

                // Restartable; a command queued with nobody connected is cancelled
                // by Stop.
                h.Listener.Start();
                var queued = h.Listener.ListAsync("/");
                h.Listener.Stop();
                Assert.Equal(NextSyncStatus.Cancelled, Wait.For(queued).Status);
            }
        }

        [Fact]
        public void A_cancellation_token_takes_a_queued_command_back()
        {
            using (var listener = new NextSyncListener(IPAddress.Loopback, 0))
            {
                listener.Start();
                using (var cts = new CancellationTokenSource())
                {
                    var t = listener.ListAsync("/", cts.Token);
                    Assert.False(t.Wait(100));
                    cts.Cancel();
                    Assert.Equal(NextSyncStatus.Cancelled, Wait.For(t).Status);
                }
                var already = listener.ListAsync("/", new CancellationToken(true));
                Assert.Equal(NextSyncStatus.Cancelled, Wait.For(already).Status);
            }
        }

        [Fact]
        public void Silence_is_the_dead_peer_signal()
        {
            // A Next that completes the handshake and then never polls: no FIN,
            // the seat simply goes quiet - and is reaped at PeerSilenceLimit.
            var mute = new MuteNext();
            var listener = new NextSyncListener(IPAddress.Loopback, 0) { PeerSilenceLimit = TimeSpan.FromMilliseconds(1500) };
            var gone = new ManualResetEvent(false);
            string reason = null;
            listener.Disconnected += (s, e) => { reason = e.Reason; gone.Set(); };
            listener.Start();
            try
            {
                mute.Connect(listener.LocalPort);
                Assert.True(gone.WaitOne(10000), "the silent seat was never reaped");
                Assert.Contains("assuming it is gone", reason);
                Assert.True(listener.IsListening, "the listener keeps accepting");
            }
            finally
            {
                mute.Dispose();
                listener.Dispose();
            }
        }

        /// <summary>A Next that completes the handshake and then never says another word.</summary>
        private sealed class MuteNext : IDisposable
        {
            private Socket sock;

            public void Connect(int port)
            {
                sock = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
                sock.Connect(new IPEndPoint(IPAddress.Loopback, port));
                sock.Send(Encoding.ASCII.GetBytes("Listen"));
                sock.ReceiveTimeout = 5000;
                var buf = new byte[64];
                int n = sock.Receive(buf);
                byte[] payload; byte pkt; bool csOk; int total;
                Assert.True(NextSyncFraming.TryParse(buf, 0, n, out payload, out pkt, out csOk, out total));
                Assert.Equal("Listening", Encoding.ASCII.GetString(payload));
            }

            public void Dispose()
            {
                try { sock.Close(); } catch (Exception) { }
            }
        }
    }
}
