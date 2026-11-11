using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using System.Threading.Tasks;

namespace NextSync.Listener
{
    /// <summary>
    /// The PC half of the NextSync <c>.sync5 -listen</c> Remote Explorer
    /// protocol: a TCP server a ZX Spectrum Next dials into (the .sync5 dot's
    /// <c>-listen</c> / <c>-L</c>, ZX Next Remote's Listener, QLNextRemote on a
    /// Sinclair QL). The Next then POLLS this server and every poll is answered
    /// with one command, whose reply the Next pushes back as framed blocks.
    /// <para>
    /// Ported from ZX-Next-Unite's <c>run_remote_listen_server</c> /
    /// <c>_re_session</c> (zxnu_workers.py): the same framing, checksums,
    /// retry and silence rules, one seat at a time (a second Next dialing in is
    /// told <c>Busy</c>, as that worker does when its roster is full).
    /// </para>
    /// <para>
    /// Threading: <see cref="Start"/> runs an accept thread; each seated Next
    /// gets a session thread that serves its polls from the command queue.
    /// The <c>*Async</c> methods queue a command and return a Task that
    /// completes when the Next has answered it - from any thread, and from a
    /// .NET Framework 4.5 app without <c>await</c> (<c>.Result</c> / <c>.Wait()</c>
    /// work: completions run on the thread pool, never on the session thread).
    /// Events are raised ON the accept / session thread: a UI must marshal.
    /// A command queued while no Next is connected waits for the next one to
    /// dial in; a command queued while a Next was connected fails with
    /// <see cref="NextSyncStatus.Cancelled"/> if that Next leaves before it ran
    /// (it is never run on a different machine).
    /// </para>
    /// </summary>
    public sealed class NextSyncListener : IDisposable
    {
        /// <summary>The NextSync port (the dot's and ZX Next Remote's default).</summary>
        public const int DefaultPort = 2048;

        /// <summary>
        /// The default served chunk, 512 bytes rather than the protocol's 1024 cap:
        /// Next CLONES (N-Go) corrupt >512-byte continuous UART bursts at the
        /// Medium/Fast rates, deterministically; ZX-Next-Unite's worker and ZX
        /// Next Remote's own server both dropped to 512 for that.
        /// </summary>
        public const int DefaultMaxPayload = 512;

        private readonly object gate = new object();
        private readonly LinkedList<Request> queue = new LinkedList<Request>();
        private Socket listenSocket;
        private Thread acceptThread;
        private ListenerSession session;
        private int nextSessionId;
        private volatile bool stopping;
        private bool running;
        private bool disposed;
        private NextIdent ident;
        private int maxPayload = DefaultMaxPayload;

        /// <summary>Listen on every IPv4 interface, port 2048.</summary>
        public NextSyncListener() : this(IPAddress.Any, DefaultPort) { }

        /// <summary>Listen on every IPv4 interface, on <paramref name="port"/> (0 = let the OS pick; see <see cref="LocalPort"/>).</summary>
        public NextSyncListener(int port) : this(IPAddress.Any, port) { }

        /// <summary>Listen on one address and port.</summary>
        public NextSyncListener(IPAddress bindAddress, int port)
        {
            if (bindAddress == null) throw new ArgumentNullException("bindAddress");
            if (port < 0 || port > 65535) throw new ArgumentOutOfRangeException("port");
            BindAddress = bindAddress;
            Port = port;
            ReplyTimeout = TimeSpan.FromSeconds(60);
            CrcTimeout = TimeSpan.FromHours(1);
            PeerSilenceLimit = TimeSpan.FromSeconds(620);
            HandshakeTimeout = TimeSpan.FromSeconds(10);
            GoodbyeLinger = TimeSpan.FromSeconds(2);
        }

        /// <summary>The address the listening socket binds to.</summary>
        public IPAddress BindAddress { get; private set; }

