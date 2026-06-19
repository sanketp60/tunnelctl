#!/usr/bin/python3
"""Minimal MongoDB liveness probe through a local tunnel port.
Sends an OP_MSG {hello:1} and waits for a BSON reply.
Exit 0 = healthy (server responded), 1 = unhealthy (timeout / no valid reply).
Usage: mongo_ping.py <port> [timeout_seconds]
"""
import socket, struct, sys

def cstr(s): return s.encode() + b"\x00"

def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 27027
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    # BSON for {"hello": 1, "$db": "admin"}
    body = (b"\x10" + cstr("hello") + struct.pack("<i", 1)
            + b"\x02" + cstr("$db") + struct.pack("<i", 6) + cstr("admin")
            + b"\x00")
    doc = struct.pack("<i", len(body) + 4) + body
    msg = struct.pack("<I", 0) + b"\x00" + doc            # flagBits + section
    packet = struct.pack("<iiii", 16 + len(msg), 1, 0, 2013) + msg  # 2013 = OP_MSG
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        s.settimeout(timeout)
        s.sendall(packet)
        data = s.recv(256)
        s.close()
        if data and len(data) >= 16 and b"setName" in data or (data and b"ok" in data):
            return 0
        # Any well-formed-looking reply (>=16 byte header) counts as alive
        return 0 if data and len(data) >= 16 else 1
    except Exception as e:
        sys.stderr.write(f"mongo probe failed on :{port}: {e}\n")
        return 1

if __name__ == "__main__":
    sys.exit(main())
