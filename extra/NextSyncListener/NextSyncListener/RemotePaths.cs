using System;
using System.Collections.Generic;
using System.IO;

namespace NextSync.Listener
{
    /// <summary>
    /// Where a received file lands locally. Ported from
    /// <c>_re_sanitize_incoming_path</c> / <c>_re_relname_under</c> in
    /// ZX-Next-Unite's <c>zxnu_workers.py</c>.
    /// </summary>
    public static class RemotePaths
    {
        /// <summary>
        /// Map a file name reported by the Next to a safe path under <paramref name="root"/>:
        /// strips any drive letter and leading slashes, drops <c>.</c>/<c>..</c>
        /// segments, and guarantees the result stays inside the root.
        /// </summary>
        public static string SanitizeIncomingPath(string root, string name)
        {
            if (root == null) throw new ArgumentNullException("root");
            name = (name ?? "").Replace('\\', '/');
            if (name.Length >= 2 && name[1] == ':')
                name = name.Substring(2);
            name = name.TrimStart('/');
            var parts = new List<string>();
            foreach (var p in name.Split('/'))
            {
                if (p.Length == 0 || p == "." || p == "..") continue;
                parts.Add(p);
            }
            string rel = parts.Count > 0 ? string.Join(Path.DirectorySeparatorChar.ToString(), parts.ToArray()) : "received.bin";
            string dest = Path.GetFullPath(Path.Combine(root, rel));
            string rootAbs = Path.GetFullPath(root);
            string rootPrefix = rootAbs.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
            bool inside = string.Equals(dest, rootAbs, PathComparison)
                          || dest.StartsWith(rootPrefix, PathComparison);
            if (!inside)
            {
                string leaf = Path.GetFileName(rel);
                dest = Path.Combine(root, leaf.Length > 0 ? leaf : "received.bin");
            }
            return dest;
        }

        /// <summary>
        /// The Next-reported <paramref name="name"/> relative to the fetched
        /// <paramref name="remote"/> item: a directory get streams every file
        /// with its full Next path (fetching <c>/games/lev</c> yields
        /// <c>/games/lev/boot.tap</c>); stripping the PARENT of the fetched item
        /// (<c>/games</c>) leaves <c>lev/boot.tap</c>, so the download recreates
        /// the fetched folder on its own. A single-file get reduces to the file
        /// name. Names with no common parent are returned unchanged.
        /// </summary>
        public static string RelativeNameUnder(string remote, string name)
        {
            name = StripDrive(name ?? "").TrimStart('/');
            string r = StripDrive(remote ?? "").TrimEnd('/');
            int slash = r.LastIndexOf('/');
            string parent = slash > 0 ? r.Substring(0, slash).TrimStart('/') : "";
            if (parent.Length > 0 && (name == parent || name.StartsWith(parent + "/", StringComparison.Ordinal)))
                return name.Substring(parent.Length).TrimStart('/');
            return name;
        }

        private static string StripDrive(string s)
        {
            s = s.Replace('\\', '/');
            if (s.Length >= 2 && s[1] == ':')
                s = s.Substring(2);
            return s;
        }

        private static readonly StringComparison PathComparison =
            Environment.OSVersion.Platform == PlatformID.Win32NT
                ? StringComparison.OrdinalIgnoreCase
                : StringComparison.Ordinal;

        /// <summary>The last path component of a Next path (<c>/games/a.tap</c> -> <c>a.tap</c>).</summary>
        public static string BaseName(string remotePath)
        {
            string s = (remotePath ?? "").Replace('\\', '/').TrimEnd('/');
            int slash = s.LastIndexOf('/');
            return slash >= 0 ? s.Substring(slash + 1) : s;
        }
    }
}