        /// <summary>The port asked for (0 = any).</summary>
        public int Port { get; private set; }

        /// <summary>The port actually bound once started (equals <see cref="Port"/> unless that was 0).</summary>
        public int LocalPort { get; private set; }

        /// <summary>
        /// Bytes per served data frame of a put, 1..1024. Default
        /// <see cref="DefaultMaxPayload"/> (512). Read per put.
        /// </summary>
        public int MaxPayload
        {
            get { return maxPayload; }
            set
            {
                if (value < 1 || value > NextSyncFraming.MaxPayloadCap) throw new ArgumentOutOfRangeException("value");
                maxPayload = value;
            }
        }

        /// <summary>
        /// How long one command's reply may go quiet BETWEEN blocks before the
        /// command is reported dropped (RE_REPLY_TIMEOUT, 60 s). Generous on
        /// purpose: the Next can legitimately pause for seconds (SD seeks, the
        /// directory re-open/skip walk, its own retry backoff).
        /// </summary>
        public TimeSpan ReplyTimeout { get; set; }

        /// <summary>The wait for a <c>'K'</c> answer: the Next streams the whole file first (~30 s per MB on the dot). Default 1 hour.</summary>
        public TimeSpan CrcTimeout { get; set; }

        /// <summary>
        /// Seconds of TOTAL silence from a seated Next, between commands, before it
        /// is assumed gone (PEER_SILENCE_LIMIT, 620 s - paired with ZX Next
        /// Remote's own dead-link guard, whose verdict lands first). The Next
        /// drives: an idle peer polls every second or two, so silence is the signal.
        /// </summary>
        public TimeSpan PeerSilenceLimit { get; set; }

        /// <summary>How long a new connection may take to send its 6-byte <c>Listen</c> keyword (10 s - over Wi-Fi it can arrive split or late).</summary>
        public TimeSpan HandshakeTimeout { get; set; }

        /// <summary>How long a sent <c>'Q'</c> is given to be READ before the socket closes (2 s), answering a <c>Bye</c> with <c>Later</c> meanwhile.</summary>
        public TimeSpan GoodbyeLinger { get; set; }

        /// <summary>
        /// Verify every put with the Next's own CRC-32 (<c>'K'</c>) against the
        /// bytes sent, on listeners that answer it (the ident is queried once per
        /// session if nobody asked). A different digest deletes the corrupted copy
        /// and fails the put with <see cref="PutResult.CrcMismatch"/>; silence,
        /// <c>'F'</c> or a malformed digest keep the file and report the put as
        /// done but unverified. Off by default here (ZX-Next-Unite's Settings
        /// toggle defaults to on): the check costs the Next a full read of the
        /// file after every put.
        /// </summary>
        public bool VerifyPuts { get; set; }

        /// <summary>True between <see cref="Start"/> and <see cref="Stop()"/>.</summary>
        public bool IsListening
        {
            get { lock (gate) return running; }
        }

        /// <summary>True while a Next is seated.</summary>
        public bool IsConnected
        {
            get { lock (gate) return session != null; }
        }

        /// <summary>The seated Next's address, or null.</summary>
        public IPEndPoint RemoteEndPoint
        {
            get { lock (gate) return session != null ? session.Remote : null; }
        }

        /// <summary>The seated Next's <c>'Y'</c> ident once queried (by <see cref="QueryIdentAsync"/> or a put verification); null until then or when the listener predates the query.</summary>
        public NextIdent Ident
        {
            get { lock (gate) return ident; }
        }

        /// <summary>A Next was seated (raised on the accept thread).</summary>
        public event EventHandler<NextConnectedEventArgs> Connected;

        /// <summary>The seated Next's session ended (raised on the session thread).</summary>
        public event EventHandler<NextDisconnectedEventArgs> Disconnected;

        /// <summary>A console-style line (raised on the accept or session thread).</summary>
        public event EventHandler<ListenerLogEventArgs> Log;

