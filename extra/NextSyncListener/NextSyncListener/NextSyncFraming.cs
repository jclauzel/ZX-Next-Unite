using System;
using System.Text;

namespace NextSync.Listener
{
    /// <summary>
    /// The byte-level framing every NextSync block uses, in both directions:
    /// <c>[2 bytes big-endian total][payload][checksum0][checksum1][packetno]</c>
    /// with <c>total = payload length + 5</c>. The two checksums are the dot's:
    /// <c>c0 ^= byte; c1 += c0</c> over the payload, each kept to 8 bits.
    /// Ported line by line from <c>_re_checksums</c> / <c>_re_sendpacket</c> /
    /// <c>_re_recv_block</c> in ZX-Next-Unite's <c>zxnu_workers.py</c>.
    /// </summary>
    public static class NextSyncFraming
    {
        /// <summary>Bytes before the payload (the big-endian total).</summary>
        public const int HeaderSize = 2;

        /// <summary>Bytes after the payload (checksum0, checksum1, packetno).</summary>
        public const int TrailerSize = 3;

        /// <summary>The smallest legal <c>total</c>: an empty payload.</summary>
        public const int MinTotal = 5;

        /// <summary>The largest <c>total</c> a reader accepts (the dot's cap).</summary>
        public const int MaxTotal = 4096;

        /// <summary>
        /// The protocol's hard cap on a served data chunk (MAX_PAYLOAD in the
        /// Python servers). <see cref="NextSyncListener.MaxPayload"/> defaults to
        /// 512 instead - see its note.
        /// </summary>
        public const int MaxPayloadCap = 1024;

        /// <summary>Compute the dot's two running checksums over a byte range.</summary>
        public static void Checksums(byte[] data, int offset, int count, out byte checksum0, out byte checksum1)
        {
            if (data == null) throw new ArgumentNullException("data");
            if (offset < 0 || count < 0 || offset + count > data.Length) throw new ArgumentOutOfRangeException("count");
            int c0 = 0, c1 = 0;
            for (int i = 0; i < count; i++)
            {
                c0 = (c0 ^ data[offset + i]) & 0xff;
                c1 = (c1 + c0) & 0xff;
            }
            checksum0 = (byte)c0;
            checksum1 = (byte)c1;
        }

        /// <summary>Frame a payload range with the given packet number.</summary>
        public static byte[] Build(byte[] payload, int offset, int count, byte packetNo)
        {
            if (payload == null) throw new ArgumentNullException("payload");
            if (offset < 0 || count < 0 || offset + count > payload.Length) throw new ArgumentOutOfRangeException("count");
            if (count + MinTotal > MaxTotal) throw new ArgumentOutOfRangeException("count", "payload too long for one frame");
            int total = count + MinTotal;
            var frame = new byte[total];
            frame[0] = (byte)(total >> 8);
            frame[1] = (byte)(total & 0xff);
            Buffer.BlockCopy(payload, offset, frame, HeaderSize, count);
            byte c0, c1;
            Checksums(payload, offset, count, out c0, out c1);
            frame[total - 3] = c0;
            frame[total - 2] = c1;
            frame[total - 1] = packetNo;
            return frame;
        }

        /// <summary>Frame a whole payload with the given packet number.</summary>
        public static byte[] Build(byte[] payload, byte packetNo)
        {
            if (payload == null) throw new ArgumentNullException("payload");
            return Build(payload, 0, payload.Length, packetNo);
        }

        /// <summary>Frame a text payload (UTF-8) with the given packet number.</summary>
        public static byte[] Build(string payload, byte packetNo)
        {
            if (payload == null) throw new ArgumentNullException("payload");
            return Build(Encoding.UTF8.GetBytes(payload), packetNo);
        }

