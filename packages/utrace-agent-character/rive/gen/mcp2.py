"""SSE-tolerant Rive MCP call (the streamable HTTP endpoint answers some long calls as text/event-stream)."""
import json, urllib.request, itertools
URL = "http://127.0.0.1:9791/mcp"; _ids = itertools.count(20000)
def call(name, arguments):
    body = json.dumps({"jsonrpc": "2.0", "id": next(_ids), "method": "tools/call", "params": {"name": name, "arguments": arguments}}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    r = urllib.request.urlopen(req, timeout=300); ct = r.headers.get("content-type", ""); data = r.read().decode()
    if "event-stream" in ct: data = "".join(l[5:].strip() for l in data.splitlines() if l.startswith("data:"))
    if not data.strip(): raise RuntimeError(f"empty response ({ct})")
    r = json.loads(data)
    if "error" in r: raise RuntimeError(r["error"])
    text = r["result"]["content"][0]["text"]
    try: return json.loads(text)
    except Exception: return text