        /// <summary>Progress of a put, get, copy or size walk (raised on the session thread).</summary>
        public event EventHandler<TransferProgressEventArgs> Progress;

        // ------------------------------------------------------------------
        // lifecycle
        // ------------------------------------------------------------------

        /// <summary>
        /// Bind and start accepting. On Windows the socket is bound exclusively
        /// (a second instance fails cleanly instead of silently stealing the
        /// port); on POSIX with SO_REUSEADDR so a restart need not wait out
        /// TIME_WAIT. Throws <see cref="SocketException"/> when the port is taken.
        /// </summary>
        public void Start()
        {
            lock (gate)
            {
                if (disposed) throw new ObjectDisposedException("NextSyncListener");
                if (running) throw new InvalidOperationException("the listener is already started");
                var sock = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
                try
                {
                    if (IsWindows)
                        sock.ExclusiveAddressUse = true;
                    else
                        sock.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.ReuseAddress, true);
                    sock.Bind(new IPEndPoint(BindAddress, Port));
                    sock.Listen(8);
                }
                catch
                {
                    try { sock.Close(); } catch (Exception) { }
                    throw;
                }
                listenSocket = sock;
                LocalPort = ((IPEndPoint)sock.LocalEndPoint).Port;
                stopping = false;
                running = true;
                acceptThread = new Thread(AcceptLoop) { IsBackground = true, Name = "NextSync listener accept" };
                acceptThread.Start();
            }
            RaiseLog(ListenerLogLevel.Info, "Remote explorer: listening on port " + LocalPort + " for a Next running .sync5 -listen");
        }

        /// <summary>Stop with a 5 s grace period for the seated Next to collect its <c>'Q'</c>.</summary>
        public void Stop()
        {
            Stop(TimeSpan.FromSeconds(5));
        }

        /// <summary>
        /// Stop accepting; if a Next is seated, tell it to leave listen mode with
        /// a <c>'Q'</c> at its next poll (waiting up to <paramref name="gracePeriod"/>
        /// for that), then close everything. Commands still queued fail with
        /// <see cref="NextSyncStatus.Cancelled"/>. The listener can be started again.
        /// </summary>
        public void Stop(TimeSpan gracePeriod)
        {
            ListenerSession live;
            Socket sock;
            Thread accept;
            lock (gate)
            {
                if (!running) return;
                stopping = true;
                live = session;
                sock = listenSocket;
                accept = acceptThread;
            }
            if (live != null)
            {
                live.RequestQuit();
                if (gracePeriod > TimeSpan.Zero && !live.IsCurrentThread)
                    live.WaitEnded(gracePeriod);
                live.Close();
            }
            try { sock.Close(); } catch (Exception) { }
            if (accept != null && accept != Thread.CurrentThread)
                accept.Join(TimeSpan.FromSeconds(5));
            if (live != null && !live.IsCurrentThread)
                live.WaitEnded(TimeSpan.FromSeconds(5));
            List<Request> dropped;
            lock (gate)
            {
                dropped = new List<Request>(queue);
                queue.Clear();
                running = false;
                stopping = false;
                listenSocket = null;
                acceptThread = null;
                session = null;
            }
            foreach (var r in dropped)
                r.Fail(NextSyncStatus.Cancelled, "the listener was stopped before the command ran");
        }

        /// <summary>Stops the listener (2 s grace) and releases it.</summary>
        public void Dispose()
        {
            Stop(TimeSpan.FromSeconds(2));
            lock (gate) disposed = true;
        }

        // ------------------------------------------------------------------
        // commands
        // ------------------------------------------------------------------

        /// <summary>List a folder (<c>'L'</c>). An empty path lists the dot's current folder (<c>.</c>).</summary>
        public Task<ListingResult> ListAsync(string path, CancellationToken cancellationToken = default(CancellationToken))
        {
            var r = new Request<ListingResult> { Op = RequestOp.List, Path = string.IsNullOrEmpty(path) ? "." : path };
            return Enqueue(r, cancellationToken);
        }

