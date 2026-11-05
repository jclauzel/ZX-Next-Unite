// UARTRS232 - a CSpect plugin that wires the emulated ZX Spectrum Next's
// ESP UART to ZX-Next-Unite's RS232 ESP emulation (espemu.py) over a local
// TCP socket, so software running inside CSpect reaches the network exactly
// as it does inside MAME:
//
//     Next software (in CSpect) --UART ports--> this plugin --TCP--> espemu
//                                                                      |
//                                                real TCP to the LAN <-+
//
// The plugin moves BYTES: the ESP8266 "AT" dialect lives in espemu, where it
// is already shared with the MAME path and covered by the app's test suite.
// There is no baud rate on this wire - a byte the Next writes is on the
// socket a few instructions later, and a byte espemu sends is readable as
// soon as it arrives - so every UART speed the Next-side software selects
// works, with no "slow pacing" requirement.
//
// THE ONE THING IT KNOWS ABOUT THE DIALECT: where a "+IPD,<len>:" payload
// ends. A real UART delivers bytes at its baud rate, and Next software leans
// on that: the .sync5 dot drains "whatever is in the FIFO" after a +IPD
// header and trusts the wire to have nothing more to offer when the block is
// complete. Here a second block is already waiting (espemu frames a 1029
// byte packet as +IPD,1024 and +IPD,5 back to back), and the drain ran
// straight through it - every upload failed its checksum (measured, first
// cut). So after the last byte of each +IPD payload the status port answers
// "nothing waiting" for a few reads (GapStatusReads): the pause the wire
// would have made, exactly where the software needs one, and nowhere else.
//
// WHEN IT IS ACTIVE. The plugin owns the UART only when it has something to
// connect it to; otherwise it registers nothing and CSpect behaves as stock
// (its own UART, -com, another UART plugin):
//
//   * ZX_NEXT_UNITE_ESP=<host>:<port> in the environment (what ZX-Next-Unite
//     sets when it launches CSpect)            -> active, reconnects for ever
//   * ZX_NEXT_UNITE_ESP=off                    -> inactive
//   * UARTRS232.cfg beside the DLL, "enabled = on" / "enabled = off"
//                                              -> as it says
//   * neither ("auto")                         -> active only when an ESP
//     emulation answers on the configured endpoint at startup (default
//     127.0.0.1:2222) AND CSpect was not given -com AND no UARTReplacement
//     plugin sits in the same folder.
//
// NEXT TO UARTReplacement. That plugin (Robin Verhagen-Guest's, for a REAL
// ESP on a serial port - this one is named UARTRS232 so the two are never
// mistaken for each other) takes the same ports, and two plugins on one UART
// is a UART that answers twice. CSpect asks the plugin with the LOWER
// priority number first (measured on CSpect 3.3.1: at 0 or +100
// UARTReplacement, loaded first, kept the UART and drove the real module;
// at -100 this plugin got every byte). So the ports are registered at
// DefaultPriority = -100: when this plugin is active it owns the UART, when
// it is inactive it registers nothing and UARTReplacement works as always -
// the launcher's on/off is a switch between the emulated ESP and the real
// one, with both DLLs left where they are. "priority = N" in the cfg file
// or ZX_NEXT_UNITE_ESP_PRIORITY overrides the number.
//
// WHAT IT HOOKS (the ESP UART only; with the Pi UART selected every access
// falls through to CSpect):
//
//   0x133B write   a byte for the ESP                          (handled)
//   0x133B read    UART status: bit 0 = a byte is waiting,
//                  bit 1 = transmit buffer full, bit 4 = empty  (handled)
//   0x143B read    the next received byte, 0 when none          (handled)
//   0x153B write   bit 6 selects ESP / Pi          (observed, passed on)
//   0x163B write   bit 7 empties the FIFOs         (observed, passed on)
//   nextreg 0x02   bit 7 holds the ESP in reset    (observed, passed on):
//                  releasing it starts a FRESH espemu session, which is what
//                  a hardware reset of the module amounts to.
//
// Built with the C# 5 compiler every Windows ships (see build_plugin.py), so
// no newer language feature may be used here. Nothing in it is Windows-only
// (sockets, threads, the console), which is what mono needs to run it under
// CSpect on Linux and macOS - written for that, not yet run there.
//
// Proven end to end on CSpect 3.3.1 and 4.5.1 (Windows) with the .sync5 dot
// 5.7.5 and 5.9.14 at its fast speed and ZX Next Remote 1.8.0 at "fast":
// megabyte round trips byte-identical, the Next's own CRC-32 matching.
//
// MIT licence, (c) Julien Clauzel and the ZX-Next-Unite contributors.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net.Sockets;
using System.Reflection;
using System.Threading;
using Plugin;

