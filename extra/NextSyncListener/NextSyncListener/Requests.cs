using System;
using System.Threading;
using System.Threading.Tasks;

namespace NextSync.Listener
{
    /// <summary>The queued command kinds, one per wire op (plus the tree delete, which is a walk of several).</summary>
    internal enum RequestOp
    {
        List,
        Get,
        Put,
        MakeDirectory,
        RemoveDirectory,
        DeleteFile,
        Rename,
        Drives,
        FreeSpace,
        Ident,
        Crc,
        Release,
        Quit,
        Copy,
        Size,
        DeleteTree
    }

    /// <summary>
    /// One queued command: its parameters and the Task its caller awaits. The
    /// session pops these at the Next's Polls and completes them; completion
    /// runs the awaiting continuations on the thread pool so a caller can
    /// never block the session thread (the net45-compatible stand-in for
    /// TaskCreationOptions.RunContinuationsAsynchronously, which is .NET 4.6+).
    /// </summary>
    internal abstract class Request
    {
        public RequestOp Op;
        public string Path;          // the (first) remote path
        public string Path2;         // rename's new path, copy's destination
        public byte[] Data;          // put: the bytes (null = read LocalPath at serve time)
        public string LocalPath;     // put: the local file; get: the local directory (null = in memory)
        public bool Flag;            // quit: exit the application too
        public int SessionId;        // the Next it was queued for (0 = none connected at the time)
        public bool InFlight;        // popped by the session; no longer cancellable
        public bool Completed;
        public CancellationTokenRegistration Registration;

        public abstract void Complete(NextSyncResult result);

        public abstract void Fail(NextSyncStatus status, string error);

        public abstract NextSyncResult NewResult();
    }

    internal sealed class Request<T> : Request where T : NextSyncResult, new()
    {
        public readonly TaskCompletionSource<T> Source = new TaskCompletionSource<T>();

        public Task<T> Task
        {
            get { return Source.Task; }
        }

        public override NextSyncResult NewResult()
        {
            return new T();
        }

        public override void Complete(NextSyncResult result)
        {
            if (Completed) return;
            Completed = true;
            try { Registration.Dispose(); } catch (Exception) { }
            var typed = (T)result;
            ThreadPool.QueueUserWorkItem(delegate { Source.TrySetResult(typed); });
        }

        public override void Fail(NextSyncStatus status, string error)
        {
            if (Completed) return;
            var r = new T { Status = status, Error = error };
            FillPath(r);
            Complete(r);
        }

        private void FillPath(NextSyncResult r)
        {
            // Keep the path-bearing results self-describing on the failure path
            // too, so a caller logging a failed result sees what it was about.
            var listing = r as ListingResult;
            if (listing != null) { listing.Path = Path; return; }
            var get = r as GetResult;
            if (get != null) { get.RemotePath = Path; return; }
            var put = r as PutResult;
            if (put != null) { put.RemotePath = Path; put.BytesTotal = Data != null ? Data.Length : 0; return; }
            var st = r as StatusResult;
            if (st != null) { st.Path = Path; st.Operation = OperationName(Op); return; }
            var crc = r as CrcResult;
            if (crc != null) { crc.Path = Path; return; }
            var free = r as FreeSpaceResult;
            if (free != null) { free.Drive = Path; return; }
            var copy = r as CopyResult;
            if (copy != null) { copy.Source = Path; copy.Destination = Path2; return; }
            var size = r as SizeResult;
            if (size != null) { size.Path = Path; return; }
            var tree = r as DeleteTreeResult;
            if (tree != null) { tree.Root = Path; return; }
            var quit = r as QuitResult;
            if (quit != null) { quit.ExitApplication = Flag; }
        }

        internal static string OperationName(RequestOp op)
        {
            switch (op)
            {
                case RequestOp.MakeDirectory: return "mkdir";
                case RequestOp.RemoveDirectory: return "rmdir";
                case RequestOp.DeleteFile: return "rm";
                case RequestOp.Rename: return "rename";
                case RequestOp.Release: return "release";
                default: return op.ToString().ToLowerInvariant();
            }
        }
    }
}