        /// <summary>
        /// Fetch a file or a whole folder (<c>'G'</c>) into <paramref name="localDirectory"/>,
        /// recreating a fetched folder's sub-structure under its own name.
        /// </summary>
        public Task<GetResult> GetAsync(string remotePath, string localDirectory, CancellationToken cancellationToken = default(CancellationToken))
        {
            if (string.IsNullOrEmpty(remotePath)) throw new ArgumentException("remote path required", "remotePath");
            if (string.IsNullOrEmpty(localDirectory)) throw new ArgumentException("local directory required", "localDirectory");
            var r = new Request<GetResult> { Op = RequestOp.Get, Path = remotePath, LocalPath = localDirectory };
            return Enqueue(r, cancellationToken);
        }

        /// <summary>Fetch a file (<c>'G'</c>) into memory: <see cref="GetResult.Data"/> holds its bytes (a folder yields one <see cref="ReceivedFile"/> per file).</summary>
        public Task<GetResult> GetBytesAsync(string remotePath, CancellationToken cancellationToken = default(CancellationToken))
        {
            if (string.IsNullOrEmpty(remotePath)) throw new ArgumentException("remote path required", "remotePath");
            var r = new Request<GetResult> { Op = RequestOp.Get, Path = remotePath, LocalPath = null };
            return Enqueue(r, cancellationToken);
        }

        /// <summary>
        /// Send a local file (<c>'P'</c>): the Next pulls it in <see cref="MaxPayload"/>-byte
        /// frames with the dot's retry/restart rules. A <paramref name="remotePath"/>
        /// ending in <c>/</c> means "into that folder" (the local file name is appended).
        /// The file is read when the command is served, so a missing file reports
        /// <see cref="NextSyncStatus.LocalError"/> on the result rather than throwing here.
        /// </summary>
        public Task<PutResult> PutAsync(string localFile, string remotePath, CancellationToken cancellationToken = default(CancellationToken))
        {
            if (string.IsNullOrEmpty(localFile)) throw new ArgumentException("local file required", "localFile");
            if (string.IsNullOrEmpty(remotePath)) throw new ArgumentException("remote path required", "remotePath");
            if (remotePath.EndsWith("/") || remotePath.EndsWith("\\"))
                remotePath = remotePath + System.IO.Path.GetFileName(localFile);
            var r = new Request<PutResult> { Op = RequestOp.Put, Path = remotePath, LocalPath = localFile };
            return Enqueue(r, cancellationToken);
        }

        /// <summary>Send bytes (<c>'P'</c>) to a full remote path (a trailing <c>/</c> is refused: there is no local name to append).</summary>
        public Task<PutResult> PutAsync(byte[] data, string remotePath, CancellationToken cancellationToken = default(CancellationToken))
        {
            if (data == null) throw new ArgumentNullException("data");
            if (string.IsNullOrEmpty(remotePath)) throw new ArgumentException("remote path required", "remotePath");
            if (remotePath.EndsWith("/") || remotePath.EndsWith("\\"))
                throw new ArgumentException("a remote path for bytes must name the file, not a folder", "remotePath");
            var r = new Request<PutResult> { Op = RequestOp.Put, Path = remotePath, Data = data };
            return Enqueue(r, cancellationToken);
        }

        /// <summary>Create a folder (<c>'M'</c>). Note esxDOS answers <c>'F'</c> for "exists" too.</summary>
        public Task<StatusResult> MakeDirectoryAsync(string path, CancellationToken cancellationToken = default(CancellationToken))
        {
            return Enqueue(StatusRequest(RequestOp.MakeDirectory, path), cancellationToken);
        }

