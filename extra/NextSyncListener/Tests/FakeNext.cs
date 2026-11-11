using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

namespace NextSync.Listener.Tests
{
    /// <summary>
    /// The dot's half of the -listen protocol, over a real loopback socket:
    /// a port of <c>mock_next</c> in ZX-Next-Unite's tests/test_remote_listen.py
    /// (plus the put-storm replay and the OS-protection paths of
    /// tests/test_listen.py). It dials in, says <c>Listen</c>, expects the
    /// framed <c>Listening</c>, then polls and executes whatever the library
    /// sends, pushing framed replies and expecting the framed <c>Ok</c> ack on
    /// each. Scenario paths (<c>/gone</c>, <c>/sys</c>, <c>/locked</c>,
    /// <c>/repoll</c>, <c>/corrupt</c>, <c>/die</c>, <c>/del</c>...) replay the
    /// Python mock's behaviours so the C# library is held to the same spec.
    /// <para>
    /// Like the Python mock it stays strictly lockstep - one message in flight -
    /// and settles briefly between turns, because the real transport is an ESP
    /// link delivering each command/frame as a discrete message while loopback
    /// TCP would happily coalesce them.
    /// </para>
    /// </summary>
    internal sealed class FakeNext : IDisposable
    {
        public sealed class Entry
        {
            public Entry(bool isDir, long size, string name) { IsDir = isDir; Size = size; Name = name; }
            public bool IsDir;
            public long Size;
            public string Name;
        }

        /// <summary>What the fake answers; defaults replay the Python mock's fixtures.</summary>
        public List<Entry> Entries = new List<Entry> { new Entry(true, 0, "GAMES"), new Entry(false, 1234, "boot.bas") };
        public byte[] FileBytes = Repeat(Encoding.ASCII.GetBytes("Hello Next!\r\n"), 5);

        /// <summary>The 'Y' answer; null plays a pre-5.8 listener that ignores 'Y' and re-polls.</summary>
        public byte[] Ident = Concat(Encoding.ASCII.GetBytes("Osync"), new byte[] { 0 }, Encoding.ASCII.GetBytes("9.9.9"));

        /// <summary>A listener that ignores 'K' and re-polls.</summary>
        public bool CrcSilent;

        /// <summary>After receiving the put packet with this index, ask "Retry" once (a checksum failure on the Next).</summary>
        public int ForceRetryAtPacket = -1;

        /// <summary>After receiving the put packet with this index, send "Restart" once (a packet-number mismatch) and pull from scratch.</summary>
        public int ForceRestartAtPacket = -1;

        /// <summary>Mock Next-side filesystem for the /del tree deletes: dirs are dictionaries, files are byte arrays.</summary>
        public Dictionary<string, object> Fs = new Dictionary<string, object>();

        // ---- what the fake saw ----
        public readonly List<KeyValuePair<string, string>> Ops = new List<KeyValuePair<string, string>>();
        public readonly Dictionary<string, byte[]> Files = new Dictionary<string, byte[]>();
        public readonly List<string> PutOsp = new List<string>();
        public readonly List<string> LsOsp = new List<string>();
        public readonly List<string> GetOsp = new List<string>();
        public readonly List<string> Rcpy = new List<string>();
        public string Ren;
        public string PutFail;
        public string Quit;                 // "" plain, "X" marked, null = never
        public byte[] ByeAnswer;
        public bool Released;
        public string LastPutPath;
        public byte[] LastPutBytes;
        public Exception Error;
        public readonly ManualResetEvent Done = new ManualResetEvent(false);
        public readonly ManualResetEvent Handshaken = new ManualResetEvent(false);

        private Socket sock;
        private Thread thread;
        private volatile bool disposing;
        private readonly object sync = new object();

        /// <summary>Dial the listener and start playing the Next on a thread.</summary>
        public void Connect(int port)
        {
            sock = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
            sock.NoDelay = true;
            sock.Connect(new IPEndPoint(IPAddress.Loopback, port));
            sock.ReceiveTimeout = 20000;
            thread = new Thread(Run) { IsBackground = true, Name = "FakeNext" };
            thread.Start();
        }

