#!/usr/bin/env python3

# Part of Jari Komppa's zx spectrum next suite
# https://github.com/jarikomppa/specnext
# released under the unlicense, see http://unlicense.org
# (practically public domain)
#
# nextsync4.py - NextSync server with the "Sync4" protocol backported from
# the ZX-Next-Unite application. In addition to the original "Sync3" behaviour
# (PC -> Next: the Next pulls files with ".sync"), this server understands the
# bidirectional "Sync4" handshake so the Next can ALSO push files and whole
# directories *back* to the PC with ".sync4 -send <file|dir>".
#
# Legacy "Sync3" dots keep working unchanged (PC -> Next only).

import random

import datetime
import fnmatch
import socket
import struct
import time
import glob
import sys
import os

assert sys.version_info >= (3, 6) # We need 3.6 for f"" strings.

PORT = 2048    # Port to listen on (non-privileged ports are > 1023)
VERSION3 = "NextSync3"
VERSION4 = "NextSync4"
VERSION = "NextSync4"
IGNOREFILE = "syncignore.txt"
SYNCPOINT = "syncpoint.dat"
MAX_PAYLOAD = 1024

# If you want to be really safe (but transfer slower), use this:
#MAX_PAYLOAD = 256

# The next uart has a buffer of 512 bytes; sending packets of 256 bytes will always
# fit and there won't be any buffer overruns. However, it's much slower.

opt_drive = '/'
opt_always_sync = False
opt_sync_once = False
# How to treat an incoming (-send) file/dir that already exists locally:
#   "prompt"    - ask at the console (default)
#   "overwrite" - always overwrite
#   "ignore"    - always skip
opt_conflict = "prompt"

def update_syncpoint(knownfiles):
    with open(SYNCPOINT, 'w') as f:
        for x in knownfiles:
            f.write(f"{x}\n")

def agecheck(f):
    if not os.path.isfile(SYNCPOINT):
        return False
    ptime = os.path.getmtime(SYNCPOINT)
    mtime = os.path.getmtime(f)
    if mtime > ptime:
        return False
    return True

def getFileList():
    knownfiles = []
    if os.path.isfile(SYNCPOINT):
        with open(SYNCPOINT) as f:
            knownfiles = f.read().splitlines()
    ignorelist = []
    if os.path.isfile(IGNOREFILE):
        with open(IGNOREFILE) as f:
            ignorelist = f.read().splitlines()
    r = []
    gf = glob.glob("**", recursive=True)
    for g in gf:
        if os.path.isfile(g) and os.path.exists(g):
            ignored = False
            for i in ignorelist:
                if fnmatch.fnmatch(g, i):
                    ignored = True
            if not opt_always_sync:
                if g in knownfiles:
                    if agecheck(g):
                        ignored = True
            if not ignored:
                stats = os.stat(g)
                r.append([g, stats.st_size])
    return r

def timestamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def sendpacket(conn, payload, packetno):
    checksum0 = 0 # random.choice([0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1]) # 5%
    checksum1 = 0
    # packetno -= random.choice([0]*99+[1]) # 1%
    for x in payload:
        checksum0 = (checksum0 ^ x) & 0xff
        checksum1 = (checksum1 + checksum0) & 0xff
    packet = ((len(payload)+5).to_bytes(2, byteorder="big")
        + payload
        + (checksum0 & 0xff).to_bytes(1, byteorder="big")
        + (checksum1 & 0xff).to_bytes(1, byteorder="big")
        + (packetno & 0xff).to_bytes(1, byteorder="big"))
    conn.sendall(packet)
    print(f'{timestamp()} | Packet sent: {len(packet)} bytes, payload: {len(payload)} bytes, checksums: {checksum0}, {checksum1}, packetno: {packetno & 0xff}')

