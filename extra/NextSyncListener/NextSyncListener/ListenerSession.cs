using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

namespace NextSync.Listener
{
    /// <summary>
    /// One seated Next's <c>-listen</c> session: the core of ZX-Next-Unite's
    /// <c>_re_session</c> (zxnu_workers.py) - the poll loop, the put pull
    /// (Get/Gee/Retry/Restart), the reply reader with its late-Poll rule, the
    /// command arms, the verify-after-put chain and the rmtree walk - without
    /// the app's multi-seat roster, Sessions Off eviction, link-loss retries
    /// and remote self-update macro.
    /// <para>
    /// THE NEXT DRIVES: it polls, every poll is answered with exactly one frame
    /// (a command or the idle <c>'I'</c>), and a command's reply is read right
    /// there, inside the arm, before the next poll is awaited. Every arm below
    /// keeps that one-frame-per-poll rule.
    /// </para>
    /// </summary>
    internal sealed class ListenerSession
    {
        private const int IdleWaitMs = 1000;
        private static readonly byte[] Empty = new byte[0];

        private readonly NextSyncListener owner;
        private readonly Socket conn;
        private readonly ManualResetEvent ended = new ManualResetEvent(false);
        private readonly LinkedList<Step> local = new LinkedList<Step>();
        private Thread thread;
        private volatile bool quitRequested;
        private volatile bool closeRequested;

        // The put the Next is pulling, and its serving state. put_data is kept
        // after the last byte for a late Retry/Restart and the terminating Get.
        private byte[] putData = Empty;
        private int putOfs;
        private int putPkt;
        private byte[] lastPacket = Empty;
        private int putRetry;
        private int putRestart;
        private Request<PutResult> pendingPut;

        // The command whose reply is being read (its failure report on a death).
        private Request inflight;

        // sess_ident: never asked (identAsked false), asked and answered, or
        // asked and met with silence (identAsked true, ident null).
        private bool identAsked;
        private NextIdent ident;
        private bool verifySkipSaid;

        private bool endRequested;
        private string endReason;

        public ListenerSession(NextSyncListener owner, Socket conn, IPEndPoint remote, int id)
        {
            this.owner = owner;
            this.conn = conn;
            Remote = remote;
            Id = id;
        }

        public IPEndPoint Remote { get; private set; }

        public int Id { get; private set; }

        public NextIdent Ident
        {
            get { return ident; }
        }

        public bool IsCurrentThread
        {
            get { return thread != null && thread == Thread.CurrentThread; }
        }

        public void Start()
        {
            thread = new Thread(Run) { IsBackground = true, Name = "NextSync listener session #" + Id };
            thread.Start();
        }

        /// <summary>Stop(): send the Next a <c>'Q'</c> at its next poll, ahead of everything.</summary>
        public void RequestQuit()
        {
            quitRequested = true;
        }

        public bool WaitEnded(TimeSpan timeout)
        {
            return ended.WaitOne(timeout);
        }

        /// <summary>Drop the link from another thread: wakes a blocked recv on every platform.</summary>
        public void Close()
        {
            closeRequested = true;
            try { conn.Shutdown(SocketShutdown.Both); } catch (Exception) { }
            try { conn.Close(); } catch (Exception) { }
        }

        private string Address
        {
            get { return Remote != null ? Remote.Address.ToString() : "?"; }
        }

        private void Log(string message)
        {
            owner.RaiseLog(ListenerLogLevel.Info, message);
        }

        private void Warn(string message)
        {
            owner.RaiseLog(ListenerLogLevel.Warning, message);
        }

        // ------------------------------------------------------------------
        // the loop
        // ------------------------------------------------------------------

        private void Run()
        {
            string reason = "the -listen session ended";
            try
            {
                long lastRx = Now();
                long turnDone = lastRx;
                var buf = new byte[1024];
                // Stop() is NOT a loop exit: it asks for a 'Q' at the next poll
                // (quitRequested, served below) and only closes the socket when
                // the grace period passes - so a stopping listener sends the Next
                // home instead of hanging up on it.
                while (!closeRequested && !endRequested)
                {
                    if (!WaitReadable(IdleWaitMs))
                    {
                        // Dead-peer detection: the Next drives and polls every
                        // second or two when idle, so silence is itself the
                        // signal. Counted between commands only - a reply has
                        // its own timeout - from the later of the last byte and
                        // the end of the last served turn (a five-minute get is
                        // not silence).
                        long quiet = Now() - Math.Max(lastRx, turnDone);
                        long limit = (long)owner.PeerSilenceLimit.TotalMilliseconds;
                        if (quiet >= limit)
                        {
                            reason = "no word from the Next for " + (quiet / 1000) + "s - assuming it is gone (powered off? Wi-Fi dropped?)";
                            Warn("Remote explorer: " + reason);
                            break;
                        }
                        continue;
                    }
                    int n = conn.Receive(buf, 0, buf.Length, SocketFlags.None);
                    if (n == 0)
                    {
                        reason = "the Next closed the connection";
                        Log("Remote explorer: the Next closed the connection (seat #" + Id + ", " + Address + ").");
                        break;
                    }
                    lastRx = Now();
                    var data = new byte[n];
                    Buffer.BlockCopy(buf, 0, data, 0, n);

                    // A put in flight is served by the Next pulling the bytes with
                    // "Get"/"Gee" (or asking to resend with "Retry"/"Restart"). A
                    // newer dotN instead pushes an explicit 'F' status block when
                    // the put fails (couldn't create the file, or the transfer gave
                    // up) - ZX Next Remote marks an OS-protection refusal as
                    // 'F'+OSP. Ack it so the dot's send_block_rt doesn't burn its
                    // retries, and report the failure. Ack even with no pending put
                    // (a rare late 'F' after the last byte already counted).
                    var failPayload = FailBlockPayload(data);
                    if (failPayload != null)
                    {
                        SendFrame("Ok", 0);
                        if (pendingPut != null)
                        {
                            PutFinish(false, OsProtection.IsRefusal(failPayload), "put failed on the Next");
                            ClearPut();
                        }
                        turnDone = Now();
                        continue;
                    }
                    // Older dots don't send 'F'; they just stop pulling and go back
                    // to "Poll". Treat any other non-pull frame during a pending put
                    // as an abandoned upload so the caller's Task completes instead
                    // of waiting forever for a "Get".
                    if (pendingPut != null && !IsPullWord(data))
                    {
                        PutFinish(false, false, "put abandoned by the Next");
                        ClearPut();
                    }

                    if (IsWord(data, "Poll"))
                        ServePoll();
                    else if (IsWord(data, "Get") || IsWord(data, "Gee"))
                        ServeGet();
                    else if (IsWord(data, "Retry"))
                        ServeRetry();
                    else if (IsWord(data, "Restart"))
                        ServeRestart();
                    else if (IsWord(data, "Bye"))
                    {
                        SendFrame("Later", 0);
                        reason = "the Next said Bye";
                        break;
                    }
                    else
                        SendIdle();   // keep the Next polling
                    turnDone = Now();
                }
                if (endRequested && endReason != null)
                    reason = endReason;
                else if (closeRequested || owner.StopRequested)
                    reason = "the listener was stopped";
            }
            catch (ObjectDisposedException)
            {
                reason = closeRequested || owner.StopRequested ? "the listener was stopped" : "the connection was closed";
            }
            catch (SocketException ex)
            {
                if (closeRequested || owner.StopRequested)
                    reason = "the listener was stopped";
                else
                {
                    reason = "connection error from the Next (" + ex.Message + ") - session over";
                    Warn("Remote explorer: " + reason + ".");
                }
            }
            catch (IOException ex)
            {
                reason = "connection error from the Next (" + ex.Message + ") - session over";
                Warn("Remote explorer: " + reason + ".");
            }
            catch (Exception ex)
            {
                reason = "the -listen session failed: " + ex.Message;
                owner.RaiseLog(ListenerLogLevel.Error, "Remote explorer server error: " + ex);
            }
            finally
            {
                SettleOpenWork(reason);
                try { conn.Close(); } catch (Exception) { }
                owner.OnSessionEnded(this, reason);
                ended.Set();
            }
        }