        /// <summary>Record an op the way the Python mock's cap['ops'] does (idle 'I' excluded).</summary>
        private void Record(string op, string arg)
        {
            lock (sync) Ops.Add(new KeyValuePair<string, string>(op, arg));
        }

        public bool SawOp(string op, string arg)
        {
            lock (sync)
            {
                foreach (var kv in Ops)
                    if (kv.Key == op && kv.Value == arg) return true;
                return false;
            }
        }

        /// <summary>
        /// Wait until a put landed in <see cref="Files"/>. A put's Task completes
        /// when the LAST DATA FRAME is served (the Python worker's rule); the dot
        /// then pulls one more, empty, frame as its end-of-file, and only then
        /// does this fake record the file - so a test that inspects the record
        /// right after the Task must let that last round trip finish.
        /// </summary>
        public bool WaitForFile(string path, int ms = 5000)
        {
            var deadline = DateTime.UtcNow.AddMilliseconds(ms);
            while (DateTime.UtcNow < deadline)
            {
                lock (sync) if (Files.ContainsKey(path)) return true;
                Thread.Sleep(5);
            }
            return false;
        }

        public int CountOp(string op)
        {
            lock (sync)
            {
                int n = 0;
                foreach (var kv in Ops) if (kv.Key == op) n++;
                return n;
            }
        }

        private void Run()
        {
            try
            {
                sock.Send(Encoding.ASCII.GetBytes("Listen"));
                var hello = RxPayload();
                if (Encoding.ASCII.GetString(hello) != "Listening")
                    throw new InvalidOperationException("expected Listening, got " + Encoding.ASCII.GetString(hello));
                Handshaken.Set();
                Loop();
            }
            catch (Exception ex)
            {
                // A socket closed by our own Dispose while the loop was live is
                // the test tearing down, not a protocol failure.
                if (!(disposing && (ex is ObjectDisposedException || ex is SocketException
                                    || (ex is InvalidOperationException && ex.Message.Contains("closed")))))
                    Error = ex;
            }
            finally
            {
                try { sock.Close(); } catch (Exception) { }
                Done.Set();
            }
        }

