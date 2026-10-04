"""Call Rive MCP tools over the local HTTP endpoint (single global session; never send initialize)."""
import json, urllib.request, itertools
URL = "http://127.0.0.1:9791/mcp"; _ids = itertools.count(1000)
def call(name, arguments):
    body = json.dumps({"jsonrpc": "2.0", "id": next(_ids), "method": "tools/call", "params": {"name": name, "arguments": arguments}}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    r = json.load(urllib.request.urlopen(req, timeout=120))
    if "error" in r: raise RuntimeError(r["error"])
    text = r["result"]["content"][0]["text"]
    try: return json.loads(text)
    except Exception: return text