        /// <summary>Clean exits owe answers too: a quit mid-transfer, the Next dropping the link, the listener stopping.</summary>
        private void SettleOpenWork(string reason)
        {
            string why = "the -listen session ended before the command finished (" + reason + ")";
            var flight = inflight;
            inflight = null;
            if (flight != null && !flight.Completed)
                flight.Fail(NextSyncStatus.ConnectionDropped, why);
            var put = pendingPut;
            pendingPut = null;
            if (put != null && !put.Completed)
            {
                var r = (PutResult)put.NewResult();
                r.RemotePath = put.Path;
                r.BytesSent = putOfs;
                r.BytesTotal = putData.Length;
                r.Retries = putRetry;
                r.Restarts = putRestart;
                r.Status = NextSyncStatus.ConnectionDropped;
                r.Error = why;
                put.Complete(r);
            }
            foreach (var step in local)
            {
                Request open = null;
                if (step.Verify != null) open = step.Verify.Put;
                else if (step.Tree != null && !step.Tree.Done) open = step.Tree.Request;
                if (open != null && !open.Completed)
                    open.Fail(NextSyncStatus.ConnectionDropped, why);
            }
            local.Clear();
        }

        // ------------------------------------------------------------------
        // polls and the put pull
        // ------------------------------------------------------------------

        private void ServePoll()
        {
            if (local.Count > 0)
            {
                // A continuation step (a verify, a tree walk) outranks the queue so
                // its sequence stays whole.
                var step = local.First.Value;
                local.RemoveFirst();
                ServeStep(step);
                return;
            }
            if (quitRequested || owner.StopRequested)
            {
                ServeQuit(null, false);
                return;
            }
            var req = owner.TryDequeue(Id);
            if (req == null)
            {
                SendIdle();
                return;
            }
            inflight = req;
            try
            {
                switch (req.Op)
                {
                    case RequestOp.List: ServeList((Request<ListingResult>)req); break;
                    case RequestOp.Get: ServeGetCommand((Request<GetResult>)req); break;
                    case RequestOp.Put: ServePut((Request<PutResult>)req); break;
                    case RequestOp.MakeDirectory: ServeStatus((Request<StatusResult>)req, 'M', "mkdir"); break;
                    case RequestOp.RemoveDirectory: ServeStatus((Request<StatusResult>)req, 'R', "rmdir"); break;
                    case RequestOp.DeleteFile: ServeStatus((Request<StatusResult>)req, 'X', "rm"); break;
                    case RequestOp.Rename: ServeRename((Request<StatusResult>)req); break;
                    case RequestOp.Release: ServeRelease((Request<StatusResult>)req); break;
                    case RequestOp.Drives: ServeDrives((Request<DrivesResult>)req); break;
                    case RequestOp.FreeSpace: ServeFreeSpace((Request<FreeSpaceResult>)req); break;
                    case RequestOp.Ident: ServeIdent((Request<IdentResult>)req); break;
                    case RequestOp.Crc: ServeCrc((Request<CrcResult>)req); break;
                    case RequestOp.Copy: ServeCopy((Request<CopyResult>)req); break;
                    case RequestOp.Size: ServeSize((Request<SizeResult>)req); break;
                    case RequestOp.DeleteTree: StartDeleteTree((Request<DeleteTreeResult>)req); break;
                    case RequestOp.Quit: ServeQuit((Request<QuitResult>)req, req.Flag); break;
                    default:
                        req.Fail(NextSyncStatus.LocalError, "unknown command " + req.Op);
                        SendIdle();
                        break;
                }
            }
            finally
            {
                if (inflight == req) inflight = null;
            }
        }

        private void ServeGet()
        {
            int n = Math.Min(owner.MaxPayload, putData.Length - putOfs);
            if (n < 0) n = 0;
            var packet = new byte[n];
            Buffer.BlockCopy(putData, putOfs, packet, 0, n);
            lastPacket = packet;
            SendFrame(packet, (byte)(putPkt & 0xff));
            putOfs += n;
            putPkt++;
            if (pendingPut != null)
            {
                owner.RaiseProgress(new TransferProgressEventArgs(TransferKind.Put, pendingPut.Path, putOfs, putData.Length, 1, ""));
                if (putOfs >= putData.Length)
                {
                    // Report the round trip only when it was NOT clean, so a
                    // healthy transfer stays silent and a troubled one says so.
                    if (putRetry > 0 || putRestart > 0)
                        Warn("put finished with " + putRetry + " retry/retries and " + putRestart +
                             " restart(s) over " + putPkt + " packet(s) - the link is corrupting data, not losing it");
                    PutFinish(true, false, null);
                }
            }
        }

        private void ServeRetry()
        {
            // Checksum failure on the Next: it wants the SAME packet again.
            putRetry++;
            if (putRetry <= 3 || putRetry % 10 == 0)
                Warn("Next asked to retry packet " + ((putPkt - 1) & 0xff) + " (" + putRetry + " so far)");
            SendFrame(lastPacket, (byte)((putPkt - 1) & 0xff));
        }

        private void ServeRestart()
        {
            // PACKET-NUMBER mismatch on the Next: the two sides' counters have
            // drifted and it is starting the file over. The dot gives up after
            // 6 in a row, so a burst approaching that is the whole diagnosis.
            putRestart++;
            Warn("Next restarted the transfer at packet " + putPkt + " (" + putRestart + " of the 6 it allows before giving up)");
            putOfs = 0;
            putPkt = 0;
            SendFrame("Back", 0);
        }

        private void ClearPut()
        {
            pendingPut = null;
            putData = Empty;
            putOfs = 0;
            putPkt = 0;
        }

        /// <summary>Resolve the pending put: verified, failed, refused by the OS protection - or chained into the verify step.</summary>
        private void PutFinish(bool ok, bool osp, string failure)
        {
            var req = pendingPut;
            pendingPut = null;
            if (req == null) return;
            var res = (PutResult)req.NewResult();
            res.RemotePath = req.Path;
            res.BytesSent = putOfs;
            res.BytesTotal = putData.Length;
            res.Retries = putRetry;
            res.Restarts = putRestart;
            putRetry = 0;
            putRestart = 0;
            if (osp)
            {
                res.Status = NextSyncStatus.OsProtected;
                res.Error = OsProtection.ErrorText;
                Log("*** put " + req.Path + ": BLOCKED by the remote OS protection ***");
            }
            else if (!ok)
            {
                res.Status = NextSyncStatus.Failed;
                res.Error = failure ?? "put failed on the Next";
                Log("*** put " + req.Path + ": FAILED on the Next (" + res.Error + ") ***");
            }
            else if (owner.VerifyPuts)
            {
                // Verify-after-put: hold this put's ONE completion until the 'K'
                // verdict. put_data is STILL the file just served, so the digest
                // is of the bytes actually sent.
                var job = new VerifyJob
                {
                    Put = req,
                    Result = res,
                    Crc = Crc32.ToHex(Crc32.Compute(putData)),
                    Size = putData.Length
                };
                local.AddFirst(new Step { Kind = StepKind.PutVerify, Verify = job });
                return;
            }
            else
            {
                res.Status = NextSyncStatus.Ok;
            }
            req.Complete(res);
        }

        // ------------------------------------------------------------------
        // the command arms
        // ------------------------------------------------------------------