        private void Loop()
        {
            while (true)
            {
                Settle();
                sock.Send(Encoding.ASCII.GetBytes("Poll"));
                var cmd = RxPayload();
                char op = (char)cmd[0];
                string arg = Encoding.UTF8.GetString(cmd, 1, cmd.Length - 1);
                // esxDOS accepts an optional drive prefix ("m:/games") on every
                // path; the mock fs is drive-less, so strip it exactly like esxDOS
                // resolves it.
                string argNp = arg.Length >= 2 && arg[1] == ':' ? arg.Substring(2) : arg;
                if (op != 'I') Record(op.ToString(), arg);
                switch (op)
                {
                    case 'Q':
                        // 'Q' alone = leave listen mode; 'Q'+'X' = leave AND end
                        // the application. A <= 5.7.4 dot answers the 'Q' with the
                        // classic raw "Bye" and waits for "Later" before closing.
                        Quit = arg;
                        Settle();
                        sock.Send(Encoding.ASCII.GetBytes("Bye"));
                        sock.ReceiveTimeout = 5000;
                        try { ByeAnswer = RxPayload(); }
                        catch (Exception) { ByeAnswer = null; }
                        return;
                    case 'I':
                        continue;
                    case 'L':
                        ServeLs(arg, argNp);
                        break;
                    case 'G':
                        ServeGet(arg);
                        break;
                    case 'P':
                        if (!ServePut(arg, argNp)) return;
                        break;
                    case 'V':
                        {
                            var parts = arg.Split(new[] { '\0' }, 2);
                            if (parts[0].StartsWith("/sys") || parts[parts.Length - 1].StartsWith("/sys"))
                                Push("FOSP", 0);
                            else
                            {
                                Ren = arg;
                                Push("O", 0);
                            }
                            break;
                        }
                    case 'X':
                        ServeRm(argNp);
                        break;
                    case 'R':
                        ServeRmdir(argNp);
                        break;
                    case 'M':
                        ServeMkdir(argNp);
                        break;
                    case 'W':
                        Push("OCCM", 0);
                        break;
                    case 'Y':
                        if (Ident == null) continue;      // a pre-5.8 listener ignores 'Y' and re-polls
                        Push(Ident, 0);
                        break;
                    case 'K':
                        if (!ServeCrc(arg, argNp)) return;
                        break;
                    case 'Z':
                        if (arg == "E") Push("F", 0);
                        else Push(Concat(Encoding.ASCII.GetBytes("O"), Le32(2048)), 0);   // 2048 blocks = 1 MB
                        break;
                    case 'S':
                        if (arg.TrimEnd('/') == "/gone") Push("F", 0);
                        else
                        {
                            Push("D" + arg, 0);
                            Push("D", 1);
                            Push(Concat(Encoding.ASCII.GetBytes("O"), Le32(7), Le32(3), Le32(512), new byte[] { 1, 0 }), 2);
                        }
                        break;
                    case 'C':
                        {
                            Rcpy.Add(arg);
                            var parts = arg.Split(new[] { '\0' }, 2);
                            string csrc = parts[0], cdst = parts.Length > 1 ? parts[1] : "";
                            if (cdst.StartsWith("/sys")) Push("FOSP", 0);
                            else if (csrc.StartsWith("/locked")) Push("F", 0);
                            else
                            {
                                Push("D" + cdst, 0);
                                Push("D", 1);
                                Push("O", 2);
                            }
                            break;
                        }
                    case 'U':
                        Released = true;
                        Push("O", 0);
                        break;
                    default:
                        throw new InvalidOperationException("unexpected opcode " + op);
                }
            }
        }

        private void ServeLs(string arg, string argNp)
        {
            if (argNp.TrimEnd('/') == "/gone")
            {
                Push("F", 0);                    // opendir failed on the Next: folder is gone
                return;
            }
            if (argNp.TrimEnd('/').StartsWith("/sys"))
            {
                Push("FOSP", 0);                 // Read+write OS protection refuses the LISTING
                LsOsp.Add(arg);
                return;
            }
            if (argNp.TrimEnd('/').StartsWith("/del"))
            {
                // rmtree playground: list from the mock fs, with the "." / ".."
                // entries a real readdir yields.
                var node = FsNode(argNp) as Dictionary<string, object>;
                if (node == null)
                {
                    Push("F", 0);
                    return;
                }
                var list = new List<Entry> { new Entry(true, 0, "."), new Entry(true, 0, "..") };
                var names = new List<string>(node.Keys);
                names.Sort(StringComparer.Ordinal);
                foreach (var name in names)
                {
                    var child = node[name];
                    bool isDir = child is Dictionary<string, object>;
                    list.Add(new Entry(isDir, isDir ? 0 : ((byte[])child).Length, name));
                }
                Push(PackListing(list), 0);
                Push("E", 1);
                return;
            }
            Push(PackListing(Entries), 0);
            Push("E", 1);
        }

        private static byte[] PackListing(List<Entry> entries)
        {
            var pl = new List<byte> { (byte)'D' };
            foreach (var e in entries)
            {
                pl.Add((byte)(e.IsDir ? 1 : 0));
                pl.AddRange(Le32((uint)e.Size));
                var name = Encoding.UTF8.GetBytes(e.Name);
                pl.Add((byte)name.Length);
                pl.AddRange(name);
            }
            return pl.ToArray();
        }