        /// <summary>Remove an EMPTY folder (<c>'R'</c>).</summary>
        public Task<StatusResult> RemoveDirectoryAsync(string path, CancellationToken cancellationToken = default(CancellationToken))
        {
            return Enqueue(StatusRequest(RequestOp.RemoveDirectory, path), cancellationToken);
        }

        /// <summary>Delete a file (<c>'X'</c>).</summary>
        public Task<StatusResult> DeleteFileAsync(string path, CancellationToken cancellationToken = default(CancellationToken))
        {
            return Enqueue(StatusRequest(RequestOp.DeleteFile, path), cancellationToken);
        }

        /// <summary>Rename or move a file or folder (<c>'V'</c>: old path, NUL, new path in one frame). NextZXOS refuses a rename onto an existing name.</summary>
        public Task<StatusResult> RenameAsync(string oldPath, string newPath, CancellationToken cancellationToken = default(CancellationToken))
        {
            if (string.IsNullOrEmpty(oldPath)) throw new ArgumentException("old path required", "oldPath");
            if (string.IsNullOrEmpty(newPath)) throw new ArgumentException("new path required", "newPath");
            var r = new Request<StatusResult> { Op = RequestOp.Rename, Path = oldPath, Path2 = newPath };
            return Enqueue(r, cancellationToken);
        }

        /// <summary>
        /// Release (<c>'U'</c>, dot v5.9+ / ZX Next Remote 1.0.3+): the far side
        /// closes the OS's own read handle on its running file so it can be
        /// swapped with rename/delete. CONTRACT: after this send ONLY path-based
        /// commands (rename, delete, quit) - anything that opens a file could be
        /// handed the freed handle number. An older listener answers with silence
        /// (<see cref="NextSyncStatus.NotSupported"/>).
        /// </summary>
        public Task<StatusResult> ReleaseAsync(CancellationToken cancellationToken = default(CancellationToken))
        {
            var r = new Request<StatusResult> { Op = RequestOp.Release, Path = "" };
            return Enqueue(r, cancellationToken);
        }

        /// <summary>The current and mounted drive letters (<c>'W'</c>, dot v5.1+). An older dot answers with silence.</summary>
        public Task<DrivesResult> GetDrivesAsync(CancellationToken cancellationToken = default(CancellationToken))
        {
            var r = new Request<DrivesResult> { Op = RequestOp.Drives, Path = "" };
            return Enqueue(r, cancellationToken);
        }

        /// <summary>Free space on a partition (<c>'Z'</c>, dot v5.2+): <c>"M"</c>, <c>"m:"</c>, or empty for the current drive.</summary>
        public Task<FreeSpaceResult> GetFreeSpaceAsync(string drive = "", CancellationToken cancellationToken = default(CancellationToken))
        {
            string d = (drive ?? "").Trim().TrimEnd(':').ToUpperInvariant();
            var r = new Request<FreeSpaceResult> { Op = RequestOp.FreeSpace, Path = d };
            return Enqueue(r, cancellationToken);
        }

        /// <summary>
        /// Who answers the session and which build (<c>'Y'</c>, dot v5.8+ / ZX Next
        /// Remote 1.0.2+): <c>sync 5.9.14</c>, <c>n2n 1.8.0</c>... An older listener
        /// ignores the opcode (<see cref="NextSyncStatus.NotSupported"/>). The answer
        /// is cached on <see cref="Ident"/> for the session.
        /// </summary>
        public Task<IdentResult> QueryIdentAsync(CancellationToken cancellationToken = default(CancellationToken))
        {
            var r = new Request<IdentResult> { Op = RequestOp.Ident, Path = "" };
            return Enqueue(r, cancellationToken);
        }

