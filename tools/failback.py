"""One-off failback to Frankfurt using the ops user's DEMO_OPS actions."""
import json, time, snowflake.connector
from pathlib import Path
KEY = str(Path.home()/".snowflake/cortex/playground/workspace/CURSOR_DEMOS/Performance_Test_Harness/keys/ops_key.p8")
ACC = {"frankfurt": "SFSEEUROPE-EU_DEMO59", "ireland": "SFSEEUROPE-CMENDONCA_DEMO_IR"}
cons = {k: snowflake.connector.connect(account=v, user="IDP_DEMO_OPS_SVC", private_key_file=KEY,
        session_parameters={"TIMEZONE": "UTC"}) for k, v in ACC.items()}
def ops(r, a):
    c = cons[r].cursor(); c.execute("USE WAREHOUSE IDP_ADMIN_WH")
    return json.loads(c.execute("CALL IDP_ADMIN.PUBLIC.DEMO_OPS(%s)", (a,)).fetchone()[0])
st = lambda r: ops(r, "status")
f, i = st("frankfurt"), st("ireland")
print("before: frankfurt primary", f["failover_group"]["is_primary"], f["table"]["rows"], "| ireland primary", i["failover_group"]["is_primary"], i["table"]["rows"])
target = i["table"]["rows"]
if f["failover_group"]["is_primary"] != "true":
    t0 = time.time(); ops("frankfurt", "refresh_fg"); print(f"refresh issued ({time.time()-t0:.0f}s)")
    for _ in range(60):
        rows = st("frankfurt")["table"]["rows"]
        if rows >= target: break
        time.sleep(10)
    print("frankfurt rows", rows, "target", target)
    assert rows == target, "counts differ; not promoting"
    print("promote:", ops("frankfurt", "promote"))
for _ in range(18):
    try: ops("ireland", "resume_fg"); print("ireland refresh resumed"); break
    except Exception as e:
        if "already" in str(e).lower(): print("ireland refresh already running"); break
        print("waiting for ireland to register secondary:", str(e)[:100]); time.sleep(10)
ops("ireland", "iw_suspend"); print("ireland IW suspended")
f, i = st("frankfurt"), st("ireland")
print("after: frankfurt primary", f["failover_group"]["is_primary"], f["table"]["rows"], "| ireland primary", i["failover_group"]["is_primary"], i["table"]["rows"], "IW", i["iw"]["state"])
print("dupes frankfurt", ops("frankfurt","dupes")["duplicate_keys"], "ireland", ops("ireland","dupes")["duplicate_keys"])
