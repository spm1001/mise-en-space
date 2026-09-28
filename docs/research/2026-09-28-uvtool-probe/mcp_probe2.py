"""Hold a server open across an engine reinstall: search gmail, wait for a flag file, then a calendar search (lazy-imports adapters.calendar)."""
import json, subprocess, sys, time, os
p = subprocess.Popen(sys.argv[1:], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(os.environ["PROBE_STDERR"], "w"), text=True)
def send(m): p.stdin.write(json.dumps(m) + "\n"); p.stdin.flush()
def recv(i):
    while True:
        line = p.stdout.readline()
        if not line: raise SystemExit(f"server closed stdout (exit {p.poll()})")
        m = json.loads(line)
        if m.get("id") == i: return m
def call(i, args):
    send({"jsonrpc":"2.0","id":i,"method":"tools/call","params":{"name":"search","arguments":args}}); r = recv(i)
    txt = "".join(c.get("text","") for c in r.get("result",{}).get("content",[]))
    try: d = json.loads(txt); return {k: d.get(k) for k in ("gmail_count","calendar_count","errors")} | {"identity": d.get("cues",{}).get("_identity")}
    except Exception: return {"raw": txt[:400]}
send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe2","version":"0"}}}); print("init", recv(1)["result"]["serverInfo"])
send({"jsonrpc":"2.0","method":"notifications/initialized"})
base = "/tmp/uvtool-probe/deposits"
print("before:", call(2, {"query":"in:inbox","sources":["gmail"],"max_results":1,"base_path":base}), flush=True)
open("/tmp/uvtool-probe/probe2.ready","w").close()
while not os.path.exists("/tmp/uvtool-probe/probe2.go"): time.sleep(0.2)
send({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search","arguments":{"type":"pdf","sources":["drive"],"max_results":1,"base_path":base}}}); r=recv(3)
d=json.loads("".join(c.get("text","") for c in r["result"]["content"])); fid=d["preview"]["drive"][0]["id"]; print("pdf:", d["preview"]["drive"][0].get("name"), flush=True)
send({"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"fetch","arguments":{"file_id":fid,"base_path":base,"thumbnails":False,"crops":False}}}); r=recv(4)
txt="".join(c.get("text","") for c in r["result"]["content"]); print("after reinstall, PDF fetch (lazy markitdown import):", txt[:300].replace(chr(10)," "), "| isError", r["result"].get("isError"), flush=True)
print("server alive:", p.poll() is None); p.terminate()
