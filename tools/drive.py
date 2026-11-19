"""Drive the deployed storyline API step by step (same calls the UI makes).
   python3 drive.py 1 2 3 ...   -> runs those steps in order, prints evidence"""
import base64, json, sys, time, urllib.request
from pathlib import Path

BASE = "http://63.185.23.145:8080"
PW = (Path.home() / ".snowflake/cortex/playground/workspace/CURSOR_DEMOS/Performance_Test_Harness/keys/demo_password").read_text().strip()
AUTH = {"Authorization": "Basic " + base64.b64encode(f"demo:{PW}".encode()).decode()}


def call(path, method="GET"):
    req = urllib.request.Request(BASE + path, method=method, headers=AUTH, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


for arg in sys.argv[1:]:
    if arg == "reset":
        print(call("/api/demo/reset-state", "POST")); continue
    n = int(arg)
    t0 = time.time()
    call(f"/api/demo/steps/{n}/run", "POST")
    while True:
        time.sleep(4)
        s = call("/api/demo/state")
        st = s["steps"][n - 1]
        if st["status"] in ("done", "failed"):
            break
    print(f"\n=== step {n} {st['title']}: {st['status'].upper()} in {time.time() - t0:.0f}s")
    if st["error"]:
        print("ERROR:", st["error"])
    print(json.dumps(st["evidence"], indent=1, default=str)[:2500])
    tp = s["traffic"]["latest"]
    if tp:
        print(f"traffic: {s['traffic']['region']} qps={tp['qps']} p95={tp['p95_ms']} errors={tp['errors']}")
    if st["status"] == "failed":
        for l in s["log"][-8:]:
            print("  log", l["ts"], l["msg"])
        sys.exit(1)