[assembly: AssemblyTitle("UARTRS232")]
[assembly: AssemblyDescription("ZX-Next-Unite RS232 ESP emulation bridge for CSpect")]
[assembly: AssemblyProduct("ZX-Next-Unite")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

namespace ZXNextUnite
{
    public class EspPlugin : iPlugin
    {
        // The version is read out of this file by build_plugin.py and out of
        // the built DLL by tests/test_cspect_esp_plugin.py (the Banner
        // literal below): bump it with every change to the plugin.
        public const string PluginVersion = "1.0.0";
        public const string Banner = "UARTRS232 " + PluginVersion;

        private const string Tag = "UARTRS232: ";
        private const string EnvEndpoint = "ZX_NEXT_UNITE_ESP";
        private const string EnvVerbose = "ZX_NEXT_UNITE_ESP_VERBOSE";
        private const string EnvPriority = "ZX_NEXT_UNITE_ESP_PRIORITY";
        private const string CfgName = "UARTRS232.cfg";
        private const string DefaultHost = "127.0.0.1";
        private const int DefaultPort = 2222;

        private const int PORT_UART_TX = 0x133B;
        private const int PORT_UART_RX = 0x143B;
        private const int PORT_UART_SELECT = 0x153B;
        private const int PORT_UART_FRAME = 0x163B;
        private const int REG_RESET = 0x02;

        private const byte STATUS_RX_AVAILABLE = 0x01;
        private const byte STATUS_TX_FULL = 0x02;
        private const byte STATUS_TX_EMPTY = 0x10;

        // Received bytes wait here for the Z80. Bounded: once it is full the
        // link thread stops reading the socket, TCP pushes back on espemu,
        // and espemu pauses the upstream link - its own flow control, reused.
        private const int RxCapacity = 128 * 1024;
        // Bytes the Z80 wrote and the socket has not taken yet. They leave in
        // one send per burst (see FlushTx's callers); the buffer only fills
        // when espemu stops reading, and the status port then says "full".
        private const int TxCapacity = 64 * 1024;
        private const int TxBusyAt = 32 * 1024;
        private const int TxFlushAt = 1024;
        // How many status reads answer "nothing waiting" after the last byte
        // of a +IPD payload. One is what the dot's drain loop needs; a few
        // leaves room for software that looks twice. Far below the shortest
        // wait any consumer has for the next byte (the dot: 20 reads).
        private const int GapStatusReads = 4;
        // "+IPD,<len>:" or "+IPD,<link>,<len>:" - nothing longer is a header.
        private const int IpdHeaderMax = 16;
        private const string IpdPrefix = "+IPD,";
        private const int AutoProbeMs = 250;
        private const int RetryMs = 1000;
        // CSpect serves the LOWER number first; UARTReplacement registers at
        // 0. See "NEXT TO UARTReplacement" at the top of this file.
        private const int DefaultPriority = -100;

        private readonly object rxLock = new object();
        private readonly byte[] rx = new byte[RxCapacity];
        private int rxHead;
        private volatile int rxCount;
        // Stream positions (bytes received / bytes handed to the Z80) and
        // the positions after which the UART must read empty for a moment.
        private long rxWritten;
        private long rxRead;
        private readonly Queue<long> gaps = new Queue<long>();
        private volatile int gapPolls;
        // The +IPD tracker (link thread only): 0-4 = that many characters of
        // "+IPD," matched, 5 = in the header's numbers, 6 = in the payload.
        private int ipdState;
        private int ipdHeaderLen;
        private long ipdField;
        private long ipdLeft;

        private readonly object txLock = new object();
        private readonly byte[] tx = new byte[TxCapacity];
        private volatile int txLen;
        private int statusStreak;

        private readonly AutoResetEvent wake = new AutoResetEvent(false);
        private volatile Socket link;
        private volatile bool running;
        private volatile bool espSelected = true;
        private volatile bool heldInReset;
        private Thread linkThread;
        private Socket firstSocket;

        private string host = DefaultHost;
        private int port = DefaultPort;
        private bool verbose;
        private int priority = DefaultPriority;

        public string GetName()
        {
            return "ZX-Next-Unite ESP";
        }

        public string GetDescription()
        {
            return "Connects the Next's ESP UART to ZX-Next-Unite's RS232 ESP emulation (" + Banner + ")";
        }

        // ---------------------------------------------------------------- init

        public List<sIO> Init(iCSpect _CSpect)
        {
            List<sIO> ports = new List<sIO>();
            try
            {
                string why;
                if (!Configure(out why))
                {
                    Say(Banner + " inactive - " + why);
                    return ports;
                }
                running = true;
                linkThread = new Thread(LinkLoop);
                linkThread.IsBackground = true;
                linkThread.Name = "UARTRS232 link";
                linkThread.Start();

                ports.Add(new sIO(PORT_UART_TX, eAccess.Port_Write, 0, priority));
                ports.Add(new sIO(PORT_UART_TX, eAccess.Port_Read, 0, priority));
                ports.Add(new sIO(PORT_UART_RX, eAccess.Port_Read, 0, priority));
                ports.Add(new sIO(PORT_UART_SELECT, eAccess.Port_Write, 0, priority));
                ports.Add(new sIO(PORT_UART_FRAME, eAccess.Port_Write, 0, priority));
                ports.Add(new sIO(REG_RESET, eAccess.NextReg_Write, 0, priority));
                Say(Banner + " active - the Next's ESP UART goes to the ESP emulation at "
                    + host + ":" + port.ToString(CultureInfo.InvariantCulture) + " (" + why
                    + "; priority " + priority.ToString(CultureInfo.InvariantCulture) + ")");
                if (File.Exists(Path.Combine(PluginFolder(), "UARTReplacement.dll")))
                    Say("the UARTReplacement plugin is installed here too: UARTRS232 answers the"
                        + " UART first, so the real ESP on its serial port is not used in this run");
            }
            catch (Exception ex)
            {
                // A plugin that throws out of Init takes CSpect's start with
                // it; one that registers nothing is merely absent.
                running = false;
                ports.Clear();
                Say("could not start: " + ex.Message);
            }
            return ports;
        }

        // Decide whether the plugin owns the UART for this run. False leaves
        // CSpect exactly as it is without the plugin; "why" says which rule
        // decided, for the one console line Init prints.
        private bool Configure(out string why)
        {
            string mode = "auto";
            string cfgPath = null;
            try
            {
                cfgPath = Path.Combine(PluginFolder(), CfgName);
                if (File.Exists(cfgPath))
                {
                    foreach (string raw in File.ReadAllLines(cfgPath))
                    {
                        string line = raw.Trim();
                        if (line.Length == 0 || line[0] == '#' || line[0] == ';')
                            continue;
                        int eq = line.IndexOf('=');
                        if (eq <= 0)
                            continue;
                        string key = line.Substring(0, eq).Trim().ToLowerInvariant();
                        string val = line.Substring(eq + 1).Trim();
                        if (key == "enabled")
                            mode = ModeOf(val, mode);
                        else if (key == "host" && val.Length > 0)
                            host = val;
                        else if (key == "port")
                            port = PortOf(val, port);
                        else if (key == "verbose")
                            verbose = Truthy(val);
                        else if (key == "priority")
                            priority = IntOf(val, priority);
                    }
                }
            }
            catch (Exception ex)
            {
                Say("could not read " + CfgName + ": " + ex.Message);
            }

            string env = Environment.GetEnvironmentVariable(EnvEndpoint);
            string envVerbose = Environment.GetEnvironmentVariable(EnvVerbose);
            if (!string.IsNullOrEmpty(envVerbose))
                verbose = Truthy(envVerbose);
            string envPriority = Environment.GetEnvironmentVariable(EnvPriority);
            if (!string.IsNullOrEmpty(envPriority))
                priority = IntOf(envPriority, priority);
            if (env != null && env.Trim().Length > 0)
            {
                // The launcher's word is final, in both directions.
                env = env.Trim();
                if (Falsy(env))
                {
                    why = EnvEndpoint + " says off";
                    return false;
                }
                int colon = env.LastIndexOf(':');
                if (colon > 0)
                {
                    host = env.Substring(0, colon).Trim();
                    port = PortOf(env.Substring(colon + 1), port);
                }
                else
                {
                    port = PortOf(env, port);
                }
                why = "set by " + EnvEndpoint;
                return true;
            }
            if (mode == "off")
            {
                why = CfgName + " says enabled = off";
                return false;
            }
            if (mode == "on")
            {
                why = CfgName + " says enabled = on";
                return true;
            }

            // Automatic: step aside for anything else that owns the UART,
            // and take it only when an ESP emulation is there to answer.
            foreach (string arg in Environment.GetCommandLineArgs())
            {
                string low = arg.ToLowerInvariant();
                if (low.StartsWith("-com=") || low == "-com")
                {
                    why = "CSpect was started with -com, the serial port keeps the UART";
                    return false;
                }
            }
            try
            {
                if (File.Exists(Path.Combine(PluginFolder(), "UARTReplacement.dll")))
                {
                    why = "the UARTReplacement plugin is installed beside it and keeps the UART"
                        + " (\"enabled = on\" in " + CfgName + " overrides)";
                    return false;
                }
            }
            catch (Exception)
            {
            }
            Socket probe = Probe(host, port, AutoProbeMs);
            if (probe == null)
            {
                why = "no ESP emulation answers at " + host + ":"
                    + port.ToString(CultureInfo.InvariantCulture)
                    + " (start it in ZX-Next-Unite, then restart CSpect; or launch CSpect from ZX-Next-Unite)";
                return false;
            }
            firstSocket = probe;
            why = "an ESP emulation answered there";
            return true;
        }

        // The folder the plugin was loaded from. CSpect loads its plugins
        // from MEMORY (Assembly.Load of the file's bytes), so this assembly's
        // own Location is empty - measured on CSpect 4.5.1 - and the folder
        // is CSpect's: the application base directory.
        private static string PluginFolder()
        {
            try
            {
                string location = Assembly.GetExecutingAssembly().Location;
                if (!string.IsNullOrEmpty(location))
                    return Path.GetDirectoryName(location);
            }
            catch (Exception)
            {
            }
            return AppDomain.CurrentDomain.BaseDirectory ?? ".";
        }

        private static string ModeOf(string val, string fallback)
        {
            string low = val.Trim().ToLowerInvariant();
            if (low == "auto")
                return "auto";
            if (Truthy(low))
                return "on";
            if (Falsy(low))
                return "off";
            return fallback;
        }

        private static bool Truthy(string val)
        {
            string low = (val ?? "").Trim().ToLowerInvariant();
            return low == "1" || low == "on" || low == "true" || low == "yes";
        }

        private static bool Falsy(string val)
        {
            string low = (val ?? "").Trim().ToLowerInvariant();
            return low == "0" || low == "off" || low == "false" || low == "no";
        }

        private static int IntOf(string val, int fallback)
        {
            int parsed;
            if (int.TryParse((val ?? "").Trim(), NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out parsed))
                return parsed;
            return fallback;
        }

        private static int PortOf(string val, int fallback)
        {
            int parsed;
            if (int.TryParse((val ?? "").Trim(), NumberStyles.None, CultureInfo.InvariantCulture, out parsed)
                && parsed >= 1 && parsed <= 65535)
                return parsed;
            return fallback;
        }

        // A bounded connect. A refused connect to a closed local port takes
        // Windows about two seconds to report, which CSpect's start must not
        // wait for: past the limit the attempt is abandoned.
        private static Socket Probe(string host, int port, int timeoutMs)
        {
            Socket s = null;
            try
            {
                s = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
                IAsyncResult pending = s.BeginConnect(host, port, null, null);
                if (pending.AsyncWaitHandle.WaitOne(timeoutMs) && s.Connected)
                {
                    s.EndConnect(pending);
                    return s;
                }
            }
            catch (Exception)
            {
            }
            CloseQuietly(s);
            return null;
        }

        // ---------------------------------------------------------- the link

        // One thread owns the connection: it connects, reads the socket into
        // the receive buffer until the link ends, and connects again. The
        // emulation thread never waits on a connect.
        private void LinkLoop()
        {
            bool saidDown = false;
            while (running)
            {
                if (heldInReset)
                {
                    wake.WaitOne(50);
                    continue;
                }
                Socket s = firstSocket;
                firstSocket = null;
                if (s == null)
                {
                    try
                    {
                        s = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
                        s.Connect(host, port);
                    }
                    catch (Exception ex)
                    {
                        CloseQuietly(s);
                        if (!saidDown)
                        {
                            saidDown = true;
                            Say("no ESP emulation at " + host + ":"
                                + port.ToString(CultureInfo.InvariantCulture)
                                + " (" + ex.Message + ") - the Next sees no ESP until one answers; still trying");
                        }
                        wake.WaitOne(RetryMs);
                        continue;
                    }
                }
                try
                {
                    s.NoDelay = true;
                    s.Blocking = false;
                }
                catch (Exception)
                {
                    CloseQuietly(s);
                    wake.WaitOne(RetryMs);
                    continue;
                }
                // A new connection is a new module: nothing of the last one
                // may be read by, or sent for, this one.
                ClearRx();
                ClearTx();
                ipdState = 0;
                link = s;
                if (saidDown || verbose)
                    Say("connected to the ESP emulation at " + host + ":"
                        + port.ToString(CultureInfo.InvariantCulture));
                saidDown = false;

                Pump(s);

                if (ReferenceEquals(link, s))
                    link = null;
                CloseQuietly(s);
                // What the Z80 has not read yet stays readable, as it would
                // in the UART's own FIFO; what it wrote has nowhere to go.
                ClearTx();
                if (running && !heldInReset)
                {
                    saidDown = true;
                    Say("the ESP emulation closed the connection - trying again");
                    wake.WaitOne(RetryMs);
                }
            }
        }

        // Socket -> receive buffer, until the link ends or is taken away
        // (reset, quit). Poll + a non-blocking Receive rather than a blocking
        // read, so the loop sees those without anyone closing a socket under
        // a blocked call.
        private void Pump(Socket s)
        {
            byte[] buf = new byte[16 * 1024];
            while (running && ReferenceEquals(link, s))
            {
                int free = RxCapacity - rxCount;
                if (free <= 0)
                {
                    Thread.Sleep(2);
                    continue;
                }
                int n;
                try
                {
                    if (!s.Poll(100000, SelectMode.SelectRead))
                        continue;
                    SocketError err;
                    n = s.Receive(buf, 0, Math.Min(buf.Length, free), SocketFlags.None, out err);
                    if (err == SocketError.WouldBlock)
                        continue;
                    if (err != SocketError.Success || n <= 0)
                        return;
                }
                catch (Exception)
                {
                    return;
                }
                lock (rxLock)
                {
                    // Only this thread adds, so the room measured above can
                    // only have grown.
                    int at = (rxHead + rxCount) % RxCapacity;
                    int first = Math.Min(n, RxCapacity - at);
                    Buffer.BlockCopy(buf, 0, rx, at, first);
                    if (first < n)
                        Buffer.BlockCopy(buf, first, rx, 0, n - first);
                    TrackIpd(buf, n);
                    rxWritten += n;
                    rxCount = rxCount + n;
                }
            }
        }

        // Follow the ESP's output just far enough to know where each +IPD
        // payload ends, and note that position: the Z80 must see the UART
        // empty for a moment once it has read up to there. Called with
        // rxLock held, before rxWritten moves past this chunk.
        //
        // Only the two shapes espemu emits are headers: "+IPD,<len>:" and,
        // after AT+CIPMUX=1, "+IPD,<link>,<len>:". Anything else is text.
        // A payload is counted, never inspected, so a "+IPD," inside binary
        // data cannot start a header.
        private void TrackIpd(byte[] buf, int n)
        {
            for (int i = 0; i < n; i++)
            {
                byte b = buf[i];
                if (ipdState == 6)
                {
                    if (--ipdLeft <= 0)
                    {
                        gaps.Enqueue(rxWritten + i + 1);
                        ipdState = 0;
                    }
                    continue;
                }
                if (ipdState == 5)
                {
                    ipdHeaderLen++;
                    if (b >= (byte)'0' && b <= (byte)'9' && ipdHeaderLen <= IpdHeaderMax)
                    {
                        ipdField = ipdField * 10 + (b - (byte)'0');
                        continue;
                    }
                    if (b == (byte)',' && ipdHeaderLen <= IpdHeaderMax)
                    {
                        ipdField = 0;      // that was the link id
                        continue;
                    }
                    if (b == (byte)':' && ipdField > 0)
                    {
                        ipdLeft = ipdField;
                        ipdState = 6;
                        continue;
                    }
                    ipdState = 0;          // not a header after all
                }
                if (b == (byte)IpdPrefix[ipdState])
                {
                    if (++ipdState == 5)
                    {
                        ipdField = 0;
                        ipdHeaderLen = 0;
                    }
                }
                else
                {
                    ipdState = b == (byte)'+' ? 1 : 0;
                }
            }
        }

        private void DropLink()
        {
            Socket s = link;
            link = null;
            CloseQuietly(s);
            wake.Set();
        }

        private static void CloseQuietly(Socket s)
        {
            if (s == null)
                return;
            try
            {
                s.Shutdown(SocketShutdown.Both);
            }
            catch (Exception)
            {
            }
            try
            {
                s.Close();
            }
            catch (Exception)
            {
            }
        }

        private void ClearRx()
        {
            lock (rxLock)
            {
                // The +IPD tracker is NOT reset: it follows the stream, and
                // the rest of a payload cut off here is still on its way.
                rxHead = 0;
                rxCount = 0;
                rxRead = rxWritten;
                gaps.Clear();
                gapPolls = 0;
            }
        }

        private void ClearTx()
        {
            lock (txLock)
            {
                txLen = 0;
            }
        }

        // -------------------------------------------------- the UART's ports

        public bool Write(eAccess _type, int _port, int _id, byte _value)
        {
            try
            {
                if (_type == eAccess.Port_Write)
                {
                    switch (_port)
                    {
                        case PORT_UART_TX:
                            if (!espSelected)
                                return false;
                            statusStreak = 0;
                            SendByte(_value);
                            return true;
                        case PORT_UART_SELECT:
                            bool esp = (_value & 0x40) == 0;
                            if (esp != espSelected)
                            {
                                espSelected = esp;
                                if (verbose)
                                    Say("UART select: " + (esp ? "ESP" : "Pi (left to CSpect)"));
                            }
                            return false;
                        case PORT_UART_FRAME:
                            if (espSelected && (_value & 0x80) != 0)
                            {
                                // "Reset the Tx and Rx modules, empty the
                                // FIFOs". What was already written is sent
                                // rather than lost: on hardware it would
                                // have left the transmitter long ago.
                                FlushTx();
                                ClearRx();
                                if (verbose)
                                    Say("UART FIFOs reset by the Next");
                            }
                            return false;
                    }
                }
                else if (_type == eAccess.NextReg_Write && _port == REG_RESET)
                {
                    bool hold = (_value & 0x80) != 0;
                    if (hold && !heldInReset)
                    {
                        heldInReset = true;
                        DropLink();
                        if (verbose)
                            Say("the Next holds the ESP in reset");
                    }
                    else if (!hold && heldInReset)
                    {
                        heldInReset = false;
                        wake.Set();
                        if (verbose)
                            Say("the Next released the ESP reset - a fresh ESP session starts");
                    }
                    return false;
                }
            }
            catch (Exception ex)
            {
                Say("write failed: " + ex.Message);
            }
            return false;
        }

        public byte Read(eAccess _type, int _port, int _id, out bool _isvalid)
        {
            try
            {
                if (_type == eAccess.Port_Read && espSelected)
                {
                    if (_port == PORT_UART_TX)
                    {
                        _isvalid = true;
                        // Two status reads with no byte written between them
                        // is software WAITING (for a reply, for room): what
                        // it wrote must be on the wire by now. A transmit
                        // loop reads the status once per byte and so keeps
                        // batching.
                        if (txLen > 0 && ++statusStreak >= 2)
                            FlushTx();
                        byte status = 0;
                        if (gapPolls > 0)
                            gapPolls = gapPolls - 1;   // the wire's pause after a +IPD block
                        else if (rxCount > 0)
                            status |= STATUS_RX_AVAILABLE;
                        int waiting = txLen;
                        if (waiting >= TxBusyAt)
                            status |= STATUS_TX_FULL;
                        else if (waiting == 0)
                            status |= STATUS_TX_EMPTY;
                        return status;
                    }
                    if (_port == PORT_UART_RX)
                    {
                        _isvalid = true;
                        if (txLen > 0)
                            FlushTx();
                        if (rxCount == 0)
                            return 0;
                        lock (rxLock)
                        {
                            if (rxCount == 0)
                                return 0;
                            byte b = rx[rxHead];
                            rxHead = (rxHead + 1) % RxCapacity;
                            rxCount = rxCount - 1;
                            rxRead++;
                            // Software that reads without asking is past
                            // any pause; software that asks gets one when
                            // this byte closed a +IPD block.
                            int pause = 0;
                            while (gaps.Count > 0 && gaps.Peek() <= rxRead)
                            {
                                gaps.Dequeue();
                                pause = GapStatusReads;
                            }
                            gapPolls = pause;
                            return b;
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                Say("read failed: " + ex.Message);
            }
            _isvalid = false;
            return 0xFF;
        }

        private void SendByte(byte value)
        {
            bool flush;
            lock (txLock)
            {
                if (link == null)
                {
                    // No module on the wire: the byte goes nowhere, as on a
                    // Next without an ESP.
                    txLen = 0;
                    return;
                }
                if (txLen >= TxCapacity)
                    return;
                tx[txLen] = value;
                txLen = txLen + 1;
                flush = value == 0x0A || txLen >= TxFlushAt;
            }
            if (flush)
                FlushTx();
        }

        // Hand the socket whatever the Z80 has written. Non-blocking: what
        // the socket will not take stays queued for the next call (the frame
        // tick at the latest), so the emulation thread never waits here.
        private void FlushTx()
        {
            lock (txLock)
            {
                if (txLen == 0)
                    return;
                Socket s = link;
                if (s == null)
                {
                    txLen = 0;
                    return;
                }
                try
                {
                    SocketError err;
                    int sent = s.Send(tx, 0, txLen, SocketFlags.None, out err);
                    if (err == SocketError.Success || err == SocketError.WouldBlock)
                    {
                        if (sent >= txLen)
                        {
                            txLen = 0;
                        }
                        else if (sent > 0)
                        {
                            Buffer.BlockCopy(tx, sent, tx, 0, txLen - sent);
                            txLen = txLen - sent;
                        }
                        return;
                    }
                }
                catch (Exception)
                {
                }
                // The link is gone: the link thread notices, clears up and
                // connects again.
                txLen = 0;
                link = null;
                CloseQuietly(s);
                wake.Set();
            }
        }

        // ------------------------------------------------- the other callbacks

        public void Tick()
        {
            // Once per emulated frame: the safety net for a byte written by
            // software that never looks at the status port afterwards.
            if (txLen > 0)
                FlushTx();
        }

        public void OSTick()
        {
        }

        public bool KeyPressed(int _id)
        {
            return false;
        }

        public void Reset()
        {
            // A machine reset empties the UART's FIFOs. It does NOT reset the
            // ESP - on hardware the module keeps its connections across one -
            // so the link stays as it is.
            ClearTx();
            ClearRx();
        }

        public void Quit()
        {
            running = false;
            DropLink();
        }

        private static void Say(string text)
        {
            try
            {
                Console.WriteLine(Tag + text);
            }
            catch (Exception)
            {
            }
        }
    }
}