        /// <summary>
        /// CRC-32 of one file computed ON the Next (<c>'K'</c>, dot v5.9.2+ / ZX Next
        /// Remote 1.0.8+ / QLNextRemote 0.1.0+). The Next streams the file and
        /// answers nothing meanwhile, so the wait is <see cref="CrcTimeout"/>. A file
        /// that did not open answers <c>'F'</c>; an older listener answers with silence.
        /// </summary>
        public Task<CrcResult> GetCrc32Async(string path, CancellationToken cancellationToken = default(CancellationToken))
        {
            if (string.IsNullOrEmpty(path)) throw new ArgumentException("path required", "path");
            var r = new Request<CrcResult> { Op = RequestOp.Crc, Path = path };
            return Enqueue(r, cancellationToken);
        }

        /// <summary>
        /// Copy a file or folder LOCALLY on the Next (<c>'C'</c>, rcpy, dot v5.2+):
        /// no data crosses the wire; across partitions too (<c>c:/x</c> to <c>m:/y</c>).
        /// Already-copied files stay on a failure, like an interrupted cp.
        /// </summary>
        public Task<CopyResult> CopyOnNextAsync(string source, string destination, CancellationToken cancellationToken = default(CancellationToken))
        {
            if (string.IsNullOrEmpty(source)) throw new ArgumentException("source required", "source");
            if (string.IsNullOrEmpty(destination)) throw new ArgumentException("destination required", "destination");
            var r = new Request<CopyResult> { Op = RequestOp.Copy, Path = source, Path2 = destination };
            return Enqueue(r, cancellationToken);
        }

        /// <summary>Total size of a file or a whole folder tree, measured on the Next (<c>'S'</c>, rfsize, dot v5.2+).</summary>
        public Task<SizeResult> GetSizeAsync(string path, CancellationToken cancellationToken = default(CancellationToken))
        {
            if (string.IsNullOrEmpty(path)) throw new ArgumentException("path required", "path");
            var r = new Request<SizeResult> { Op = RequestOp.Size, Path = path };
            return Enqueue(r, cancellationToken);
        }

        /// <summary>
        /// Delete a whole folder: esxDOS rmdir only removes EMPTY folders, so the
        /// tree is walked over the ordinary protocol - list each folder, delete
        /// its files, recurse into its sub-folders, remove it once empty
        /// (bottom-up), one step per poll, ahead of anything else queued. A plain
        /// refusal is counted and the walk carries on; an OS-protection refusal
        /// ends it. Reports once, when the root folder is gone.
        /// </summary>
        public Task<DeleteTreeResult> DeleteTreeAsync(string path, CancellationToken cancellationToken = default(CancellationToken))
        {
            if (string.IsNullOrEmpty(path)) throw new ArgumentException("path required", "path");
            var r = new Request<DeleteTreeResult> { Op = RequestOp.DeleteTree, Path = path };
            return Enqueue(r, cancellationToken);
        }

        /// <summary>
        /// Tell the seated Next to leave listen mode (<c>'Q'</c>), or - with
        /// <paramref name="exitApplication"/> - to leave AND end its application
        /// (<c>'Q'</c>+<c>'X'</c>, ZX Next Remote 0.9.47+; a dot exits to BASIC
        /// either way). The session ends; the listener keeps accepting.
        /// </summary>
        public Task<QuitResult> QuitAsync(bool exitApplication = false, CancellationToken cancellationToken = default(CancellationToken))
        {
            var r = new Request<QuitResult> { Op = RequestOp.Quit, Path = "", Flag = exitApplication };
            return Enqueue(r, cancellationToken);
        }

        private static Request<StatusResult> StatusRequest(RequestOp op, string path)
        {
            if (string.IsNullOrEmpty(path)) throw new ArgumentException("path required", "path");
            return new Request<StatusResult> { Op = op, Path = path };
        }

        private Task<T> Enqueue<T>(Request<T> r, CancellationToken token) where T : NextSyncResult, new()
        {
            lock (gate)
            {
                if (disposed) throw new ObjectDisposedException("NextSyncListener");
                r.SessionId = session != null ? session.Id : 0;
                queue.AddLast(r);
            }
            if (token.CanBeCanceled)
            {
                r.Registration = token.Register(delegate { CancelRequest(r); });
                if (token.IsCancellationRequested) CancelRequest(r);
            }
            return r.Task;
        }