# ---- Sync4 upload (Next -> PC) helpers ----------------------------------
# The Next frames each uploaded block exactly like sendpacket():
#   [2 bytes big-endian total][payload][checksum0][checksum1][packetno]
# where total = len(payload) + 5. recv_block() reverses that and verifies
# the checksums. The payload's first byte is an opcode:
#   'N' new file : 'N' + [4B filelen][1B namelen][name]
#   'D' data     : 'D' + raw file bytes
#   'E' end file : 'E'
#   'B' bye      : 'B'
# We reply with a framed "Ok" (accept) or "Resend"/"Err ..." (retry/abort).

def recv_exact(conn, n):
    """Read exactly n bytes from conn, or None on disconnect."""
    buf = b''
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf

def recv_block(conn):
    """Read one framed upload block.

    Returns (payload_bytes, packetno) on success, the string 'BADCS' when the
    frame's checksum is wrong (caller should ask for a resend), or None on
    disconnect / malformed length.
    """
    hdr = recv_exact(conn, 2)
    if hdr is None:
        return None
    total = (hdr[0] << 8) | hdr[1]
    if total < 5 or total > 4096:
        return None
    rest = recv_exact(conn, total - 2)
    if rest is None:
        return None
    payload = rest[:-3]
    cs0, cs1, pktno = rest[-3], rest[-2], rest[-1]
    c0 = 0
    c1 = 0
    for x in payload:
        c0 = (c0 ^ x) & 0xff
        c1 = (c1 + c0) & 0xff
    if c0 != cs0 or c1 != cs1:
        return 'BADCS'
    return (payload, pktno)

def sanitize_incoming_path(root, name):
    """Map a filename reported by the Next to a safe path under root.

    Strips any drive letter and leading slashes, drops '.'/'..' segments, and
    guarantees the result stays inside root.
    """
    name = name.replace('\\', '/')
    if len(name) >= 2 and name[1] == ':':
        name = name[2:]
    name = name.lstrip('/')
    parts = [p for p in name.split('/') if p not in ('', '.', '..')]
    rel = os.path.join(*parts) if parts else 'received.bin'
    dest = os.path.normpath(os.path.join(root, rel))
    root_abs = os.path.abspath(root)
    if not (os.path.abspath(dest) == root_abs or
            os.path.abspath(dest).startswith(root_abs + os.sep)):
        dest = os.path.join(root, os.path.basename(rel) or 'received.bin')
    return dest

def ask_conflict(path):
    """Console prompt: how to handle an incoming file/dir that already exists.

    Returns one of 'overwrite', 'overwrite_all', 'ignore', 'ignore_all'.
    """
    print(f"{timestamp()} | File or directory already exists locally:")
    print(f"             {path}")
    print("    [o] Overwrite (one time)")
    print("    [O] Overwrite (always for the rest of this sync)")
    print("    [i] Ignore    (one time)   [default]")
    print("    [I] Ignore    (always for the rest of this sync)")
    try:
        choice = input("    Choice [o/O/i/I]: ").strip()
    except (EOFError, KeyboardInterrupt):
        choice = ""
    if choice == 'o':
        return 'overwrite'
    if choice == 'O':
        return 'overwrite_all'
    if choice == 'I':
        return 'ignore_all'
    return 'ignore'