        private void ServeList(Request<ListingResult> req)
        {
            string path = req.Path;
            var res = (ListingResult)req.NewResult();
            res.Path = path;
            var entries = new List<RemoteEntry>();
            bool failed = false, osp = false;
            // The Next answers a listing with 'D' blocks then 'E', or a single
            // 'F' status block if opendir failed (the folder is gone) - or the
            // MARKED 'F'+OSP when a ZX Next Remote's Read+write OS protection
            // refuses it. Track which so a missing folder isn't mistaken for an
            // empty one, and so the 'F' block is consumed instead of desyncing
            // the stream.
            SendFrame("L" + path, 0);
            var outcome = RecvReply(delegate (byte[] payload)
            {
                if (payload.Length == 0) return false;
                byte o = payload[0];
                if (o == (byte)'E') return true;
                if (o == (byte)'F')
                {
                    failed = true;
                    osp = OsProtection.IsRefusal(payload);
                    return true;
                }
                if (o == (byte)'D')
                    ParseListing(payload, entries);
                return false;
            }, ReplyTimeoutMs, false);
            if (outcome == ReplyOutcome.Completed)
            {
                if (osp)
                {
                    res.Status = NextSyncStatus.OsProtected;
                    res.Error = OsProtection.ErrorText;
                }
                else if (failed)
                {
                    res.Status = NextSyncStatus.Failed;
                    res.Error = "ls " + path + " failed (missing folder?)";
                }
                else
                {
                    res.Status = NextSyncStatus.Ok;
                    res.Entries = entries
                        .OrderBy(e => e.IsDirectory ? 0 : 1)
                        .ThenBy(e => e.Name.ToLowerInvariant(), StringComparer.Ordinal)
                        .ToList();
                }
            }
            else
            {
                res.Status = NextSyncStatus.ConnectionDropped;
                res.Error = "ls " + path + ": connection dropped";
            }
            req.Complete(res);
        }

        /// <summary>'D' block of a listing: packed [1B flags][4B size LE][1B namelen][name] entries.</summary>
        private static void ParseListing(byte[] payload, List<RemoteEntry> entries)
        {
            int i = 1;
            while (i + 6 <= payload.Length)
            {
                int flags = payload[i];
                long size = (uint)(payload[i + 1] | (payload[i + 2] << 8) | (payload[i + 3] << 16) | (payload[i + 4] << 24));
                int nl = payload[i + 5];
                int avail = Math.Max(0, Math.Min(nl, payload.Length - (i + 6)));
                string name = Encoding.UTF8.GetString(payload, i + 6, avail);
                i += 6 + nl;
                entries.Add(new RemoteEntry(name, (flags & 1) != 0, size));
            }
        }

