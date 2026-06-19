#!/usr/bin/python3
"""Config helper for tunnelctl. Single place that parses tunnels.json.

Usage:
  _lib.py names                  -> print tunnel names, one per line
  _lib.py rows                   -> print 'name|local_port|remote_host|remote_port|type|description'
  _lib.py field <name> <field>   -> print one resolved field (with defaults applied)
  _lib.py ssh-args <name>        -> print the gcloud ssh argv, NUL-separated
  _lib.py validate               -> exit 0 if config valid, else print errors and exit 1
"""
import json, os, sys

CONF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tunnels.json")
REQUIRED = ["name", "local_port", "remote_host", "remote_port", "type", "bastion"]
VALID_TYPES = {"pg", "mongo", "tcp"}


def load():
    with open(CONF) as f:
        data = json.load(f)
    defaults = data.get("defaults", {})
    bastions = data.get("bastions", {})
    tunnels = data.get("tunnels", [])
    for t in tunnels:
        t.setdefault("project", defaults.get("project"))
        t.setdefault("ssh_opts", defaults.get("ssh_opts", []))
        t.setdefault("description", t.get("name", ""))
        # Resolve bastion alias -> instance + zone (+ optional project).
        # Falls back to treating 'bastion' as a raw instance name (needs 'zone').
        b = t.get("bastion")
        if b in bastions:
            spec = bastions[b]
            t["_instance"] = spec.get("instance")
            t["zone"] = spec.get("zone")
            if spec.get("project"):
                t["project"] = spec["project"]
        else:
            t["_instance"] = b  # raw instance name (legacy)
    return tunnels


def find(name):
    for t in load():
        if t["name"] == name:
            return t
    sys.stderr.write(f"tunnel '{name}' not found in {CONF}\n")
    sys.exit(2)


def validate():
    errors = []
    try:
        tunnels = load()
    except Exception as e:
        print(f"JSON parse error: {e}")
        return 1
    seen_names, seen_ports = {}, {}
    for i, t in enumerate(tunnels):
        ctx = t.get("name", f"index {i}")
        for fld in REQUIRED:
            if not t.get(fld) and t.get(fld) != 0:
                errors.append(f"[{ctx}] missing required field: {fld}")
        if t.get("type") and t["type"] not in VALID_TYPES:
            errors.append(f"[{ctx}] invalid type '{t['type']}' (use pg|mongo|tcp)")
        # Bastion alias must resolve to an instance + zone.
        if not t.get("_instance"):
            errors.append(f"[{ctx}] bastion '{t.get('bastion')}' has no instance "
                          f"(define it under \"bastions\" or set zone for a raw instance)")
        if not t.get("zone"):
            errors.append(f"[{ctx}] no zone (bastion alias '{t.get('bastion')}' "
                          f"missing zone, or raw instance needs a 'zone' field)")
        n = t.get("name")
        if n in seen_names:
            errors.append(f"duplicate name '{n}'")
        seen_names[n] = True
        p = t.get("local_port")
        if p in seen_ports:
            errors.append(f"duplicate local_port {p} ('{n}' and '{seen_ports[p]}')")
        seen_ports[p] = n
    if errors:
        print("\n".join(errors))
        return 1
    print("config OK")
    return 0


def main():
    if len(sys.argv) < 2:
        sys.stderr.write(__doc__)
        return 1
    cmd = sys.argv[1]
    if cmd == "validate":
        return validate()
    if cmd == "names":
        for t in load():
            print(t["name"])
        return 0
    if cmd == "rows":
        for t in load():
            print("|".join(str(t[k]) for k in
                  ("name", "local_port", "remote_host", "remote_port", "type", "description")))
        return 0
    if cmd == "field":
        t = find(sys.argv[2])
        print(t.get(sys.argv[3], ""))
        return 0
    if cmd == "ssh-args":
        t = find(sys.argv[2])
        args = ["compute", "ssh", "--zone", t["zone"], t["_instance"],
                "--project", t["project"], "--tunnel-through-iap", "--"]
        args += list(t.get("ssh_opts", []))
        args += ["-L", f'{t["local_port"]}:{t["remote_host"]}:{t["remote_port"]}', "-N"]
        # Trailing NUL after EVERY arg so `read -d ''` captures the last one too.
        sys.stdout.write("".join(a + "\0" for a in args))
        return 0
    sys.stderr.write(f"unknown command: {cmd}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