        private void CancelRequest(Request r)
        {
            bool removed = false;
            lock (gate)
            {
                if (!r.InFlight && !r.Completed)
                {
                    queue.Remove(r);
                    removed = true;
                }
            }
            if (removed)
                r.Fail(NextSyncStatus.Cancelled, "cancelled before the command ran");
        }

        // ------------------------------------------------------------------
        // session plumbing
        // ------------------------------------------------------------------

        internal bool StopRequested
        {
            get { return stopping; }
        }

        internal Request TryDequeue(int sessionId)
        {
            lock (gate)
            {
                for (var node = queue.First; node != null; node = node.Next)
                {
                    var r = node.Value;
                    if (r.SessionId == 0 || r.SessionId == sessionId)
                    {
                        queue.Remove(node);
                        r.InFlight = true;
                        return r;
                    }
                }
                return null;
            }
        }

        internal void CacheIdent(NextIdent value)
        {
            lock (gate) ident = value;
        }

        internal void OnSessionEnded(ListenerSession ended, string reason)
        {
            var orphans = new List<Request>();
            lock (gate)
            {
                if (session == ended)
                {
                    session = null;
                    ident = null;
                }
                for (var node = queue.First; node != null;)
                {
                    var next = node.Next;
                    if (node.Value.SessionId == ended.Id)
                    {
                        orphans.Add(node.Value);
                        queue.Remove(node);
                    }
                    node = next;
                }
            }
            foreach (var r in orphans)
                r.Fail(NextSyncStatus.Cancelled, "the Next this command was queued for left before it ran (" + reason + ")");
            var handler = Disconnected;
            if (handler != null)
            {
                try { handler(this, new NextDisconnectedEventArgs(ended.Remote, reason)); }
                catch (Exception) { }
            }
        }

        internal void RaiseLog(ListenerLogLevel level, string message)
        {
            var handler = Log;
            if (handler == null) return;
            try { handler(this, new ListenerLogEventArgs(level, message)); }
            catch (Exception) { }
        }

        internal void RaiseProgress(TransferProgressEventArgs args)
        {
            var handler = Progress;
            if (handler == null) return;
            try { handler(this, args); }
            catch (Exception) { }
        }

        private void AcceptLoop()
        {
            var listen = listenSocket;
            while (!stopping)
            {
                Socket conn;
                try
                {
                    conn = listen.Accept();
                }
                catch (ObjectDisposedException) { break; }
                catch (SocketException) { break; }
                catch (Exception) { break; }
                if (stopping)
                {
                    try { conn.Close(); } catch (Exception) { }
                    break;
                }
                HandleNewConnection(conn);
            }
        }