        private void ServeGetCommand(Request<GetResult> req)
        {
            // Works for a single file or a whole directory: the Next streams
            // every file back (N/D/E per file, B at the end) with a path relative
            // to the fetched item, which we keep so sub-folders are recreated
            // locally intact.
            string remote = req.Path;
            string destDir = req.LocalPath;
            var res = (GetResult)req.NewResult();
            res.RemotePath = remote;
            var files = new List<ReceivedFile>();
            if (destDir != null)
            {
                try { Directory.CreateDirectory(destDir); }
                catch (Exception ex)
                {
                    res.Status = NextSyncStatus.LocalError;
                    res.Error = "get " + remote + ": cannot create " + destDir + " (" + ex.Message + ")";
                    req.Complete(res);
                    SendIdle();
                    return;
                }
            }
            FileStream cur = null;
            MemoryStream mem = null;
            ReceivedFile curFile = null;
            long bytes = 0;
            bool osp = false;
            Action closeCurrent = delegate
            {
                if (cur != null) { try { cur.Close(); } catch (Exception) { } cur = null; }
                if (mem != null && curFile != null) { curFile.Data = mem.ToArray(); mem = null; }
            };
            SendFrame("G" + remote, 0);
            ReplyOutcome outcome;
            try
            {
                outcome = RecvReply(delegate (byte[] payload)
                {
                    if (payload.Length == 0) return false;
                    byte o = payload[0];
                    if (o == (byte)'F' && OsProtection.IsRefusal(payload))
                    {
                        // A read-protected SOURCE. A get has no failure frame, so
                        // a ZX Next Remote listener leads its empty walk with the
                        // marked 'F'+OSP purely so this is nameable; the 'B' still
                        // follows and ends the stream with nothing.
                        osp = true;
                        return false;
                    }
                    if (o == (byte)'N')
                    {
                        // 'N' + [4B filelen][1B namelen][name] - and those four
                        // length bytes are ALWAYS ZERO on this wire (the dot
                        // writes "length unknown", ZX Next Remote "size unknown,
                        // like the dot"), so no total is ever claimed.
                        int namelen = payload.Length > 5 ? payload[5] : 0;
                        int avail = Math.Max(0, Math.Min(namelen, payload.Length - 6));
                        string name = Encoding.UTF8.GetString(payload, 6, avail);
                        string rel = RemotePaths.RelativeNameUnder(remote, name);
                        if (rel.Length == 0) rel = RemotePaths.BaseName(name);
                        closeCurrent();
                        curFile = new ReceivedFile { RemoteName = name };
                        if (destDir != null)
                        {
                            string path = RemotePaths.SanitizeIncomingPath(destDir, rel);
                            string parent = Path.GetDirectoryName(path);
                            if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);
                            cur = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.Read);
                            curFile.LocalPath = path;
                        }
                        else
                        {
                            mem = new MemoryStream();
                        }
                        files.Add(curFile);
                    }
                    else if (o == (byte)'D')
                    {
                        int count = payload.Length - 1;
                        if (cur != null) cur.Write(payload, 1, count);
                        else if (mem != null) mem.Write(payload, 1, count);
                        if (curFile != null) curFile.Length += count;
                        bytes += count;
                        owner.RaiseProgress(new TransferProgressEventArgs(TransferKind.Get, remote, bytes, -1, files.Count,
                                                                          curFile != null ? curFile.RemoteName : ""));
                    }
                    else if (o == (byte)'E')
                    {
                        closeCurrent();
                    }
                    else if (o == (byte)'B')
                    {
                        closeCurrent();
                        return true;
                    }
                    return false;
                }, ReplyTimeoutMs, false);
            }
            finally
            {
                // Closed on every path: a half-written file left open would
                // refuse a retried get's own open on Windows.
                closeCurrent();
            }
            res.Files = files;
            res.BytesReceived = bytes;
            if (osp)
            {
                res.Status = NextSyncStatus.OsProtected;
                res.Error = OsProtection.ErrorText;
            }
            else if (outcome == ReplyOutcome.Completed)
            {
                res.Status = NextSyncStatus.Ok;
            }
            else
            {
                res.Status = NextSyncStatus.ConnectionDropped;
                res.Error = "get " + remote + ": failed (" + LogSize(bytes) + " received" +
                            (files.Count > 1 ? " over " + files.Count + " files" : "") + ")";
            }
            req.Complete(res);
        }

        private void ServePut(Request<PutResult> req)
        {
            byte[] data = req.Data;
            if (data == null)
            {
                try { data = File.ReadAllBytes(req.LocalPath); }
                catch (Exception ex)
                {
                    // Unlike the Python worker (which `continue`s and leaves this
                    // Poll unanswered, costing the Next one cipxfer timeout), the
                    // poll is answered with an idle: one frame per poll, always.
                    var bad = (PutResult)req.NewResult();
                    bad.RemotePath = req.Path;
                    bad.Status = NextSyncStatus.LocalError;
                    bad.Error = "put " + req.LocalPath + ": " + ex.Message;
                    req.Complete(bad);
                    SendIdle();
                    return;
                }
            }
            putData = data;
            putOfs = 0;
            putPkt = 0;
            putRetry = 0;
            putRestart = 0;
            pendingPut = req;
            inflight = null;    // owned by pendingPut from here
            Log("put: " + (req.LocalPath ?? "<bytes>") + " -> " + req.Path + " (" + data.Length + " bytes)");
            SendFrame("P" + req.Path, 0);
            // the Next now pulls the bytes via "Get" (served by ServeGet)
        }

        private void ServeStatus(Request<StatusResult> req, char opcode, string opName)
        {
            string path = req.Path;
            var res = (StatusResult)req.NewResult();
            res.Operation = opName;
            res.Path = path;
            bool ok = false, osp = false;
            SendFrame(opcode + path, 0);
            // late_ok: M, R and X are opcodes every seat has known since v1, so
            // a raw Poll while the reply is owed means the seat MISSED the
            // command and re-polled; the framed reply follows (9.7.35).
            var outcome = RecvReply(delegate (byte[] payload)
            {
                ok = payload.Length > 0 && payload[0] == (byte)'O';
                osp = OsProtection.IsRefusal(payload);
                return true;
            }, ReplyTimeoutMs, true);
            FillStatus(res, outcome, ok, osp, opName + " " + path);
            req.Complete(res);
        }

        private void ServeRename(Request<StatusResult> req)
        {
            var res = (StatusResult)req.NewResult();
            res.Operation = "rename";
            res.Path = req.Path;
            bool ok = false, osp = false;
            // 'V' + old + NUL + new, in one length-framed block.
            var payload = Concat(Encoding.UTF8.GetBytes("V" + req.Path), new byte[] { 0 }, Encoding.UTF8.GetBytes(req.Path2));
            SendFrame(payload, 0);
            var outcome = RecvReply(delegate (byte[] p)
            {
                ok = p.Length > 0 && p[0] == (byte)'O';
                osp = OsProtection.IsRefusal(p);
                return true;
            }, ReplyTimeoutMs, false);
            FillStatus(res, outcome, ok, osp, "rename " + req.Path + " -> " + req.Path2);
            req.Complete(res);
        }

        private void ServeRelease(Request<StatusResult> req)
        {
            // 'U' (dot v5.9+ / ZXNR 1.0.3+): the far side closes the OS's own
            // read handle on its running file. From here on, path-based ops
            // only. An older listener ignores the unknown opcode in silence.
            var res = (StatusResult)req.NewResult();
            res.Operation = "release";
            res.Path = "";
            bool ok = false, osp = false;
            SendFrame("U", 0);
            var outcome = RecvReply(delegate (byte[] p)
            {
                ok = p.Length > 0 && p[0] == (byte)'O';
                osp = OsProtection.IsRefusal(p);
                return true;
            }, ReplyTimeoutMs, false);
            if (outcome == ReplyOutcome.Completed)
                FillStatus(res, outcome, ok, osp, "release");
            else if (LinkDied(res, outcome, "release"))
            {
            }
            else
            {
                res.Status = NextSyncStatus.NotSupported;
                res.Error = "the far side did not answer the release op (needs .sync v5.9+ / ZXNR 1.0.3+)";
                Log(res.Error);
            }
            req.Complete(res);
        }

        private void FillStatus(NextSyncResult res, ReplyOutcome outcome, bool ok, bool osp, string what)
        {
            if (outcome != ReplyOutcome.Completed)
            {
                res.Status = NextSyncStatus.ConnectionDropped;
                res.Error = what + ": connection dropped";
            }
            else if (osp)
            {
                res.Status = NextSyncStatus.OsProtected;
                res.Error = OsProtection.ErrorText;
                Log("*** " + what + ": BLOCKED by the remote OS protection ***");
            }
            else if (ok)
            {
                res.Status = NextSyncStatus.Ok;
            }
            else
            {
                res.Status = NextSyncStatus.Failed;
                res.Error = what + ": FAILED on the Next";
                Log("*** " + res.Error + " ***");
            }
        }

        /// <summary>
        /// EOF under a reply is a dead link, never "not supported": the optional
        /// ops (drives, free, version, crc, release, rcpy, rfsize) read a
        /// swallowed opcode - a raw Poll, a timeout - as NotSupported, but a
        /// link that died is reported as such. The Python worker conflates the
        /// two in its wording; the status enum here does not.
        /// </summary>
        private bool LinkDied(NextSyncResult res, ReplyOutcome outcome, string what)
        {
            if (outcome != ReplyOutcome.Eof) return false;
            res.Status = NextSyncStatus.ConnectionDropped;
            res.Error = what + ": connection dropped";
            return true;
        }

        private void ServeDrives(Request<DrivesResult> req)
        {
            // getdrives: one pushed status block, 'O' + current drive letter +
            // one letter per mounted drive. An old dot (pre v5.1) ignores 'W'
            // and re-polls; its raw "Poll" fails the block parse, which lands
            // in the NotSupported fallback.
            var res = (DrivesResult)req.NewResult();
            string cur = "";
            var letters = new List<string>();
            SendFrame("W", 0);
            var outcome = RecvReply(delegate (byte[] p)
            {
                if (p.Length >= 2 && p[0] == (byte)'O')
                {
                    cur = ((char)p[1]).ToString();
                    for (int i = 2; i < p.Length; i++) letters.Add(((char)p[i]).ToString());
                }
                return true;
            }, ReplyTimeoutMs, false);
            if (outcome == ReplyOutcome.Completed && cur.Length > 0)
            {
                res.Status = NextSyncStatus.Ok;
                res.Current = cur;
                res.Mounted = letters;
            }
            else if (LinkDied(res, outcome, "drives"))
            {
            }
            else
            {
                res.Status = outcome == ReplyOutcome.Completed ? NextSyncStatus.Failed : NextSyncStatus.NotSupported;
                res.Error = "drives not supported (needs .sync v5.1+)";
                Log("This .sync dot does not report drives (pre v5.1); staying on the default drive.");
            }
            req.Complete(res);
        }

        private void ServeFreeSpace(Request<FreeSpaceResult> req)
        {
            // 'Z' (dot v5.2+): optional drive letter, answered with one status
            // block 'O' + 4 bytes little-endian = free 512-byte blocks
            // (F_GETFREE), or 'F' when the drive can't be measured.
            string drv = req.Path ?? "";
            var res = (FreeSpaceResult)req.NewResult();
            res.Drive = drv;
            long blocks = -1;
            bool fail = false;
            SendFrame("Z" + drv, 0);
            var outcome = RecvReply(delegate (byte[] p)
            {
                if (p.Length > 0 && p[0] == (byte)'F') fail = true;
                else if (p.Length >= 5 && p[0] == (byte)'O')
                    blocks = (uint)(p[1] | (p[2] << 8) | (p[3] << 16) | (p[4] << 24));
                return true;
            }, ReplyTimeoutMs, false);
            if (outcome == ReplyOutcome.Completed && blocks >= 0)
            {
                res.Status = NextSyncStatus.Ok;
                res.FreeBytes = blocks * 512;
            }
            else if (fail)
            {
                res.Status = NextSyncStatus.Failed;
                res.Error = "free space " + (drv.Length > 0 ? drv : "(current drive)") + ": FAILED on the Next";
                Log(res.Error);
            }
            else if (LinkDied(res, outcome, "free space " + (drv.Length > 0 ? drv : "(current drive)")))
            {
            }
            else
            {
                res.Status = NextSyncStatus.NotSupported;
                res.Error = "free space not supported (needs .sync v5.2+)";
                Log(res.Error);
            }
            req.Complete(res);
        }

        private void ServeIdent(Request<IdentResult> req)
        {
            var res = (IdentResult)req.NewResult();
            ReplyOutcome outcome;
            var who = QueryIdent(out outcome);
            if (who != null)
            {
                res.Status = NextSyncStatus.Ok;
                res.Ident = who;
                Log("version: " + who.Type + " " + who.Version);
            }
            else if (LinkDied(res, outcome, "version"))
            {
            }
            else
            {
                res.Status = NextSyncStatus.NotSupported;
                res.Error = "version not supported (needs ZXNR 1.0.2+ / .sync v5.8+)";
                Log("This listener does not answer the version query (pre ZXNR 1.0.2 / .sync v5.8).");
            }
            req.Complete(res);
        }

        /// <summary>
        /// The 'Y' exchange: (type, number), or null when the listener predates
        /// it - its raw "Poll" fails the block parse at once (no wait paid; the
        /// stray bytes hit the loop's catch-all idle reply). Answers this Poll.
        /// </summary>
        private NextIdent QueryIdent()
        {
            ReplyOutcome outcome;
            return QueryIdent(out outcome);
        }

        private NextIdent QueryIdent(out ReplyOutcome outcome)
        {
            string type = "", number = "";
            SendFrame("Y", 0);
            outcome = RecvReply(delegate (byte[] p)
            {
                if (p.Length >= 2 && p[0] == (byte)'O')
                {
                    int nul = Array.IndexOf(p, (byte)0, 1);
                    if (nul < 0)
                        type = Encoding.UTF8.GetString(p, 1, p.Length - 1);
                    else
                    {
                        type = Encoding.UTF8.GetString(p, 1, nul - 1);
                        number = Encoding.UTF8.GetString(p, nul + 1, p.Length - nul - 1);
                    }
                }
                return true;
            }, ReplyTimeoutMs, false);
            identAsked = true;
            ident = outcome == ReplyOutcome.Completed && type.Length > 0 ? new NextIdent(type, number) : null;
            owner.CacheIdent(ident);
            return ident;
        }

        private void ServeCrc(Request<CrcResult> req)
        {
            // CRC-32 of ONE file computed ON the Next ('K'): 'O' + 8 upper-case
            // hex digits, 'F' when the file did not open. The Next streams the
            // file and answers nothing meanwhile, so the wait is long. An old
            // listener ignores the unknown opcode - degrade like 'version'.
            string path = req.Path;
            var res = (CrcResult)req.NewResult();
            res.Path = path;
            string digest = "";
            bool fail = false, osp = false;
            SendFrame("K" + path, 0);
            var outcome = RecvReply(delegate (byte[] p)
            {
                if (p.Length >= 9 && p[0] == (byte)'O')
                    digest = Encoding.UTF8.GetString(p, 1, 8).ToUpperInvariant();
                else if (p.Length > 0 && p[0] == (byte)'F')
                {
                    fail = true;
                    osp = OsProtection.IsRefusal(p);
                }
                return true;
            }, (long)owner.CrcTimeout.TotalMilliseconds, false);
            uint value;
            if (outcome == ReplyOutcome.Completed && IsHexDigest(digest, out value))
            {
                res.Status = NextSyncStatus.Ok;
                res.Crc32Hex = digest;
                res.Crc32 = value;
                Log("crc32 " + path + ": " + digest);
            }
            else
            {
                string why;
                if (osp)
                {
                    res.Status = NextSyncStatus.OsProtected;
                    why = "the far side's OS protection refused the read";
                }
                else if (fail)
                {
                    res.Status = NextSyncStatus.Failed;
                    why = "the file did not open";
                }
                else if (outcome == ReplyOutcome.Completed)
                {
                    res.Status = NextSyncStatus.Failed;
                    why = "malformed digest from the Next";
                }
                else if (outcome == ReplyOutcome.Eof)
                {
                    res.Status = NextSyncStatus.ConnectionDropped;
                    why = "connection dropped";
                }
                else
                {
                    res.Status = NextSyncStatus.NotSupported;
                    why = "no answer: the file did not open, or the listener predates .sync v5.9.2 / ZXNR 1.0.8 / QLNR 0.1.0";
                }
                res.Error = "crc failed (" + why + ")";
                Log("crc32 " + path + ": " + why);
            }
            req.Complete(res);
        }

        private void ServeCopy(Request<CopyResult> req)
        {
            // Local copy ON the Next ('C', dot v5.2+): src and dst travel
            // NUL-separated like rename's paths. The Next answers with 'D'
            // progress blocks then one 'O'/'F'; the keepalives bound the gaps.
            var res = (CopyResult)req.NewResult();
            res.Source = req.Path;
            res.Destination = req.Path2;
            int files = 0;
            bool ok = false, osp = false, verdict = false;
            var payload = Concat(Encoding.UTF8.GetBytes("C" + req.Path), new byte[] { 0 }, Encoding.UTF8.GetBytes(req.Path2));
            SendFrame(payload, 0);
            var outcome = RecvReply(delegate (byte[] p)
            {
                if (p.Length > 0 && p[0] == (byte)'D')
                {
                    // Progress: named = a file copy just started (the name is its
                    // destination path), empty = the per-64KB keepalive.
                    string item = Encoding.UTF8.GetString(p, 1, p.Length - 1);
                    if (p.Length > 1) files++;
                    owner.RaiseProgress(new TransferProgressEventArgs(TransferKind.Copy, req.Path, 0, -1, files, item));
                    return false;
                }
                verdict = true;
                ok = p.Length > 0 && p[0] == (byte)'O';
                osp = OsProtection.IsRefusal(p);
                return true;
            }, ReplyTimeoutMs, false);
            res.FilesCopied = files;
            if (outcome == ReplyOutcome.Completed && verdict)
                FillStatus(res, outcome, ok, osp, "rcpy " + req.Path + " -> " + req.Path2);
            else if (LinkDied(res, outcome, "rcpy " + req.Path))
            {
            }
            else
            {
                res.Status = outcome == ReplyOutcome.Completed ? NextSyncStatus.Failed : NextSyncStatus.NotSupported;
                res.Error = "rcpy needs .sync v5.2+ (or the link dropped)";
                Log(res.Error);
            }
            if (res.Status == NextSyncStatus.Failed && outcome == ReplyOutcome.Completed && verdict)
                res.Error = "rcpy FAILED on the Next (copied files stay)";
            req.Complete(res);
        }

        private void ServeSize(Request<SizeResult> req)
        {
            // Tree/file size ON the Next ('S', rfsize, dot v5.2+). 'D' blocks
            // are progress (named per directory) and keepalives; the terminal
            // 'O' carries [4B files][4B dirs][4B size_lo][2B size_hi].
            string path = req.Path;
            var res = (SizeResult)req.NewResult();
            res.Path = path;
            bool got = false, fail = false, osp = false;
            SendFrame("S" + path, 0);
            var outcome = RecvReply(delegate (byte[] p)
            {
                if (p.Length > 0 && p[0] == (byte)'D')
                {
                    string item = Encoding.UTF8.GetString(p, 1, p.Length - 1);
                    owner.RaiseProgress(new TransferProgressEventArgs(TransferKind.Size, path, 0, -1, 0, item));
                    return false;
                }
                if (p.Length >= 15 && p[0] == (byte)'O')
                {
                    got = true;
                    res.Files = (uint)(p[1] | (p[2] << 8) | (p[3] << 16) | (p[4] << 24));
                    res.Directories = (uint)(p[5] | (p[6] << 8) | (p[7] << 16) | (p[8] << 24));
                    long lo = (uint)(p[9] | (p[10] << 8) | (p[11] << 16) | (p[12] << 24));
                    long hi = p[13] | (p[14] << 8);
                    res.Bytes = (hi << 32) | lo;
                }
                else if (p.Length > 0 && p[0] == (byte)'F')
                {
                    fail = true;
                    osp = OsProtection.IsRefusal(p);
                }
                return true;    // 'O' or 'F' both end the reply
            }, ReplyTimeoutMs, false);
            if (got)
                res.Status = NextSyncStatus.Ok;
            else if (osp)
            {
                res.Status = NextSyncStatus.OsProtected;
                res.Error = OsProtection.ErrorText;
            }
            else if (fail || outcome == ReplyOutcome.Completed)
            {
                res.Status = NextSyncStatus.Failed;
                res.Error = "rfsize " + path + ": FAILED on the Next (missing path?)";
                Log(res.Error);
            }
            else if (LinkDied(res, outcome, "rfsize " + path))
            {
            }
            else
            {
                res.Status = NextSyncStatus.NotSupported;
                res.Error = "rfsize needs .sync v5.2+ (or the link dropped)";
                Log(res.Error);
            }
            req.Complete(res);
        }

        private void ServeQuit(Request<QuitResult> req, bool exitApplication)
        {
            // 'Q' alone = leave listen mode; 'Q'+'X' = leave AND end the
            // application (/forceexit, ZX Next Remote 0.9.47+; a dot exits to
            // BASIC either way - it reads payload[0] and ignores the rest).
            SendFrame(exitApplication ? "QX" : "Q", 0);
            GoodbyeLinger();
            Log(exitApplication ? "listen: sent quit (exit application)" : "listen: sent quit");
            if (req != null)
            {
                var res = (QuitResult)req.NewResult();
                res.Status = NextSyncStatus.Ok;
                res.ExitApplication = exitApplication;
                req.Complete(res);
            }
            endRequested = true;
            endReason = exitApplication ? "quit sent (exit application)" : "quit sent";
        }

        /// <summary>
        /// Give a just-sent goodbye time to be READ before our FIN can race it
        /// off the peer's wire: drain until the peer closes, bounded. The dot
        /// (&lt;= 5.7.4) replies to the 'Q' with the classic raw "Bye" and then
        /// waits for "Later" before it closes - unanswered, it stared at silence
        /// for its full cipxfer timeout. 5.7.5+ skips the Bye and just closes.
        /// </summary>
        private void GoodbyeLinger()
        {
            long wait = (long)owner.GoodbyeLinger.TotalMilliseconds;
            var tail = new List<byte>();
            var tmp = new byte[256];
            try
            {
                while (true)
                {
                    if (!WaitReadable(wait)) break;
                    int n = conn.Receive(tmp, 0, tmp.Length, SocketFlags.None);
                    if (n == 0) break;                 // peer closed: the goodbye was read
                    for (int i = 0; i < n; i++) tail.Add(tmp[i]);
                    if (tail.Count > 8) tail.RemoveRange(0, tail.Count - 8);
                    if (Contains(tail, "Bye"))
                    {
                        SendFrame("Later", 0);
                        tail.Clear();
                    }
                    // anything else (stray polls): consumed, not answered
                }
            }
            catch (Exception) { }                  // timeout or reset: we tried, close
        }

        // ------------------------------------------------------------------
        // continuation steps: verify-after-put, the tree delete walk
        // ------------------------------------------------------------------

        private enum StepKind
        {
            PutVerify,
            PutVerifyDelete,
            TreeList,
            TreeDeleteFile,
            TreeRemoveDirectory
        }

        private sealed class Step
        {
            public StepKind Kind;
            public VerifyJob Verify;
            public TreeJob Tree;
            public string Path;
        }

        private sealed class VerifyJob
        {
            public Request<PutResult> Put;
            public PutResult Result;
            public string Crc;
            public long Size;
            public string Got;
        }

        private sealed class TreeJob
        {
            public Request<DeleteTreeResult> Request;
            public DeleteTreeResult Result;
            public string Root;
            public int Fails;
            public bool Osp;
            public bool Done;
        }

        private void ServeStep(Step step)
        {
            switch (step.Kind)
            {
                case StepKind.PutVerify: ServePutVerify(step); break;
                case StepKind.PutVerifyDelete: ServePutVerifyDelete(step); break;
                case StepKind.TreeList: ServeTreeList(step); break;
                case StepKind.TreeDeleteFile: ServeTreeRemove(step, 'X'); break;
                case StepKind.TreeRemoveDirectory: ServeTreeRemove(step, 'R'); break;
            }
        }

        private void ServePutVerify(Step step)
        {
            var job = step.Verify;
            var res = job.Result;
            if (!identAsked)
            {
                QueryIdent();                 // answers this Poll
                local.AddFirst(step);         // back on the next one
                return;
            }
            if (ident == null || !ident.AnswersCrc)
            {
                if (!verifySkipSaid)
                {
                    verifySkipSaid = true;
                    Log("crc32: verification skipped for this Next - its listener predates .sync v5.9.2 / " +
                        "ZX Next Remote 1.0.8 / QLNextRemote 0.1.0 (or does not answer the version query); files are sent unverified");
                }
                res.Status = NextSyncStatus.Ok;
                res.Verified = false;
                res.VerificationNote = "not verified: the listener predates the crc op (or does not answer the version query)";
                job.Put.Complete(res);
                SendIdle();                   // this Poll needs an answer
                return;
            }
            long wait = VerifyWaitMs(job.Size);
            Log("crc32 " + job.Put.Path + ": verifying " + job.Size + " bytes on the Next...");
            string digest = "";
            bool fail = false, osp = false;
            SendFrame("K" + job.Put.Path, 0);
            var outcome = RecvReply(delegate (byte[] p)
            {
                if (p.Length >= 9 && p[0] == (byte)'O')
                    digest = Encoding.UTF8.GetString(p, 1, 8).ToUpperInvariant();
                else if (p.Length > 0 && p[0] == (byte)'F')
                {
                    fail = true;
                    osp = OsProtection.IsRefusal(p);
                }
                return true;
            }, wait, false);
            uint value;
            if (outcome == ReplyOutcome.Completed && IsHexDigest(digest, out value))
            {
                if (digest == job.Crc)
                {
                    Log("crc32 " + job.Put.Path + ": " + digest + " - verified");
                    res.Status = NextSyncStatus.Ok;
                    res.Verified = true;
                    res.VerificationNote = "verified: CRC-32 " + digest;
                    job.Put.Complete(res);
                }
                else
                {
                    Log("crc32 " + job.Put.Path + ": " + digest + " on the Next, " + job.Crc +
                        " sent - MISMATCH, deleting the corrupted copy");
                    job.Got = digest;
                    local.AddFirst(new Step { Kind = StepKind.PutVerifyDelete, Verify = job });
                }
            }
            else
            {
                // Silence, 'F', 'F'+OSP or a malformed digest is NOT corruption:
                // keep the file, say it is unverified, report the put as done.
                string why;
                if (osp) why = "the far side's OS protection refused the read";
                else if (fail) why = "the file did not open on the Next";
                else if (outcome == ReplyOutcome.Completed) why = "malformed digest from the Next";
                else why = "no answer: the listener predates .sync v5.9.2 / ZXNR 1.0.8 / QLNR 0.1.0, or the link dropped";
                Log("crc32 " + job.Put.Path + ": not verified (" + why + ") - file kept");
                res.Status = NextSyncStatus.Ok;
                res.Verified = false;
                res.VerificationNote = "not verified: " + why;
                job.Put.Complete(res);
            }
        }

        private void ServePutVerifyDelete(Step step)
        {
            // The digest differed: delete the corrupted copy ('X'), then the red
            // verdict, then the put's ONE completion - as a failure.
            var job = step.Verify;
            var res = job.Result;
            bool ok = false, osp = false;
            SendFrame("X" + job.Put.Path, 0);
            var outcome = RecvReply(delegate (byte[] p)
            {
                ok = p.Length > 0 && p[0] == (byte)'O';
                osp = OsProtection.IsRefusal(p);
                return true;
            }, ReplyTimeoutMs, false);
            string msg;
            if (outcome == ReplyOutcome.Completed && ok)
            {
                Log("crc32 " + job.Put.Path + ": corrupted copy deleted");
                msg = "CRC-32 verification FAILED for " + job.Put.Path + ": " + job.Crc + " was sent but the Next holds " +
                      job.Got + ". The corrupted copy has been deleted from the Next - send the file again.";
            }
            else
            {
                string detail = osp ? "the far side's OS protection refused the delete"
                              : outcome == ReplyOutcome.Completed ? "the Next refused the delete"
                              : "no answer from the Next to the delete";
                Log("crc32 " + job.Put.Path + ": corrupted copy NOT deleted (" + detail + ")");
                msg = "CRC-32 verification FAILED for " + job.Put.Path + ": " + job.Crc + " was sent but the Next holds " +
                      job.Got + ". The corrupted copy could NOT be deleted from the Next (" + detail +
                      ") - remove it by hand and send the file again.";
            }
            owner.RaiseLog(ListenerLogLevel.Error, msg);
            res.Status = NextSyncStatus.Failed;
            res.CrcMismatch = true;
            res.Verified = false;
            res.VerificationNote = msg;
            res.Error = msg;
            job.Put.Complete(res);
        }

        private void StartDeleteTree(Request<DeleteTreeResult> req)
        {
            // Recursive folder delete: open a walk job and start with the root's
            // listing on this same poll.
            var job = new TreeJob { Request = req, Root = req.Path, Result = (DeleteTreeResult)req.NewResult() };
            job.Result.Root = req.Path;
            inflight = null;   // owned by the job from here
            ServeTreeList(new Step { Kind = StepKind.TreeList, Tree = job, Path = req.Path });
        }

        private void ServeTreeList(Step step)
        {
            // rmtree step 1: list one folder of the walk, then queue deleting its
            // files, walking its sub-folders and finally removing the (now empty)
            // folder itself, ahead of anything else - so the tree comes down
            // bottom-up.
            var job = step.Tree;
            string path = step.Path;
            var entries = new List<RemoteEntry>();
            bool failed = false;
            SendFrame("L" + path, 0);
            var outcome = RecvReply(delegate (byte[] p)
            {
                if (p.Length == 0) return false;
                byte o = p[0];
                if (o == (byte)'E') return true;
                if (o == (byte)'F') { failed = true; return true; }
                if (o == (byte)'D') ParseListing(p, entries);
                return false;
            }, ReplyTimeoutMs, false);
            if (outcome == ReplyOutcome.Completed)
            {
                var subs = new List<Step>();
                if (!failed)
                {
                    string baseDir = path.TrimEnd('/');
                    foreach (var e in entries)
                    {
                        if (e.Name == "." || e.Name == "..") continue;
                        string child = baseDir + "/" + e.Name;
                        subs.Add(new Step
                        {
                            Kind = e.IsDirectory ? StepKind.TreeList : StepKind.TreeDeleteFile,
                            Tree = job,
                            Path = child
                        });
                    }
                }
                // On a failed listing (gone, or not a folder) still try the
                // rmdir: it reports the failure if the folder is really stuck,
                // instead of stalling the job.
                subs.Add(new Step { Kind = StepKind.TreeRemoveDirectory, Tree = job, Path = path });
                for (int i = subs.Count - 1; i >= 0; i--)
                    local.AddFirst(subs[i]);
            }
            else
            {
                AbandonTree(job);
                job.Result.Status = NextSyncStatus.ConnectionDropped;
                job.Result.Error = "delete " + path + ": connection dropped";
                job.Request.Complete(job.Result);
            }
        }

        private void ServeTreeRemove(Step step, char opcode)
        {
            // rmtree steps 2/3: delete one file / one emptied folder. Only the
            // root folder's rmdir reports back - one result for the whole job.
            var job = step.Tree;
            string path = step.Path;
            bool ok = false, osp = false;
            SendFrame(opcode + path, 0);
            var outcome = RecvReply(delegate (byte[] p)
            {
                ok = p.Length > 0 && p[0] == (byte)'O';
                osp = OsProtection.IsRefusal(p);
                return true;
            }, ReplyTimeoutMs, false);
            if (outcome != ReplyOutcome.Completed)
            {
                AbandonTree(job);
                job.Result.Status = NextSyncStatus.ConnectionDropped;
                job.Result.Error = "delete " + path + ": connection dropped";
                job.Request.Complete(job.Result);
                return;
            }
            if (ok)
            {
                if (opcode == 'X') job.Result.FilesDeleted++;
                else job.Result.FoldersDeleted++;
            }
            else
            {
                job.Fails++;
                job.Osp = job.Osp || osp;
                Log("delete: could not remove " + path + (osp ? " (remote OS protection)" : ""));
            }
            // A protected member ends the whole tree delete now: the rest would
            // only repeat the refusal.
            bool rootDone = opcode == 'R' && path == job.Root;
            if (osp || rootDone)
            {
                if (osp) AbandonTree(job);
                job.Done = true;
                job.Result.Failures = job.Fails;
                if (job.Osp)
                {
                    job.Result.Status = NextSyncStatus.OsProtected;
                    job.Result.Error = OsProtection.ErrorText;
                }
                else if (job.Fails == 0)
                {
                    job.Result.Status = NextSyncStatus.Ok;
                }
                else
                {
                    job.Result.Status = NextSyncStatus.Failed;
                    job.Result.Error = "delete " + job.Root + ": " + job.Fails + " item(s) could not be removed";
                }
                job.Request.Complete(job.Result);
            }
        }

        /// <summary>Abandon the queued remainder of a tree job.</summary>
        private void AbandonTree(TreeJob job)
        {
            job.Done = true;
            for (var node = local.First; node != null;)
            {
                var next = node.Next;
                if (node.Value.Tree == job) local.Remove(node);
                node = next;
            }
        }

        // ------------------------------------------------------------------
        // the wire
        // ------------------------------------------------------------------

        private long ReplyTimeoutMs
        {
            get { return (long)owner.ReplyTimeout.TotalMilliseconds; }
        }

        /// <summary>Verify-after-put reply wait = 60 s floor + size / 15 KB/s worst case, capped at the crc op's hour.</summary>
        internal static long VerifyWaitMs(long sizeBytes)
        {
            double seconds = 60.0 + Math.Max(0, sizeBytes) / 15000.0;
            if (seconds > 3600.0) seconds = 3600.0;
            return (long)(seconds * 1000.0);
        }

        private void SendIdle()
        {
            SendFrame("I", 0);
        }

        private void SendFrame(string payload, byte packetNo)
        {
            SendFrame(Encoding.UTF8.GetBytes(payload), packetNo);
        }

        private void SendFrame(byte[] payload, byte packetNo)
        {
            var frame = NextSyncFraming.Build(payload, packetNo);
            int sent = 0;
            while (sent < frame.Length)
                sent += conn.Send(frame, sent, frame.Length - sent, SocketFlags.None);
        }

        private enum ReplyOutcome
        {
            Completed,
            Dropped,
            Eof
        }

        private enum BlockKind
        {
            Block,
            BadChecksum,
            Eof,
            Poll,
            BadHeader
        }

        private sealed class ReplyTimeoutException : Exception
        {
        }

        /// <summary>
        /// Read the framed blocks the Next pushes in reply to a command, acking
        /// each with "Ok"; the handler returns true to stop. A bad checksum is
        /// answered "Resend", a duplicate (a lost ack) re-acked, a sequence gap
        /// "Err seq" and the reply dropped. <paramref name="lateOk"/>: a raw Poll
        /// from the seat while the reply is owed is "no reply" - Dropped, its tail
        /// left in the socket for the loop's catch-all - unless lateOk, when it is
        /// swallowed ONCE and the wait continues (only for opcodes every seat
        /// knows: a seat that missed the command and re-polled; never answered,
        /// never resent).
        /// </summary>
        private ReplyOutcome RecvReply(Func<byte[], bool> handler, long timeoutMs, bool lateOk)
        {
            int expected = 0;
            bool polled = false;
            while (true)
            {
                byte[] payload;
                byte pkt;
                BlockKind kind;
                try
                {
                    kind = RecvBlock(timeoutMs, out payload, out pkt);
                }
                catch (ReplyTimeoutException)
                {
                    return ReplyOutcome.Dropped;
                }
                if (kind == BlockKind.Eof) return ReplyOutcome.Eof;
                if (kind == BlockKind.Poll)
                {
                    if (!lateOk || polled)
                    {
                        if (lateOk) Warn("Remote explorer: the Next re-polled twice while a reply was owed - reply dropped");
                        return ReplyOutcome.Dropped;
                    }
                    if (RecvExact(2, timeoutMs) == null) return ReplyOutcome.Eof;    // the Poll's "ll"
                    polled = true;
                    Warn("Remote explorer: the Next re-polled while a reply was owed - swallowed, still waiting");
                    continue;
                }
                if (kind == BlockKind.BadHeader) return ReplyOutcome.Dropped;
                if (kind == BlockKind.BadChecksum)
                {
                    SendFrame("Resend", (byte)expected);
                    continue;
                }
                if (pkt == ((expected - 1) & 0xff))
                {
                    SendFrame("Ok", pkt);          // duplicate (lost ack)
                    continue;
                }
                if (pkt != expected)
                {
                    SendFrame("Err seq", pkt);
                    return ReplyOutcome.Dropped;
                }
                bool stop = handler(payload);
                SendFrame("Ok", pkt);
                expected = (expected + 1) & 0xff;
                if (stop) return ReplyOutcome.Completed;
            }
        }

        /// <summary>
        /// One framed block from the seat. Poll when the two header bytes are
        /// the seat's own raw "Poll" (its last two bytes LEFT UNREAD): a frame
        /// can never start with "Po" (20591 is over the 4096 cap).
        /// </summary>
        private BlockKind RecvBlock(long timeoutMs, out byte[] payload, out byte packetNo)
        {
            payload = null;
            packetNo = 0;
            var hdr = RecvExact(2, timeoutMs);
            if (hdr == null) return BlockKind.Eof;
            if (hdr[0] == (byte)'P' && hdr[1] == (byte)'o') return BlockKind.Poll;
            int total = (hdr[0] << 8) | hdr[1];
            if (total < NextSyncFraming.MinTotal || total > NextSyncFraming.MaxTotal)
            {
                Warn("Remote explorer: bad block header " + hdr[0].ToString("x2") + " " + hdr[1].ToString("x2") +
                     " - not a frame, the reply is dropped");
                return BlockKind.BadHeader;
            }
            var rest = RecvExact(total - 2, timeoutMs);
            if (rest == null) return BlockKind.Eof;
            int count = total - NextSyncFraming.MinTotal;
            payload = new byte[count];
            Buffer.BlockCopy(rest, 0, payload, 0, count);
            byte c0, c1;
            NextSyncFraming.Checksums(payload, 0, count, out c0, out c1);
            if (rest[count] != c0 || rest[count + 1] != c1) return BlockKind.BadChecksum;
            packetNo = rest[count + 2];
            return BlockKind.Block;
        }

        /// <summary>Exactly n bytes, each recv bounded by the timeout (the gap between bytes, as the Python socket timeout is); null on EOF.</summary>
        private byte[] RecvExact(int n, long timeoutMs)
        {
            var buf = new byte[n];
            int got = 0;
            while (got < n)
            {
                if (!WaitReadable(timeoutMs)) throw new ReplyTimeoutException();
                int r = conn.Receive(buf, got, n - got, SocketFlags.None);
                if (r == 0) return null;
                got += r;
            }
            return buf;
        }

        /// <summary>Poll in slices of at most a second until data is readable (or the link is closed, which also reads as readable) or the timeout passes.</summary>
        private bool WaitReadable(long timeoutMs)
        {
            long deadline = Now() + timeoutMs;
            while (true)
            {
                long left = deadline - Now();
                if (left <= 0) return conn.Poll(0, SelectMode.SelectRead);
                int sliceMs = (int)Math.Min(left, 1000);
                if (conn.Poll(sliceMs * 1000, SelectMode.SelectRead)) return true;
            }
        }

        private static readonly Stopwatch Clock = Stopwatch.StartNew();

        private static long Now()
        {
            return Clock.ElapsedMilliseconds;
        }

        /// <summary>
        /// The verified payload of a framed 'F' status block at the start of a
        /// raw chunk, else null. Framing is [len_hi len_lo][payload][cs0][cs1][pktno]
        /// with len = payload + 5; trailing bytes are tolerated (TCP coalescing).
        /// </summary>
        private static byte[] FailBlockPayload(byte[] data)
        {
            if (data.Length < 6 || data[0] != 0) return null;
            int total = (data[0] << 8) | data[1];
            if (total < 6 || data.Length < total || data[2] != (byte)'F') return null;
            int count = total - 5;
            var payload = new byte[count];
            Buffer.BlockCopy(data, 2, payload, 0, count);
            byte c0, c1;
            NextSyncFraming.Checksums(payload, 0, count, out c0, out c1);
            return data[total - 3] == c0 && data[total - 2] == c1 ? payload : null;
        }

        private static bool IsWord(byte[] data, string word)
        {
            if (data.Length != word.Length) return false;
            for (int i = 0; i < word.Length; i++)
                if (data[i] != (byte)word[i]) return false;
            return true;
        }

        private static bool IsPullWord(byte[] data)
        {
            return IsWord(data, "Get") || IsWord(data, "Gee") || IsWord(data, "Retry") || IsWord(data, "Restart");
        }

        private static bool Contains(List<byte> tail, string word)
        {
            for (int start = 0; start + word.Length <= tail.Count; start++)
            {
                bool match = true;
                for (int i = 0; i < word.Length; i++)
                    if (tail[start + i] != (byte)word[i]) { match = false; break; }
                if (match) return true;
            }
            return false;
        }

        private static bool IsHexDigest(string digest, out uint value)
        {
            value = 0;
            if (digest == null || digest.Length != 8) return false;
            return uint.TryParse(digest, System.Globalization.NumberStyles.AllowHexSpecifier,
                                 System.Globalization.CultureInfo.InvariantCulture, out value);
        }

        private static byte[] Concat(byte[] a, byte[] b, byte[] c)
        {
            var r = new byte[a.Length + b.Length + c.Length];
            Buffer.BlockCopy(a, 0, r, 0, a.Length);
            Buffer.BlockCopy(b, 0, r, a.Length, b.Length);
            Buffer.BlockCopy(c, 0, r, a.Length + b.Length, c.Length);
            return r;
        }

        /// <summary>Bytes under a KB, then KB, then MB alongside once the shown kilobytes reach 1024 (zxnu_config.log_size).</summary>
        private static string LogSize(long n)
        {
            if (n < 1024) return n + " bytes";
            double kb = n / 1024.0;
            if (kb < 1024) return kb.ToString("0.0", System.Globalization.CultureInfo.InvariantCulture) + " KB";
            return kb.ToString("0.0", System.Globalization.CultureInfo.InvariantCulture) + " KB (" +
                   (kb / 1024.0).ToString("0.0", System.Globalization.CultureInfo.InvariantCulture) + " MB)";
        }
    }
}