        private void ServeGet(string arg)
        {
            // A directory get streams every file with its FULL Next path, exactly
            // as the dot's send_dir does.
            if (arg.StartsWith("/sys"))
            {
                // Read-protected source: a get has no failure frame, so a ZXNR
                // listener LEADS the empty walk with the marked 'F'+OSP, then 'B'.
                Push("FOSP", 0);
                Push("B", 1);
                GetOsp.Add(arg);
                return;
            }
            if (arg.TrimEnd('/').EndsWith("/lev"))
            {
                byte pkt = 0;
                var files = new[]
                {
                    new KeyValuePair<string, byte[]>("/games/lev/a.bin", Encoding.ASCII.GetBytes("AAAA")),
                    new KeyValuePair<string, byte[]>("/games/lev/sub/b.bin", Encoding.ASCII.GetBytes("BBBBBB"))
                };
                foreach (var f in files)
                {
                    Push(NewFileBlock(f.Key, f.Value.Length), pkt++);
                    Push(Concat(Encoding.ASCII.GetBytes("D"), f.Value), pkt++);
                    Push("E", pkt++);
                }
                Push("B", pkt);
                return;
            }
            string name = arg.TrimEnd('/');
            int slash = name.LastIndexOf('/');
            name = slash >= 0 ? name.Substring(slash + 1) : name;
            if (name.Length == 0) name = "f.bin";
            Push(NewFileBlock(name, FileBytes.Length), 0);
            Push(Concat(Encoding.ASCII.GetBytes("D"), FileBytes), 1);
            Push("E", 2);
            Push("B", 3);
        }

        private static byte[] NewFileBlock(string name, int length)
        {
            // 'N' + [4B filelen, big-endian in the Python mock - and zero on the
            // real wire][1B namelen][name]
            var n = Encoding.UTF8.GetBytes(name);
            var block = new List<byte> { (byte)'N' };
            block.AddRange(new[] { (byte)(length >> 24), (byte)(length >> 16), (byte)(length >> 8), (byte)length });
            block.Add((byte)n.Length);
            block.AddRange(n);
            return block.ToArray();
        }

        /// <summary>The dot's transfer(): pull the file the server serves with "Get", asking "Retry"/"Restart" where the scenario says. False = the session is over.</summary>
        private bool ServePut(string arg, string argNp)
        {
            if (arg.StartsWith("/sys"))
            {
                // A protecting ZXNextRemote refuses the put with the MARKED
                // 'F'+"OSP" status block; it still expects the server's "Ok" ack.
                Push("FOSP", 0);
                PutOsp.Add(arg);
                return true;
            }
            if (arg.StartsWith("/locked"))
            {
                // A put the Next can't create: push an 'F' status block and
                // expect the server's "Ok".
                Push("F", 0);
                PutFail = arg;
                return true;
            }
            if (arg.StartsWith("/abandon"))
            {
                // An OLD dot that could not open the file sends nothing at all -
                // it just goes back to polling. The server must report the put
                // as abandoned and answer that Poll normally.
                return true;
            }
            var buf = new List<byte>();
            int index = 0;
            byte expectedPkt = 0;
            bool retried = false, restarted = false;
            while (true)
            {
                Settle();
                sock.Send(Encoding.ASCII.GetBytes("Get"));
                byte pkt;
                var d = RxBlock(out pkt);
                if (pkt != expectedPkt)
                    throw new InvalidOperationException("put packet " + index + ": packetno " + pkt + ", expected " + expectedPkt);
                if (!retried && index == ForceRetryAtPacket)
                {
                    // Pretend the frame failed its checksum: the server must
                    // resend the SAME packet with the SAME packet number.
                    retried = true;
                    Settle();
                    sock.Send(Encoding.ASCII.GetBytes("Retry"));
                    byte pkt2;
                    var again = RxBlock(out pkt2);
                    if (pkt2 != pkt || !Same(again, d))
                        throw new InvalidOperationException("Retry did not resend the same packet");
                }
                if (!restarted && index == ForceRestartAtPacket)
                {
                    // Pretend the packet number did not match: the server must
                    // answer "Back" and serve the file from the top.
                    restarted = true;
                    Settle();
                    sock.Send(Encoding.ASCII.GetBytes("Restart"));
                    var back = RxPayload();
                    if (Encoding.ASCII.GetString(back) != "Back")
                        throw new InvalidOperationException("Restart was not answered with Back");
                    buf.Clear();
                    index = 0;
                    expectedPkt = 0;
                    continue;
                }
                if (d.Length == 0) break;
                buf.AddRange(d);
                index++;
                expectedPkt++;
            }
            var stored = buf.ToArray();
            LastPutPath = arg;
            LastPutBytes = stored;
            if (argNp.StartsWith("/corrupt") && stored.Length > 0)
                stored[stored.Length - 1] ^= 0xFF;          // a bad SD write
            lock (sync) Files[arg] = stored;
            return true;
        }