def receive_files(conn, stats):
    """Handle a Sync4 'Send' upload: the Next pushes files/dirs to us.

    Inbound blocks are length-framed, so we read them with recv_block() here
    rather than the plain conn.recv(1024) used by the main command loop.
    Returns when the Next sends 'B' (bye) or the connection drops.
    """
    print(f'{timestamp()} | Receiving files from the Next...')
    sendpacket(conn, b"Send", 0)   # ack -> Next starts sending
    stats['packets'] += 1

    upload_root = "./"
    print(f'{timestamp()} | Saving incoming files under: {os.path.abspath(upload_root)}')

    conflict_policy = opt_conflict if opt_conflict in ("prompt", "overwrite", "ignore") else "prompt"
    print(f"{timestamp()} | Existing-file policy: {conflict_policy}")

    expected_pkt = 0
    cur_file = None
    cur_name = None
    cur_path = None
    cur_bytes = 0
    cur_skip = False
    files_received = 0
    # Paths of files fully received this session - added to the syncpoint
    # afterwards so the next PC->Next sync won't push them straight back.
    received_paths = []

    while True:
        blk = recv_block(conn)
        if blk is None:
            print(f'{timestamp()} | Upload connection closed')
            break
        if blk == 'BADCS':
            print(f'{timestamp()} | Bad checksum, requesting resend')
            sendpacket(conn, b"Resend", expected_pkt)
            stats['retries'] += 1
            continue
        payload, pktno = blk
        # Duplicate of last block (our ack was lost): re-ack only.
        if pktno == ((expected_pkt - 1) & 0xff):
            sendpacket(conn, b"Ok", pktno)
            continue
        if pktno != expected_pkt:
            print(f'{timestamp()} | Packet sequence error (got {pktno}, expected {expected_pkt})')
            sendpacket(conn, b"Err seq", pktno)
            break

        op = payload[0:1]
        if op == b'N':
            # 'N' + [4B filelen][1B namelen][name]
            namelen = payload[5] if len(payload) > 5 else 0
            cur_name = payload[6:6 + namelen].decode(errors='replace')
            cur_path = sanitize_incoming_path(upload_root, cur_name)
            # Close any still-open previous file first.
            if cur_file is not None:
                cur_file.close()
                cur_file = None
            cur_bytes = 0
            cur_skip = False
            # Conflict handling when the target already exists.
            if os.path.exists(cur_path):
                decision = conflict_policy
                if decision == 'prompt':
                    choice = ask_conflict(cur_path)
                    if choice == 'overwrite_all':
                        conflict_policy = 'overwrite'   # apply to rest of this sync
                        decision = 'overwrite'
                    elif choice == 'ignore_all':
                        conflict_policy = 'ignore'      # apply to rest of this sync
                        decision = 'ignore'
                    elif choice == 'overwrite':
                        decision = 'overwrite'
                    else:
                        decision = 'ignore'
                if decision == 'ignore':
                    cur_skip = True
            if cur_skip:
                # Don't create/truncate the local file: incoming data blocks are
                # still acked but discarded (cur_file is None), and this file is
                # not counted/recorded.
                print(f'{timestamp()} | Skipped (already exists): {cur_path}')
                sendpacket(conn, b"Ok", pktno)
            else:
                try:
                    parent = os.path.dirname(cur_path)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                except OSError:
                    pass
                try:
                    cur_file = open(cur_path, 'wb')
                except OSError as ex:
                    print(f'{timestamp()} | Cannot create {cur_path}: {ex}')
                    cur_file = None
                    sendpacket(conn, b"Err open", pktno)
                    break
                print(f'{timestamp()} | Receiving: {cur_name} -> {cur_path}')
                sendpacket(conn, b"Ok", pktno)
        elif op == b'D':
            if cur_file is not None:
                cur_file.write(payload[1:])
                cur_bytes += len(payload) - 1
            stats['payloadbytes'] += len(payload) - 1
            stats['totalbytes'] += len(payload)
            sendpacket(conn, b"Ok", pktno)
        elif op == b'E':
            if cur_file is not None:
                cur_file.close()
                cur_file = None
                files_received += 1
                if cur_path and cur_path not in received_paths:
                    received_paths.append(cur_path)
                print(f'{timestamp()} | Received {cur_name} ({cur_bytes} bytes)')
            sendpacket(conn, b"Ok", pktno)
        elif op == b'B':
            # Ack the bye with "Ok" (not "Later"): the Next's generic send_block()
            # only treats a reply as success when it starts with 'O'. Replying
            # "Later" makes the Next consider the bye failed and retry it ~12x;
            # since we close the connection right after, each retry hits its full
            # timeout - a long stall before the dot prints "All done". "Ok" lets
            # it finish on the first try.
            sendpacket(conn, b"Ok", pktno)
            print(f'{timestamp()} | Upload finished, {files_received} file(s) received')
            # If that single ack is lost/corrupted in transit (more likely after
            # a long directory send), the Next retransmits the bye and would burn
            # its full UART timeout against a closed socket. Linger briefly:
            # re-ack any retransmitted bye and stop as soon as the Next hangs up
            # (clean case) or the short grace period elapses.
            try:
                conn.settimeout(2.0)
                while True:
                    extra = recv_block(conn)
                    if extra is None:
                        break   # Next closed its side - done
                    if extra == 'BADCS':
                        continue
                    xpayload, xpktno = extra
                    if xpayload[0:1] == b'B':
                        sendpacket(conn, b"Ok", xpktno)
                    else:
                        break
            except (socket.timeout, OSError):
                pass
            finally:
                try:
                    conn.settimeout(None)
                except OSError:
                    pass
            break
        else:
            sendpacket(conn, b"Err op", pktno)
            break
        expected_pkt = (expected_pkt + 1) & 0xff

    if cur_file is not None:
        cur_file.close()
        cur_file = None

    # Record received files in the syncpoint so the next PC->Next sync treats
    # them as already known and skips them (matching the glob path form
    # getFileList uses).
    if received_paths:
        sp_known = []
        if os.path.isfile(SYNCPOINT):
            with open(SYNCPOINT) as spf:
                sp_known = spf.read().splitlines()
        for rp in received_paths:
            # Store paths in the same relative form glob produces.
            relp = os.path.relpath(rp, ".").replace('\\', '/')
            if relp not in sp_known:
                sp_known.append(relp)
        update_syncpoint(sp_known)
        print(f'{timestamp()} | Sync point updated with {len(received_paths)} received file(s)')