        private void HandleNewConnection(Socket conn)
        {
            IPEndPoint remote = null;
            try { remote = conn.RemoteEndPoint as IPEndPoint; } catch (Exception) { }
            string address = remote != null ? remote.Address.ToString() : "?";

            // The Next opens with the raw "Listen" handshake keyword. READ IT
            // PATIENTLY (the 2026-08-13 field report): over the ESP's Wi-Fi TCP
            // the keyword can arrive split, arrive late (the dot's CIPSEND
            // round-trip alone can eat a second), or a connect-retry probe can
            // close without a byte. A 10 s window, accumulate to 6 bytes, and NO
            // outcome here brings the listener down.
            var data = new List<byte>();
            try
            {
                conn.NoDelay = true;
                try { conn.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.KeepAlive, true); } catch (Exception) { }
                var deadline = DateTime.UtcNow + HandshakeTimeout;
                var tmp = new byte[1024];
                while (data.Count < 6)
                {
                    var left = deadline - DateTime.UtcNow;
                    if (left <= TimeSpan.Zero) throw new SocketException((int)SocketError.TimedOut);
                    int slice = (int)Math.Min(left.TotalMilliseconds * 1000, 1000000);
                    if (!conn.Poll(slice, SelectMode.SelectRead)) continue;
                    int n = conn.Receive(tmp, 0, tmp.Length, SocketFlags.None);
                    if (n == 0) break;
                    for (int i = 0; i < n; i++) data.Add(tmp[i]);
                }
            }
            catch (Exception)
            {
                // Timeout or reset mid-handshake: a probe, not a peer.
                try { conn.Close(); } catch (Exception) { }
                return;
            }
            if (data.Count == 0)
            {
                // Connected and closed without a word - the ESP's connect-retry
                // does this. Not an error: the real attempt is right behind it.
                RaiseLog(ListenerLogLevel.Info, "Remote explorer: silent probe from " + address);
                try { conn.Close(); } catch (Exception) { }
                return;
            }
            if (!StartsWith(data, "Listen"))
            {
                // A classic-sync (or unknown) client: refuse THIS connection but
                // keep serving.
                RaiseLog(ListenerLogLevel.Error, "Connected client did not request -listen mode.");
                try { conn.Close(); } catch (Exception) { }
                return;
            }
            bool busy;
            lock (gate) busy = session != null;
            if (busy)
            {
                // Over capacity: the turn-away. Busy-aware dots (5.7.2+) print
                // "Server busy"; older ones fail fast.
                try { conn.Send(NextSyncFraming.Build("Busy", 0)); } catch (Exception) { }
                try { conn.Close(); } catch (Exception) { }
                RaiseLog(ListenerLogLevel.Info, "Remote explorer: turned away a second Next at " + address + " - a session is already active (Busy).");
                return;
            }
            try
            {
                conn.Send(NextSyncFraming.Build("Listening", 0));
            }
            catch (Exception)
            {
                try { conn.Close(); } catch (Exception) { }
                return;
            }
            SetKeepAlive(conn);
            RaiseLog(ListenerLogLevel.Info, "Remote explorer: connected to " + address);
            ListenerSession s;
            lock (gate)
            {
                nextSessionId++;
                s = new ListenerSession(this, conn, remote, nextSessionId);
                session = s;
                ident = null;
            }
            var handler = Connected;
            if (handler != null)
            {
                try { handler(this, new NextConnectedEventArgs(remote)); }
                catch (Exception) { }
            }
            s.Start();
        }

        private static bool StartsWith(List<byte> data, string word)
        {
            if (data.Count < word.Length) return false;
            for (int i = 0; i < word.Length; i++)
                if (data[i] != (byte)word[i]) return false;
            return true;
        }

        /// <summary>
        /// Arm TCP keepalive on a seated link: SO_KEEPALIVE everywhere, and on
        /// Windows the 30 s idle / 30 s interval the Python worker sets through
        /// SIO_KEEPALIVE_VALS (the per-option knobs are .NET Core 3+ only, so the
        /// other platforms keep their OS defaults). Best effort, never throws.
        /// </summary>
        private static void SetKeepAlive(Socket conn)
        {
            try { conn.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.KeepAlive, true); } catch (Exception) { }
            if (!IsWindows) return;
            try
            {
                var values = new byte[12];
                WriteUInt32(values, 0, 1);
                WriteUInt32(values, 4, 30000);
                WriteUInt32(values, 8, 30000);
                conn.IOControl(IOControlCode.KeepAliveValues, values, null);
            }
            catch (Exception) { }
        }

        private static void WriteUInt32(byte[] buffer, int offset, uint value)
        {
            buffer[offset] = (byte)value;
            buffer[offset + 1] = (byte)(value >> 8);
            buffer[offset + 2] = (byte)(value >> 16);
            buffer[offset + 3] = (byte)(value >> 24);
        }

        internal static bool IsWindows
        {
            get { return Environment.OSVersion.Platform == PlatformID.Win32NT; }
        }
    }
}
