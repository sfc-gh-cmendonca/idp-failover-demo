"""Run SQL statements (one per arg or a file via -f) against Ireland as CHRIS/ACCOUNTADMIN."""
import sys, snowflake.connector
from pathlib import Path
KEY = Path.home() / ".snowflake/cortex/playground/workspace/idp-streaming-harness/keys/chris_ir_key.p8"
args = sys.argv[1:]
stmts = Path(args[1]).read_text() if args[:1] == ["-f"] else ";\n".join(args)
con = snowflake.connector.connect(account="SFSEEUROPE-CMENDONCA_DEMO_IR", user="CHRIS",
                                  private_key_file=str(KEY), role="ACCOUNTADMIN", warehouse="IDP_ADMIN_WH")
for cur in con.execute_string(stmts, remove_comments=True):
    rows = cur.fetchall()
    cols = [c[0] for c in cur.description or []]
    print("--", (cur.query or "")[:120].replace("\n", " "))
    if cols: print(" | ".join(cols))
    for r in rows[:50]: print(" | ".join(str(v) for v in r))