        /// <summary>
        /// Parse one complete frame at the start of <paramref name="data"/>.
        /// Returns false when the bytes do not hold a whole, well-formed frame
        /// (short, or a total outside <see cref="MinTotal"/>..<see cref="MaxTotal"/>).
        /// <paramref name="checksumOk"/> says whether the trailer matched the
        /// payload; trailing bytes after the frame are ignored.
        /// </summary>
        public static bool TryParse(byte[] data, int offset, int available, out byte[] payload, out byte packetNo, out bool checksumOk, out int total)
        {
            payload = null;
            packetNo = 0;
            checksumOk = false;
            total = 0;
            if (data == null || available < MinTotal || offset < 0 || offset + available > data.Length)
                return false;
            total = (data[offset] << 8) | data[offset + 1];
            if (total < MinTotal || total > MaxTotal || total > available)
                return false;
            int count = total - MinTotal;
            payload = new byte[count];
            Buffer.BlockCopy(data, offset + HeaderSize, payload, 0, count);
            byte c0, c1;
            Checksums(payload, 0, count, out c0, out c1);
            checksumOk = data[offset + total - 3] == c0 && data[offset + total - 2] == c1;
            packetNo = data[offset + total - 1];
            return true;
        }
    }

    /// <summary>
    /// ZX Next Remote's "OS protection" refusal (0.9.0): a listener that guards
    /// its OS folders answers a blocked operation with an <c>'F'</c> status
    /// block carrying the three bytes <c>OSP</c> after the <c>'F'</c>. The
    /// .sync5 dot never sends it, so an ordinary Next just fails the way it
    /// always did; this library surfaces it as
    /// <see cref="NextSyncStatus.OsProtected"/> instead of a plain failure,
    /// because the fix is a setting on the far machine, not the network.
    /// </summary>
    public static class OsProtection
    {
        /// <summary>The marker that follows the <c>'F'</c>.</summary>
        public static readonly byte[] Marker = { (byte)'O', (byte)'S', (byte)'P' };

        /// <summary>The explanation ZX-Next-Unite and its HTTP bridge give (RE_OSP_ERROR).</summary>
        public const string ErrorText =
            "os-protected: write access is blocked in the remote operating " +
            "system. If the far side is ZX Next Remote, check its \"OS protection\" " +
            "setting and customise the restricted directory list if appropriate.";

        /// <summary>True when a reply payload (a block minus its framing) is the marked refusal.</summary>
        public static bool IsRefusal(byte[] payload)
        {
            if (payload == null || payload.Length < 1 + Marker.Length || payload[0] != (byte)'F')
                return false;
            for (int i = 0; i < Marker.Length; i++)
                if (payload[1 + i] != Marker[i]) return false;
            return true;
        }
    }

    /// <summary>
    /// CRC-32 (IEEE 802.3, the value <c>zlib.crc32</c> and the Next's <c>'K'</c>
    /// op compute). Callers compare it with <see cref="CrcResult.Crc32Hex"/>.
    /// </summary>
    public static class Crc32
    {
        private static readonly uint[] Table = MakeTable();

        private static uint[] MakeTable()
        {
            var table = new uint[256];
            for (uint n = 0; n < 256; n++)
            {
                uint c = n;
                for (int k = 0; k < 8; k++)
                    c = (c & 1) != 0 ? 0xEDB88320u ^ (c >> 1) : c >> 1;
                table[n] = c;
            }
            return table;
        }

        /// <summary>CRC-32 of a byte range.</summary>
        public static uint Compute(byte[] data, int offset, int count)
        {
            if (data == null) throw new ArgumentNullException("data");
            if (offset < 0 || count < 0 || offset + count > data.Length) throw new ArgumentOutOfRangeException("count");
            uint crc = 0xFFFFFFFFu;
            for (int i = 0; i < count; i++)
                crc = Table[(crc ^ data[offset + i]) & 0xff] ^ (crc >> 8);
            return crc ^ 0xFFFFFFFFu;
        }

        /// <summary>CRC-32 of a whole buffer.</summary>
        public static uint Compute(byte[] data)
        {
            if (data == null) throw new ArgumentNullException("data");
            return Compute(data, 0, data.Length);
        }

        /// <summary>The 8 upper-case hex digits the Next answers <c>'K'</c> with.</summary>
        public static string ToHex(uint crc)
        {
            return crc.ToString("X8");
        }
    }
}
