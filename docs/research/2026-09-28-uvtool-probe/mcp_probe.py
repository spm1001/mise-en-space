"""Minimal MCP stdio client: initialize, list tools, call search. Usage: mcp_probe.py <cmd> [args...]"""
import json, subprocess, sys, time, os
p = subprocess.Popen(sys.argv[1:], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(os.environ.get("PROBE_STDERR", "/dev/null"), "w"), text=True)
def send(msg): p.stdin.write(json.dumps(msg) + "\n"); p.stdin.flush()
def recv(want_id):
    while True:
        line = p.stdout.readline()
        if not line: raise SystemExit(f"server closed stdout (exit {p.poll()})")
        m = json.loads(line)
        if m.get("id") == want_id: return m
t0 = time.time()
send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}})
r = recv(1); print("initialize:", r["result"]["serverInfo"], f"{time.time()-t0:.1f}s")
send({"jsonrpc":"2.0","method":"notifications/initialized"})
send({"jsonrpc":"2.0","id":2,"method":"tools/list"}); r = recv(2)
print("tools:", [t["name"] for t in r["result"]["tools"]])
args = {"query":"in:drafts newer_than:2d","sources":["gmail"],"max_results":1,"base_path":os.environ.get("PROBE_BASE","/tmp/uvtool-probe/deposits")}
send({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search","arguments":args}}); r = recv(3)
res = r.get("result", {}); txt = "".join(c.get("text","") for c in res.get("content", []))
try:
    d = json.loads(txt); print("search: gmail_count", d.get("gmail_count"), "| identity", d.get("cues",{}).get("_identity")); os.environ.get("PROBE_FULL") and print(json.dumps(d, indent=1)[:2500])
except Exception: print("search raw:", txt[:600], "| isError", res.get("isError"))
p.terminate()