def warnings():
    print()
    print(f"Note: Using {os.getcwd()} as sync root")
    if not os.path.isfile(IGNOREFILE):
        print(f"Warning! Ignore file {IGNOREFILE} not found in directory. All files will be synced, possibly including this file.")
    if not os.path.isfile(SYNCPOINT):
        print(f"Note: Sync point file {SYNCPOINT} not found, syncing all files regardless of timestamp.")
    initial = getFileList()
    total = 0
    for x in initial:
        total += x[1]
    severity = ""
    if len(initial) < 10 and total < 100000:
        severity ="Note"
    elif len(initial) < 100 and total < 1000000:
        severity = "Warning"
    else:
        severity = "WARNING"
    print(f"{severity}: Ready to sync {len(initial)} files, {total/1024:.2f} kilobytes.")
    print()

def main():
    print(f"NextSync server, protocol version {VERSION}")
    print("by Julien Clauzel 2026 and Jari Komppa 2020")
    print("Sync4 (bidirectional) backport")
    print()
    hostinfo = socket.gethostbyname_ex(socket.gethostname())
    print(f"Running on host:\n    {hostinfo[0]}")
    if hostinfo[1] != []:
        print("Aliases:")
        for x in hostinfo[1]:
            print(f"    {x}")
    if hostinfo[2] != []:
        print("IP addresses:")
        for x in hostinfo[2]:
            print(f"    {x}")

    # If we're unsure of the ip, try getting it via internet connection
    if len(hostinfo[2]) > 1 or "127" in hostinfo[2][0]:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80)) # ping google dns
            print(f"Primary IP:\n    {s.getsockname()[0]}")


    warnings()

    working = True
    while working:
        print(f"{timestamp()} | NextSync listening to port {PORT}")
        print(f"{timestamp()} | Now run one of these commands on your Next:")
        print(f"{timestamp()} |   PC  -> Next : .sync4   (or .syncfast)")
        print(f"{timestamp()} |   Next -> PC  : .sync4 -send <file or directory>")
        print(f"{timestamp()} | .sync4  now supports -slow -default -fast additional command option to specify network speed transfer rate")
        # Stats for this connection. Held in a dict so receive_files() can
        # update the upload counters in place.
        stats = {
            'totalbytes': 0,
            'payloadbytes': 0,
            'retries': 0,
            'packets': 0,
            'restarts': 0,
            'gee': 0,
        }
        starttime = 0
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", PORT))
            s.listen()
            conn, addr = s.accept()
            # Make sure *nixes close the socket when we ask it to.
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
            f = getFileList()
            print(f'{timestamp()} | Sync file list has {len(f)} files.')
            knownfiles = []
            if os.path.isfile(SYNCPOINT):
                with open(SYNCPOINT) as kf:
                    knownfiles = kf.read().splitlines()
            fn = 0
            filedata = b''
            packet = b''
            fileofs = 0
            packetno = 0
            starttime = time.time()
            endtime = starttime
            with conn:
                print(f'{timestamp()} | Connected by {addr[0]} port {addr[1]}')
                talking = True
                while talking:
                    data = conn.recv(1024)
                    if not data:
                        break
                    decoded = data.decode(errors='replace')
                    print(f'{timestamp()} | Data received: "{decoded}", {len(decoded)} bytes')
                    if data == b"Sync3":
                        print(f'{timestamp()} | Using protocol version: {VERSION3}')
                        packet = str.encode(VERSION3)
                        sendpacket(conn, packet, 0)
                        stats['packets'] += 1
                        stats['totalbytes'] += len(packet)
                    elif data == b"Sync4":
                        # Bidirectional protocol negotiation (Sync4). Only then
                        # will the Next be allowed to push files to us.
                        print(f'{timestamp()} | Using protocol version: {VERSION4}')
                        packet = str.encode(VERSION4)
                        sendpacket(conn, packet, 0)
                        stats['packets'] += 1
                        stats['totalbytes'] += len(packet)
                    elif data == b"Send":
                        # Sync4 upload mode: the Next pushes files to us. We frame
                        # inbound blocks ourselves in receive_files() (the main
                        # recv(1024) loop can't frame length-prefixed data).
                        receive_files(conn, stats)
                        talking = False
                    elif data == b"Next" or data == b"Neex": # Really common mistransmit. Probably uart-esp..
                        if data == b"Neex":
                            stats['gee'] += 1
                        if fn >= len(f):
                            print(f"{timestamp()} | Nothing (more) to sync")
                            packet = b'\x00\x00\x00\x00\x00' # end of.
                            stats['packets'] += 1
                            sendpacket(conn, packet, 0)
                            stats['totalbytes'] += len(packet)
                            # Sync complete, set sync point
                            update_syncpoint(knownfiles)
                        else:
                            specfn = opt_drive + f[fn][0].replace('\\','/')
                            print(f"{timestamp()} | File:{f[fn][0]} (as {specfn}) length:{f[fn][1]} bytes")
                            packet = (f[fn][1]).to_bytes(4, byteorder="big") + (len(specfn)).to_bytes(1, byteorder="big") + (specfn).encode()
                            stats['packets'] += 1
                            sendpacket(conn, packet, 0)
                            stats['totalbytes'] += len(packet)
                            with open(f[fn][0], 'rb') as srcfile:
                                filedata = srcfile.read()
                            stats['payloadbytes'] += len(filedata)
                            if f[fn][0] not in knownfiles:
                                knownfiles.append(f[fn][0])
                            fileofs = 0
                            packetno = 0
                            fn+=1
                    elif data == b"Get" or data == b"Gee": # Really common mistransmit. Probably uart-esp..
                        bytecount = MAX_PAYLOAD
                        if bytecount + fileofs > len(filedata):
                            bytecount = len(filedata) - fileofs
                        packet = filedata[fileofs:fileofs+bytecount]
                        print(f"{timestamp()} | Sending {bytecount} bytes, offset {fileofs}/{len(filedata)}")
                        stats['packets'] += 1
                        sendpacket(conn, packet, packetno)
                        stats['totalbytes'] += len(packet)
                        fileofs += bytecount
                        packetno += 1
                        if data == b"Gee":
                            stats['gee'] += 1
                    elif data == b"Retry":
                        stats['retries'] += 1
                        print(f"{timestamp()} | Resending")
                        sendpacket(conn, packet, packetno - 1)
                    elif data == b"Restart":
                        stats['restarts'] += 1
                        print(f"{timestamp()} | Restarting")
                        fileofs = 0
                        packetno = 0
                        sendpacket(conn, str.encode("Back"), 0)
                    elif data == b"Bye":
                        sendpacket(conn, str.encode("Later"), 0)
                        print(f"{timestamp()} | Closing connection")
                        talking = False
                    elif data == b"Sync2" or data == b"Sync1" or data == b"Sync":
                        packet = str.encode("Nextsync 0.8 or later needed")
                        print(f'{timestamp()} | Old version requested')
                        sendpacket(conn, packet, 0)
                        stats['packets'] += 1
                        stats['totalbytes'] += len(packet)
                    else:
                        print(f"{timestamp()} | Unknown command")
                        sendpacket(conn, str.encode("Error"), 0)
                endtime = time.time()
        deltatime = endtime - starttime
        if deltatime <= 0:
            deltatime = 0.0001
        print(f"{timestamp()} | {stats['totalbytes']/1024:.2f} kilobytes transferred in {deltatime:.2f} seconds, {(stats['totalbytes']/deltatime)/1024:.2f} kBps")
        print(f"{timestamp()} | {stats['payloadbytes']/1024:.2f} kilobytes payload, {(stats['payloadbytes']/deltatime)/1024:.2f} kBps effective speed")
        print(f"{timestamp()} | packets: {stats['packets']}, retries: {stats['retries']}, restarts: {stats['restarts']}, gee: {stats['gee']}")
        print(f"{timestamp()} | Disconnected")
        print()
        if opt_sync_once:
            working = False