        private void ServeRm(string argNp)
        {
            if (argNp.StartsWith("/sys")) Push("FOSP", 0);
            else if (argNp.StartsWith("/del"))
            {
                Dictionary<string, object> parent;
                string name;
                FsParent(argNp, out parent, out name);
                if (parent != null && name != "locked.txt" && parent.ContainsKey(name) && !(parent[name] is Dictionary<string, object>))
                {
                    parent.Remove(name);
                    Push("O", 0);
                }
                else Push("F", 0);
            }
            else if (argNp.StartsWith("/corrupt/locked")) Push("F", 0);
            else if (argNp.StartsWith("/corrupt/osp")) Push("FOSP", 0);
            else Push("O", 0);
        }

        private void ServeRmdir(string argNp)
        {
            if (argNp.StartsWith("/sys")) Push("FOSP", 0);
            else if (argNp.StartsWith("/del"))
            {
                // esxDOS semantics: rmdir only removes an EMPTY directory.
                Dictionary<string, object> parent;
                string name;
                FsParent(argNp, out parent, out name);
                var node = parent != null && parent.ContainsKey(name) ? parent[name] as Dictionary<string, object> : null;
                if (node != null && node.Count == 0)
                {
                    parent.Remove(name);
                    Push("O", 0);
                }
                else Push("F", 0);
            }
            else Push("O", 0);
        }

        private void ServeMkdir(string argNp)
        {
            if (argNp.StartsWith("/sys")) Push("FOSP", 0);
            else if (argNp.StartsWith("/repoll2"))
            {
                // TWO raw Polls and never an 'O': the second Poll means the
                // command was lost, so the server must give the old verdict at
                // once, with the second Poll's tail left for the loop's
                // catch-all, whose framed 'I' this seat then reads as its answer.
                Settle(); sock.Send(Encoding.ASCII.GetBytes("Poll"));
                Settle(); sock.Send(Encoding.ASCII.GetBytes("Poll"));
                var idle = RxPayload();
                if (idle.Length == 0 || idle[0] != (byte)'I')
                    throw new InvalidOperationException("the second Poll was not answered with I");
            }
            else if (argNp.StartsWith("/repoll"))
            {
                // THE SEAT RE-POLLS BEFORE IT ANSWERS: a raw Poll goes out first
                // and the framed reply follows. The server must swallow the Poll
                // and wait - and NOT answer it (Push asserts the next bytes are
                // the 'O' ack).
                Settle(); sock.Send(Encoding.ASCII.GetBytes("Poll"));
                Push("O", 0);
            }
            else Push("O", 0);
        }

        /// <summary>False = the fake hung up.</summary>
        private bool ServeCrc(string arg, string argNp)
        {
            if (CrcSilent) return true;                    // a listener that ignores 'K' re-polls
            if (arg.TrimEnd('/') == "/gone" || argNp.StartsWith("/unv")) Push("F", 0);
            else if (argNp.StartsWith("/ospread")) Push("FOSP", 0);
            else if (argNp.StartsWith("/malformed")) Push("O1234", 0);
            else if (argNp.StartsWith("/die"))
            {
                sock.Close();                              // the link drops mid-check
                return false;
            }
            else
            {
                byte[] stored;
                bool known;
                lock (sync) known = Files.TryGetValue(arg, out stored);
                if (known)
                {
                    Push("O" + Crc32.ToHex(Crc32.Compute(stored)), 0);
                    if (argNp.StartsWith("/corrupt/hangup"))
                    {
                        sock.Close();                      // hangs up before the 'X' can be served
                        return false;
                    }
                }
                else
                {
                    // The digits are the CRC of the PATH, so a test can predict
                    // them without a file table.
                    Push("O" + Crc32.ToHex(Crc32.Compute(Encoding.UTF8.GetBytes(arg))), 0);
                }
            }
            return true;
        }

