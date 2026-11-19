"""Failover storyline state machine: 7 ordered, idempotent steps with recorded evidence."""

import json
import threading
import time
import traceback
from datetime import datetime, timezone

from . import producer_ctl as producer
from . import settings
from .snowflake_ops import ops, parse_ts
from .traffic import traffic

STEPS = [
    (1, "ingest_frankfurt", "Ingest on Frankfurt"),
    (2, "traffic_frankfurt", "Traffic on Frankfurt"),
    (3, "stop_replication", "Stop replication to Ireland"),
    (4, "stop_harness", "Stop Integration and API Traffic"),
    (5, "failover", "Fail over to Ireland"),
    (6, "replay", "Replay the gap and reconcile"),
    (7, "ingest_ireland", "Point ingest to Ireland"),
    (8, "traffic_ireland", "Point traffic to Ireland"),
    (9, "failback", "Fail back to Frankfurt"),
]
STEP_NO = {sid: n for n, sid, _ in STEPS}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def fmt(d: datetime | None) -> str | None:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if d else None


class Demo:
    def __init__(self):
        self._lock = threading.Lock()
        self.running_step: int | None = None
        self.live: dict = {"frankfurt": None, "ireland": None, "producer": None, "errors": {}}
        self.state = self._load()

    # ---- persistence -------------------------------------------------------------------
    def _blank(self) -> dict:
        return {"steps": {str(n): {"status": "pending", "evidence": {}, "error": None,
                                   "started": None, "finished": None} for n, _, _ in STEPS},
                "log": [], "run": 1}

    def _load(self) -> dict:
        try:
            state = json.loads(settings.STATE_FILE.read_text())
            if set(state["steps"]) == {str(n) for n, _, _ in STEPS}:
                return state
        except (OSError, ValueError, KeyError):
            pass
        return self._blank()      # missing, corrupt, or from an older step layout

    def _ev(self, sid: str) -> dict:
        return self.state["steps"][str(STEP_NO[sid])]["evidence"]

    def _save(self):
        tmp = settings.STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, default=str, indent=1))
        tmp.replace(settings.STATE_FILE)

    def log(self, msg: str):
        self.state["log"].append({"ts": fmt(utcnow()), "msg": msg})
        self.state["log"] = self.state["log"][-300:]
        self._save()

    def reset(self):
        with self._lock:
            if self.running_step:
                raise RuntimeError("a step is running")
            run = self.state.get("run", 1) + 1
            self.state = self._blank()
            self.state["run"] = run
            self.log(f"Storyline reset (run {run})")

    # ---- live status poller ------------------------------------------------------------
    def start_poller(self):
        threading.Thread(target=self._poll, daemon=True).start()

    def _poll(self):
        while True:
            for key, fn in (("frankfurt", lambda: ops("frankfurt", "status")),
                            ("ireland", lambda: ops("ireland", "status")),
                            ("producer", producer.status)):
                try:
                    self.live[key] = fn()
                    self.live["errors"].pop(key, None)
                except Exception as e:  # keep the last good value, surface the error
                    self.live["errors"][key] = str(e)[:300]
            time.sleep(3)

    # ---- step execution ----------------------------------------------------------------
    def snapshot(self) -> dict:
        tp = traffic.latest()
        return {"steps": [{"n": n, "id": sid, "title": title, **self.state["steps"][str(n)]}
                          for n, sid, title in STEPS],
                "running_step": self.running_step, "run": self.state.get("run", 1),
                "log": self.state["log"][-60:], "live": self.live,
                "traffic": {"region": traffic.region, "running": traffic.running, "latest": tp}}

    def can_run(self, n: int) -> str | None:
        if self.running_step:
            return f"step {self.running_step} is running"
        if n > 1 and self.state["steps"][str(n - 1)]["status"] != "done":
            return f"complete step {n - 1} first"
        return None

    def run_step(self, n: int):
        with self._lock:
            why = self.can_run(n)
            if why:
                raise RuntimeError(why)
            self.running_step = n
            st = self.state["steps"][str(n)]
            # Re-running a finished step starts it fresh and invalidates everything after it,
            # so later steps can never act on evidence from an earlier pass. A failed step keeps
            # its partial evidence so it can resume.
            if st["status"] == "done":
                st["evidence"] = {}
            for later, _, _ in STEPS:
                if later > n:
                    self.state["steps"][str(later)].update(status="pending", evidence={}, error=None,
                                                           started=None, finished=None)
            st.update(status="running", error=None, started=fmt(utcnow()), finished=None)
            self._save()
        threading.Thread(target=self._execute, args=(n,), daemon=True).start()

    def _execute(self, n: int):
        sid, title = next((s, t) for k, s, t in STEPS if k == n)
        st = self.state["steps"][str(n)]
        self.log(f"Step {n} started: {title}")
        try:
            evidence = getattr(self, f"_step_{sid}")(st["evidence"])
            st["evidence"].update(evidence or {})
            st["status"] = "done"
            self.log(f"Step {n} done: {title}")
        except Exception as e:
            st["status"], st["error"] = "failed", str(e)
            self.log(f"Step {n} FAILED: {e}")
            traceback.print_exc()
        finally:
            st["finished"] = fmt(utcnow())
            self.running_step = None
            self._save()

    # ---- the storyline -------------------------------------------------------------------
    def _step_traffic_frankfurt(self, ev):
        ops("frankfurt", "iw_resume")
        traffic.start("frankfurt")
        self.log("JDBC lookup traffic started against Frankfurt IDP_IW_XS")
        p = traffic.wait_for_qps()
        return {"region": "frankfurt", "qps": p["qps"], "p50_ms": p["p50_ms"], "p95_ms": p["p95_ms"]}

    def _step_ingest_frankfurt(self, ev):
        producer.set_target("frankfurt")
        producer.start()
        s = self._wait_producer_rate()
        base = ops("frankfurt", "status")["table"]
        self.log(f"Producer streaming to Frankfurt at {s['rate']}/s")
        return {"region": "frankfurt", "rate": s["rate"], "frankfurt_rows": base["rows"],
                "ingest_started": fmt(utcnow())}

    def _step_stop_harness(self, ev):
        # Frankfurt is declared down: stop API lookup traffic and the producer, let the SDK flush.
        traffic.stop()
        self.log("API lookup traffic stopped")
        producer.stop()
        t_stop = utcnow()
        self.log("Integration harness stopped (writer outage declared)")
        frozen = self._wait_stable_count("frankfurt")
        self.log(f"Frankfurt frozen at {frozen:,} rows (demo-only reference)")
        return {"producer_stopped": fmt(t_stop), "frankfurt_frozen_rows": frozen}

    def _step_stop_replication(self, ev):
        # Suspend Ireland's scheduled refresh so the secondary is frozen at its last snapshot.
        try:
            ops("ireland", "suspend_fg")
        except Exception as e:
            if "already" not in str(e).lower():
                raise
        st = ops("ireland", "status")
        fg = st["failover_group"]
        self.log("Replication Frankfurt → Ireland suspended"
                 f" (last snapshot {(st.get('last_refresh') or {}).get('primary_snapshot_ts')})")
        return {"replication": "suspended", "next_scheduled_refresh": fg.get("next_scheduled_refresh") or "none",
                "last_refresh": st.get("last_refresh"), "ireland_rows": st["table"]["rows"]}

    def _step_failover(self, ev):
        stop = self._ev("stop_harness")
        t_stop = parse_ts(stop["producer_stopped"])
        if (producer.status() or {}).get("service") == "active":
            raise RuntimeError("integration harness is running; run 'Stop Integration and API Traffic' first")
        before = ops("ireland", "status")
        if "last_refresh" not in ev:      # keep the pre-promotion snapshot across a resumed run
            ev["last_refresh"] = before.get("last_refresh")
            ev["ireland_rows_at_promotion"] = before["table"]["rows"]
            self._save()
        t0 = time.monotonic()
        res = ops("ireland", "promote")
        promote_sec = round(time.monotonic() - t0, 1)
        t_promoted = utcnow()
        after = ops("ireland", "status")
        if str(after["failover_group"]["is_primary"]).lower() != "true":
            raise RuntimeError("Ireland did not become primary")
        self.log(f"Ireland primary ({res.get('promote')}) in {promote_sec}s")
        return {"promoted": fmt(t_promoted), "promote_sec": promote_sec,
                "rto_sec": round((t_promoted - t_stop).total_seconds(), 1), "promote_result": res.get("promote")}

    def _step_replay(self, ev):
        stop, fo = self._ev("stop_harness"), self._ev("failover")
        frozen = stop["frankfurt_frozen_rows"]
        status = ops("ireland", "status")
        if str(status["failover_group"]["is_primary"]).lower() != "true":
            raise RuntimeError("Ireland is not primary (replay can only write to the primary); run 'Fail over to Ireland'")
        snap = parse_ts((fo.get("last_refresh") or {}).get("primary_snapshot_ts"))
        stopped = parse_ts(stop["producer_stopped"])
        ie = status["table"]
        watermark = parse_ts(ie["max_event_ts"])
        since = snap or watermark
        if since is None:
            raise RuntimeError("could not determine last replication time")
        missing_rows = frozen - ie["rows"]
        self.log(f"Last replication snapshot {fmt(snap)}; Ireland watermark {fmt(watermark)}; "
                 f"{missing_rows:,} rows behind Frankfurt")
        result = producer.replay("ireland", fmt(since))
        self.log(f"Replayed {result['replayed']:,} of {result['missing']:,} missing rows "
                 f"({result['journal_rows_in_window']:,} journal rows checked)")
        rows = self._wait_count("ireland", frozen)
        dupes = ops("ireland", "dupes")["duplicate_keys"]
        # Baseline from Frankfurt, which replay never writes to (historic dupes predate the
        # epoch-microsecond PRODUCT_VERSION); replay must not add any.
        base_dupes = ops("frankfurt", "dupes")["duplicate_keys"]
        recheck = producer.replay("ireland", fmt(since), dry=True)
        checks = [
            {"name": "Ireland rows = Frankfurt frozen rows", "ok": rows == frozen,
             "detail": f"{rows:,} vs {frozen:,}"},
            {"name": "Every journaled row present in Ireland", "ok": recheck["missing"] == 0,
             "detail": f"{recheck['journal_rows_in_window']:,} checked, {recheck['missing']} missing"},
            {"name": "No duplicate (PRODUCT_ID, PRODUCT_VERSION) introduced by replay", "ok": dupes == base_dupes,
             "detail": f"Ireland {dupes:,} = Frankfurt baseline {base_dupes:,}"},
        ]
        ev.update(checks=checks)
        out = {"last_replication_snapshot": fmt(snap), "ireland_watermark": fmt(watermark),
               "rpo_window_sec": round((stopped - since).total_seconds(), 1),
               "rows_behind": missing_rows, "replay": result, "ireland_rows": rows, "checks": checks}
        if not all(c["ok"] for c in checks):
            ev.update(out)
            raise RuntimeError("reconciliation checks failed")
        return out

    def _step_ingest_ireland(self, ev):
        before = ops("ireland", "status")["table"]
        producer.set_target("ireland")
        producer.start()
        s = self._wait_producer_rate()
        after = self._wait_rows_grow("ireland", before["rows"])
        self.log(f"Producer streaming to Ireland at {s['rate']}/s")
        return {"region": "ireland", "rate": s["rate"], "ireland_rows": after}

    def _step_traffic_ireland(self, ev):
        ops("ireland", "iw_resume")
        t0 = time.monotonic()
        traffic.start("ireland")
        p = traffic.wait_for_qps()
        cut = round(time.monotonic() - t0, 1)
        self.log(f"Traffic serving from Ireland after {cut}s switchover")
        return {"region": "ireland", "qps": p["qps"], "p50_ms": p["p50_ms"], "p95_ms": p["p95_ms"],
                "switchover_sec": cut}

    def _step_failback(self, ev):
        # Resumable: each phase records its result so a re-run continues where it stopped.
        if not ev.get("ireland_frozen_rows"):
            producer.stop()
            self.log("Producer stopped for failback")
            ev["ireland_frozen_rows"] = self._wait_stable_count("ireland")
            self._save()
        frozen = ev["ireland_frozen_rows"]
        if str(ops("frankfurt", "status")["failover_group"]["is_primary"]).lower() != "true":
            ops("frankfurt", "refresh_fg")
            self.log(f"Frankfurt refreshing from Ireland (target {frozen:,} rows)")
            ev["frankfurt_rows"] = self._wait_count("frankfurt", frozen, timeout=600)
            self._save()
            ops("frankfurt", "promote")
            self.log("Frankfurt promoted to primary")
        rows = ev.get("frankfurt_rows") or ops("frankfurt", "status")["table"]["rows"]
        self._resume_secondary_refresh("ireland")
        self.log("Ireland back to secondary with scheduled refresh")
        producer.set_target("frankfurt")
        producer.start()
        traffic.start("frankfurt")
        traffic.wait_for_qps()
        ops("ireland", "iw_suspend")
        dupes = ops("frankfurt", "dupes")["duplicate_keys"]
        base_dupes = ops("ireland", "dupes")["duplicate_keys"]
        checks = [{"name": "Frankfurt rows = Ireland frozen rows", "ok": rows == frozen,
                   "detail": f"{rows:,} vs {frozen:,}"},
                  {"name": "Duplicate keys unchanged by failback", "ok": dupes == base_dupes,
                   "detail": f"Frankfurt {dupes:,} = Ireland {base_dupes:,}"}]
        self.log("Ingest and traffic back on Frankfurt; Ireland IDP_IW_XS suspended")
        if not all(c["ok"] for c in checks):
            raise RuntimeError("failback checks failed")
        return {"ireland_frozen_rows": frozen, "frankfurt_rows": rows, "checks": checks}

    def _resume_secondary_refresh(self, region, timeout=180):
        """RESUME the scheduled refresh once `region` has registered that it is secondary."""
        deadline, last = time.monotonic() + timeout, None
        while time.monotonic() < deadline:
            try:
                ops(region, "resume_fg")
                return
            except Exception as e:
                msg = str(e)
                if "already" in msg.lower() and "resum" in msg.lower():
                    return
                last = msg
                time.sleep(10)
        raise RuntimeError(f"could not resume {region} refresh: {last}")

    # ---- helpers -----------------------------------------------------------------------
    def _wait_producer_rate(self, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            s = (producer.status() or {}).get("stats") or {}
            if s.get("running") and s.get("rate", 0) > 0 and time.time() - s.get("ts", 0) < 10:
                return s
            time.sleep(3)
        raise TimeoutError("producer did not start streaming")

    def _wait_stable_count(self, region, settle=3, timeout=180):
        """Row count once it stops changing for `settle` consecutive polls (streaming flushed)."""
        last, same, deadline = None, 0, time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = ops(region, "status")["table"]["rows"]
            same = same + 1 if rows == last else 0
            if same >= settle:
                return rows
            last = rows
            time.sleep(5)
        raise TimeoutError(f"{region} row count did not settle")

    def _wait_count(self, region, target, timeout=240):
        deadline, rows = time.monotonic() + timeout, None
        while time.monotonic() < deadline:
            rows = ops(region, "status")["table"]["rows"]
            if rows >= target:
                return rows
            time.sleep(5)
        return rows

    def _wait_rows_grow(self, region, base, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = ops(region, "status")["table"]["rows"]
            if rows > base:
                return rows
            time.sleep(5)
        raise TimeoutError(f"no new rows in {region}")


demo = Demo()