for x in sys.argv[1:]:
    if x == '-c':
        opt_drive = 'c:/'
    elif x == '-d':
        opt_drive = 'd:/'
    elif x == '-e':
        opt_drive = 'e:/'
    elif x == '-a':
        opt_always_sync = True
    elif x == '-o':
        opt_sync_once = True
    elif x == '-s':
        MAX_PAYLOAD = 256
    elif x == '-u':
        MAX_PAYLOAD = 1455
    elif x == '-ow':
        opt_conflict = "overwrite"
    elif x == '-ig':
        opt_conflict = "ignore"
    elif x == '-pr':
        opt_conflict = "prompt"
    else:
        print(f"Unknown parameter: {x}")
        print(
        """
        Run without parameters for normal action. See nextsync.txt for details.

        This is the Sync4 (bidirectional) server. It serves files to the Next
        with ".sync4" (Sync3, unchanged) AND receives files/directories pushed
        from the Next with ".sync -send <file|dir>" (Sync4).

        Optional parameters:
        -a  - Always sync, regardless of timestamps (doesn't skip ignore file)
        -o  - Sync once, then quit. Default is to keep the sync loop running.
        -s  - Use safe payload size (256 bytes). Slower, but more robust.
              Use this if you get a lot of retries.
        -u  - To live on the edge, you can try to use really unsafe payload
              size (1455 bytes). Faster, but more likely to break.
        -c  - Prefix filenames with c: (i.e, /dot/foo becomes c:/dot/foo)
        -d  - Prefix filenames with d: (i.e, /dot/foo becomes d:/dot/foo)
        -e  - Prefix filenames wieh e: (i.e, /dot/foo becomes e:/dot/foo)

        Sync4 receive (.sync4 -send) options - how to handle an incoming file
        or directory that already exists locally:
        -pr - Prompt at the console for each conflict (default)
        -ow - Always overwrite existing files
        -ig - Always ignore (skip) existing files
        """)
        quit()

main()
