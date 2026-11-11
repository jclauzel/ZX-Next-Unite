using System;
using System.Net;

namespace NextSync.Listener
{
    /// <summary>A Next dialed in with the <c>Listen</c> handshake and was seated.</summary>
    public sealed class NextConnectedEventArgs : EventArgs
    {
        internal NextConnectedEventArgs(IPEndPoint remoteEndPoint)
        {
            RemoteEndPoint = remoteEndPoint;
        }

        /// <summary>The Next's address and port.</summary>
        public IPEndPoint RemoteEndPoint { get; private set; }
    }

    /// <summary>The seated Next's session ended (quit, hang-up, silence, error, or the listener stopping).</summary>
    public sealed class NextDisconnectedEventArgs : EventArgs
    {
        internal NextDisconnectedEventArgs(IPEndPoint remoteEndPoint, string reason)
        {
            RemoteEndPoint = remoteEndPoint;
            Reason = reason;
        }

        public IPEndPoint RemoteEndPoint { get; private set; }

        /// <summary>Why, in the worker's words (English).</summary>
        public string Reason { get; private set; }
    }

    /// <summary>Severity of a <see cref="NextSyncListener.Log"/> line.</summary>
    public enum ListenerLogLevel
    {
        Info,
        Warning,
        Error
    }

    /// <summary>A human-readable line the worker would have printed on the NextSync console.</summary>
    public sealed class ListenerLogEventArgs : EventArgs
    {
        internal ListenerLogEventArgs(ListenerLogLevel level, string message)
        {
            Level = level;
            Message = message;
        }

        public ListenerLogLevel Level { get; private set; }

        public string Message { get; private set; }
    }

    /// <summary>Which long command a progress event belongs to.</summary>
    public enum TransferKind
    {
        /// <summary>A put: the Next is pulling the file.</summary>
        Put,

        /// <summary>A get: the Next is pushing a file.</summary>
        Get,

        /// <summary>A copy on the Next (rcpy): one named block per file started, empty keepalives inside big files.</summary>
        Copy,

        /// <summary>A size walk on the Next (rfsize): one named block per directory entered.</summary>
        Size
    }

    /// <summary>Progress of a put, a get, a copy on the Next or a size walk.</summary>
    public sealed class TransferProgressEventArgs : EventArgs
    {
        internal TransferProgressEventArgs(TransferKind kind, string remotePath, long bytesDone, long bytesTotal, int files, string item)
        {
            Kind = kind;
            RemotePath = remotePath;
            BytesDone = bytesDone;
            BytesTotal = bytesTotal;
            Files = files;
            Item = item ?? "";
        }

        public TransferKind Kind { get; private set; }

        /// <summary>The path the command named.</summary>
        public string RemotePath { get; private set; }

        /// <summary>Bytes moved so far (puts and gets).</summary>
        public long BytesDone { get; private set; }

        /// <summary>The file size for a put; -1 for a get (the wire never says: the dot's length field is always zero).</summary>
        public long BytesTotal { get; private set; }

        /// <summary>Files seen so far (a directory get streams several).</summary>
        public int Files { get; private set; }

        /// <summary>The item the Next just named (the file being copied, the directory being scanned, the file being received), or empty for a keepalive.</summary>
        public string Item { get; private set; }
    }
}
