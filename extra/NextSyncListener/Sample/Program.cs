using System;
using System.Threading;
using System.Threading.Tasks;
using NextSync.Listener;

namespace NextSync.Listener.Sample
{
    /// <summary>
    /// Start the NextSync Listener on port 2048, wait for a Next to run
    /// <c>.sync5 -listen</c> (or ZX Next Remote's Listener) against this PC,
    /// then list its root folder, say who answered, and send the Next home.
    /// <para>
    /// Usage: <c>dotnet run --project Sample [-- port [path-to-list]]</c>
    /// </para>
    /// </summary>
    internal static class Program
    {
        private static async Task<int> Main(string[] args)
        {
            int port = args.Length > 0 ? int.Parse(args[0]) : NextSyncListener.DefaultPort;
            string path = args.Length > 1 ? args[1] : "/";

            using (var listener = new NextSyncListener(port))
            {
                var seated = new TaskCompletionSource<bool>();
                listener.Log += (s, e) => Console.WriteLine("[" + e.Level + "] " + e.Message);
                listener.Connected += (s, e) =>
                {
                    Console.WriteLine("A Next dialed in from " + e.RemoteEndPoint);
                    seated.TrySetResult(true);
                };
                listener.Disconnected += (s, e) => Console.WriteLine("The Next left: " + e.Reason);
                listener.Progress += (s, e) =>
                {
                    if (e.Kind == TransferKind.Put || e.Kind == TransferKind.Get)
                        Console.WriteLine("  " + e.Kind + " " + e.RemotePath + ": " + e.BytesDone + " bytes");
                };

                listener.Start();
                Console.WriteLine("Listening on port " + listener.LocalPort + " - on the Next run: .sync5 -listen");
                Console.WriteLine("(Ctrl-C to stop)");

                await seated.Task;

                // Who is on the line? (dot v5.8+ / ZX Next Remote 1.0.2+ answer;
                // an older listener says NotSupported and the session carries on.)
                var who = await listener.QueryIdentAsync();
                Console.WriteLine(who.Ok ? "Listener: " + who.Ident : "Listener: " + who.Status + " (" + who.Error + ")");

                var drives = await listener.GetDrivesAsync();
                if (drives.Ok)
                    Console.WriteLine("Drives: current " + drives.Current + ", mounted " + string.Join(" ", drives.Mounted));

                var listing = await listener.ListAsync(path);
                if (!listing.Ok)
                {
                    Console.WriteLine("ls " + path + " failed: " + listing.Status + " - " + listing.Error);
                }
                else
                {
                    Console.WriteLine("Listing of " + path + " (" + listing.Entries.Count + " entries):");
                    foreach (var e in listing.Entries)
                        Console.WriteLine(e.IsDirectory ? "   <DIR>        " + e.Name : "   " + e.Size.ToString().PadLeft(10) + "  " + e.Name);
                }

                var free = await listener.GetFreeSpaceAsync();
                if (free.Ok)
                    Console.WriteLine("Free space on the current drive: " + free.FreeBytes + " bytes");

                // Leave the Next in BASIC (a plain 'Q'), then stop.
                var quit = await listener.QuitAsync();
                Console.WriteLine("Quit: " + quit.Status);
                listener.Stop();
            }
            return 0;
        }
    }
}
