#!/usr/bin/env python3
"""Drive Google's Workspace MCP servers over StreamableHTTP with mise's token.

Token is loaded and refreshed in memory; never printed. Output redacts auth.
Usage: gmcp.py handshake | gmcp.py call '<tool>' '<json-args>' [--repeat N]
"""
import json, sys, time, urllib.request, urllib.error, urllib.parse

TOKEN_PATH = '/home/modha/.claude/plugins/data/mise-batterie-de-savoir/token.json'
import os
BASE = os.environ.get('GMCP_BASE', 'https://workspacemcp.googleapis.com/mcp/v1')
OUT_DIR = '/tmp/claude-1000/-home-modha-repos-spm1001-mise-en-space/b682f8f5-a2d4-4911-a296-d67f53b6febf/scratchpad'


def get_access_token():
    tok = json.load(open(TOKEN_PATH))
    data = urllib.parse.urlencode({
        'client_id': tok['client_id'],
        'client_secret': tok['client_secret'],
        'refresh_token': tok['refresh_token'],
        'grant_type': 'refresh_token',
    }).encode()
    req = urllib.request.Request(tok.get('token_uri', 'https://oauth2.googleapis.com/token'), data=data)
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=30))
    except urllib.error.HTTPError as e:
        print('REFRESH FAILED', e.code, e.read().decode()[:600])
        sys.exit(1)
    print(f"token refreshed OK, expires_in={resp.get('expires_in')}s (value redacted)")
    return resp['access_token']


AT = get_access_token()
SESSION = None


def rpc(method, params=None, id=None, timeout=90):
    global SESSION
    body = {'jsonrpc': '2.0', 'method': method}
    if params is not None:
        body['params'] = params
    if id is not None:
        body['id'] = id
    h = {'Authorization': f'Bearer {AT}', 'Content-Type': 'application/json',
         'Accept': 'application/json, text/event-stream'}
    if SESSION:
        h['Mcp-Session-Id'] = SESSION
    req = urllib.request.Request(BASE, data=json.dumps(body).encode(), headers=h)
    t0 = time.monotonic()
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        raw = r.read().decode()
        ct = r.headers.get('content-type', '')
        sid = r.headers.get('mcp-session-id')
        if sid:
            SESSION = sid
    except urllib.error.HTTPError as e:
        dt = time.monotonic() - t0
        print(f'{method}: HTTP {e.code} in {dt:.2f}s')
        print(e.read().decode()[:1200])
        return None, dt
    dt = time.monotonic() - t0
    if 'event-stream' in ct:
        msgs = [json.loads(l[5:]) for l in raw.splitlines() if l.startswith('data:')]
        obj = msgs[-1] if msgs else None
    else:
        obj = json.loads(raw) if raw.strip() else None
    return obj, dt


def handshake():
    obj, dt = rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                 'clientInfo': {'name': 'mise-spike', 'version': '0.1'}}, id=1)
    if obj is None:
        sys.exit(2)
    info = obj.get('result', {})
    print(f"initialize: {dt:.2f}s  server={json.dumps(info.get('serverInfo'))}  proto={info.get('protocolVersion')}")
    rpc('notifications/initialized')
    obj, dt = rpc('tools/list', {}, id=2)
    if obj is None:
        sys.exit(3)
    tools = obj['result']['tools']
    print(f"tools/list: {dt:.2f}s, {len(tools)} tool(s)")
    with open(f'{OUT_DIR}/gmcp-tools.json', 'w') as f:
        json.dump(tools, f, indent=1)
    for t in tools:
        props = list(t.get('inputSchema', {}).get('properties', {}).keys())
        print(f"\n--- {t['name']}  (params: {', '.join(props)})")
        print((t.get('description') or '')[:700])
        print(f"annotations: {json.dumps(t.get('annotations'))}")
    print(f"\nfull schemas -> {OUT_DIR}/gmcp-tools.json")


def call(tool, args, repeat=1):
    rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                       'clientInfo': {'name': 'mise-spike', 'version': '0.1'}}, id=1)
    rpc('notifications/initialized')
    for i in range(repeat):
        obj, dt = rpc('tools/call', {'name': tool, 'arguments': args}, id=10 + i)
        if obj is None:
            continue
        res = obj.get('result', obj.get('error'))
        blob = json.dumps(res)
        tag = f'{OUT_DIR}/gmcp-call-{tool}-{i}.json'
        with open(tag, 'w') as f:
            json.dump(res, f, indent=1)
        print(f'call[{i}] {tool}: {dt:.2f}s, payload {len(blob):,} chars -> {tag}')


if __name__ == '__main__':
    if sys.argv[1] == 'handshake':
        handshake()
    elif sys.argv[1] == 'call':
        call(sys.argv[2], json.loads(sys.argv[3]),
             repeat=int(sys.argv[4]) if len(sys.argv) > 4 else 1)