        // ---- framing ----

        private static void Settle()
        {
            Thread.Sleep(3);
        }

        private void Push(string payload, byte pkt)
        {
            Push(Encoding.UTF8.GetBytes(payload), pkt);
        }

        private void Push(byte[] payload, byte pkt)
        {
            Settle();
            sock.Send(NextSyncFraming.Build(payload, pkt));
            var ack = RxPayload();
            if (ack.Length == 0 || ack[0] != (byte)'O')
                throw new InvalidOperationException("expected the Ok ack, got " + Encoding.ASCII.GetString(ack));
        }

        private byte[] RxExact(int n)
        {
            var b = new byte[n];
            int got = 0;
            while (got < n)
            {
                int r = sock.Receive(b, got, n - got, SocketFlags.None);
                if (r == 0) throw new InvalidOperationException("the server closed the connection");
                got += r;
            }
            return b;
        }

        /// <summary>One framed block: the payload (checksums verified) and its packet number.</summary>
        private byte[] RxBlock(out byte pkt)
        {
            var hdr = RxExact(2);
            int total = (hdr[0] << 8) | hdr[1];
            if (total < 5 || total > 4096) throw new InvalidOperationException("bad frame length " + total);
            var rest = RxExact(total - 2);
            var payload = new byte[total - 5];
            Buffer.BlockCopy(rest, 0, payload, 0, payload.Length);
            byte c0, c1;
            NextSyncFraming.Checksums(payload, 0, payload.Length, out c0, out c1);
            if (rest[payload.Length] != c0 || rest[payload.Length + 1] != c1)
                throw new InvalidOperationException("the server sent a frame with a bad checksum");
            pkt = rest[payload.Length + 2];
            return payload;
        }

        private byte[] RxPayload()
        {
            byte pkt;
            return RxBlock(out pkt);
        }

        // ---- helpers ----

        private object FsNode(string path)
        {
            object node = Fs;
            foreach (var part in path.Split('/'))
            {
                if (part.Length == 0) continue;
                var dict = node as Dictionary<string, object>;
                if (dict == null || !dict.ContainsKey(part)) return null;
                node = dict[part];
            }
            return node;
        }

        private void FsParent(string path, out Dictionary<string, object> parent, out string leaf)
        {
            parent = null;
            leaf = null;
            var parts = new List<string>();
            foreach (var p in path.Split('/')) if (p.Length > 0) parts.Add(p);
            if (parts.Count == 0) return;
            object node = Fs;
            for (int i = 0; i < parts.Count - 1; i++)
            {
                var dict = node as Dictionary<string, object>;
                if (dict == null || !dict.ContainsKey(parts[i])) return;
                node = dict[parts[i]];
            }
            parent = node as Dictionary<string, object>;
            leaf = parts[parts.Count - 1];
        }

        public static byte[] Le32(uint v)
        {
            return new[] { (byte)v, (byte)(v >> 8), (byte)(v >> 16), (byte)(v >> 24) };
        }

        public static byte[] Concat(params byte[][] parts)
        {
            var list = new List<byte>();
            foreach (var p in parts) list.AddRange(p);
            return list.ToArray();
        }

        public static byte[] Repeat(byte[] pattern, int times)
        {
            var list = new List<byte>();
            for (int i = 0; i < times; i++) list.AddRange(pattern);
            return list.ToArray();
        }

        private static bool Same(byte[] a, byte[] b)
        {
            if (a.Length != b.Length) return false;
            for (int i = 0; i < a.Length; i++) if (a[i] != b[i]) return false;
            return true;
        }

        public void Dispose()
        {
            disposing = true;
            try { sock.Close(); } catch (Exception) { }
            if (thread != null) thread.Join(5000);
        }
    }
}
