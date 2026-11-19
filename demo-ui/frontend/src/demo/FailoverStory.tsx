import { useMemo, useState } from "react";
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { resetStory, runStep, stopTraffic, useDemoStream } from "./api";
import type { AccountStatus, Check, ProducerStatus, Snapshot, Step, TrafficPoint } from "./types";

const REGION_COLOR = { frankfurt: "#38bdf8", ireland: "#10b981" } as const;

const NARRATIVE: Record<number, { lead: string; detail: string }> = {
  1: {
    lead: "The integration harness streams product updates into Frankfurt.",
    detail: "Snowpipe Streaming writes full-state product snapshots (50 messages/s) into the interactive table. Every row handed to the SDK is also journaled at the source, standing in for the upstream system's replay buffer. IDP_FG replicates Frankfurt to Ireland every 10 minutes.",
  },
  2: {
    lead: "Client traffic is served from Frankfurt.",
    detail: "Continuous JDBC point lookups (latest version of a product) run against the Frankfurt interactive warehouse IDP_IW_XS. This is the read path we must keep alive.",
  },
  4: {
    lead: "Stop Integration and API Traffic: Frankfurt is declared down.",
    detail: "API lookup traffic to Frankfurt is stopped, then the producer is stopped and the SDK flushes its buffered rows. Once Frankfurt's row count stops changing, it is recorded as the outage reference (demo only; in a real regional outage Frankfurt would be unreachable). Rows committed since the last replication snapshot are now Ireland's RPO gap.",
  },
  3: {
    lead: "Stop replication from Frankfurt to Ireland.",
    detail: "ALTER FAILOVER GROUP IDP_FG SUSPEND in Ireland stops the 10-minute scheduled refresh, so Ireland is frozen at its last replicated snapshot. That snapshot is the starting point for the replay in step 6. Frankfurt stays primary and keeps ingesting until the next step.",
  },
  5: {
    lead: "Ireland is promoted to primary.",
    detail: "Ireland's last replication snapshot is recorded, then PROMOTE_IDP() suspends the scheduled refresh and makes IDP_FG primary in Ireland. From here Frankfurt is read-only.",
  },
  6: {
    lead: "Find the last replicated point and replay everything after it.",
    detail: "The last replication snapshot comes from Ireland's refresh history. Journaled rows from that point (minus a safety margin) are diffed against Ireland on (PRODUCT_ID, PRODUCT_VERSION). Only missing rows are streamed, so the replay is exactly-once on an append-only table. Row counts and duplicates are then verified.",
  },
  7: {
    lead: "The integration harness now writes to Ireland.",
    detail: "The producer is repointed to the Ireland account and resumes streaming. New rows land in Ireland, which is now the primary.",
  },
  8: {
    lead: "Client traffic moves to Ireland.",
    detail: "Ireland's interactive warehouse is resumed and the lookup traffic reconnects to the Ireland account. Watch the chart switch colour.",
  },
  9: {
    lead: "Fail back to Frankfurt and reset the demo.",
    detail: "Ingest stops, Frankfurt refreshes from Ireland until the counts match, and Frankfurt is promoted again. Ireland's scheduled refresh is resumed, ingest and traffic return to Frankfurt, and Ireland's warehouse is suspended.",
  },
};

const fmtN = (n: number | null | undefined) => (n == null ? "-" : n.toLocaleString());
const isPrimary = (a: AccountStatus | null) => a && String(a.failover_group.is_primary).toLowerCase() === "true";

export function FailoverStory() {
  const { snap, points, connected } = useDemoStream();
  const [selected, setSelected] = useState(1);
  const [err, setErr] = useState<string | null>(null);

  if (!snap) {
    return <div className="story-loading">{connected ? "Loading…" : "Connecting to demo backend…"}</div>;
  }
  const step = snap.steps[selected - 1];
  const next = snap.steps.find((s) => s.status !== "done");

  async function act(fn: () => Promise<void>) {
    setErr(null);
    try { await fn(); } catch (e) { setErr((e as Error).message); }
  }

  return (
    <div className="story">
      <StatusStrip snap={snap} />
      <TrafficChart points={points} />
      <div className="story-body">
        <nav className="story-rail">
          {snap.steps.map((s) => (
            <button key={s.n} className={`rail-step ${s.status} ${s.n === selected ? "selected" : ""}`}
                    onClick={() => setSelected(s.n)}>
              <span className="rail-num">{s.status === "done" ? "✓" : s.status === "failed" ? "!" : s.n}</span>
              <span>{s.title}</span>
            </button>
          ))}
          <button className="rail-reset" disabled={snap.running_step != null}
                  onClick={() => act(resetStory)}>Reset storyline (run {snap.run})</button>
        </nav>
        <section className="story-main">
          <StepPanel step={step} snap={snap} isNext={next?.n === step.n}
                     onRun={() => act(async () => { await runStep(step.n); })} />
          {err && <div className="story-error">{err}</div>}
          <EventLog log={snap.log} />
        </section>
      </div>
    </div>
  );
}

function StepPanel({ step, snap, isNext, onRun }: { step: Step; snap: Snapshot; isNext: boolean; onRun: () => void }) {
  const nar = NARRATIVE[step.n];
  const blocked = snap.running_step != null || (step.n > 1 && snap.steps[step.n - 2].status !== "done");
  const { checks, ...rest } = step.evidence;
  return (
    <div className="step-panel">
      <div className="step-head">
        <div>
          <div className="step-kicker">Step {step.n} of {snap.steps.length}</div>
          <h2>{step.title}</h2>
        </div>
        <button className="run-btn" disabled={blocked || step.status === "running"} onClick={onRun}>
          {step.status === "running" ? "Running…" : step.status === "done" ? "Re-run step" : isNext ? "Run step" : "Run"}
        </button>
      </div>
      <p className="step-lead">{nar.lead}</p>
      <p className="step-detail">{nar.detail}</p>
      {step.error && <div className="story-error">{step.error}</div>}
      {checks && <Checks checks={checks} />}
      {Object.keys(rest).length > 0 && <Evidence ev={rest} />}
      {step.status === "done" && step.n < snap.steps.length && (
        <div className="step-times">Re-running this step resets the steps after it.</div>
      )}
      {step.started && (
        <div className="step-times">started {step.started} UTC{step.finished ? ` · finished ${step.finished} UTC` : ""}</div>
      )}
    </div>
  );
}

function Checks({ checks }: { checks: Check[] }) {
  return (
    <ul className="checks">
      {checks.map((c) => (
        <li key={c.name} className={c.ok ? "ok" : "bad"}>
          <span className="tick">{c.ok ? "✓" : "✗"}</span> {c.name} <span className="muted">({c.detail})</span>
        </li>
      ))}
    </ul>
  );
}

const LABELS: Record<string, string> = {
  rto_sec: "RTO (writer stop → Ireland primary), s",
  rpo_window_sec: "RPO window (last snapshot → writer stop), s",
  promote_sec: "Promotion time, s",
  frankfurt_frozen_rows: "Frankfurt rows at outage",
  ireland_rows_at_promotion: "Ireland rows at promotion",
  rows_behind: "Rows behind at promotion",
  last_replication_snapshot: "Last replication snapshot (UTC)",
  ireland_watermark: "Ireland MAX(EVENT_TS) before replay",
  switchover_sec: "Traffic switchover, s",
};

function Evidence({ ev }: { ev: Record<string, unknown> }) {
  const rows = Object.entries(ev).flatMap(([k, v]) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.entries(v as Record<string, unknown>).map(([k2, v2]) => [`${k}.${k2}`, v2] as const)
      : [[k, v] as const]);
  return (
    <table className="evidence">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <th>{LABELS[k] ?? k.replace(/_/g, " ")}</th>
            <td>{typeof v === "number" ? v.toLocaleString() : String(v ?? "-")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function StatusStrip({ snap }: { snap: Snapshot }) {
  const { frankfurt, ireland, producer, errors } = snap.live;
  return (
    <div className="status-strip">
      <AccountCard name="Frankfurt" region="frankfurt" a={frankfurt} err={errors.frankfurt} />
      <AccountCard name="Ireland" region="ireland" a={ireland} err={errors.ireland} />
      <ProducerCard p={producer} err={errors.producer} />
      <div className="card">
        <div className="card-title">Lookup traffic</div>
        <div className="card-big" style={{ color: snap.traffic.region ? REGION_COLOR[snap.traffic.region as "frankfurt"] : undefined }}>
          {snap.traffic.running ? "Running" : "Stopped"}
        </div>
        <div className="muted">{snap.traffic.running && snap.traffic.region ? `→ ${snap.traffic.region}` : "-"}</div>
        <button className="stop-btn" disabled={!snap.traffic.running}
                onClick={() => { stopTraffic().catch(() => undefined); }}>
          Stop traffic
        </button>
      </div>
    </div>
  );
}

function AccountCard({ name, region, a, err }: { name: string; region: "frankfurt" | "ireland"; a: AccountStatus | null; err?: string }) {
  const primary = isPrimary(a);
  return (
    <div className="card" style={{ borderTopColor: REGION_COLOR[region] }}>
      <div className="card-title">
        {name} <span className={`badge ${primary ? "primary" : "secondary"}`}>{a ? (primary ? "PRIMARY" : "SECONDARY") : "?"}</span>
      </div>
      <div className="card-big">{fmtN(a?.table.rows)} <span className="muted small">rows</span></div>
      <div className="muted">
        newest row {a?.table.seconds_behind != null ? `${a.table.seconds_behind}s ago` : "-"} · IW {a?.iw.state?.toLowerCase() ?? "-"}
        {a?.iw.clusters ? ` (${a.iw.clusters}×XS)` : ""}
      </div>
      {!primary && a?.last_refresh && <div className="muted">last snapshot {a.last_refresh.primary_snapshot_ts}</div>}
      {err && <div className="card-err">{err}</div>}
    </div>
  );
}

function ProducerCard({ p, err }: { p: ProducerStatus | null; err?: string }) {
  const region = p?.target === "secondary" ? "ireland" : "frankfurt";
  const live = p?.service === "active";
  return (
    <div className="card">
      <div className="card-title">Integration harness</div>
      <div className="card-big" style={{ color: live ? REGION_COLOR[region] : undefined }}>
        {live ? "Running" : "Stopped"}
      </div>
      <div className="muted">→ {p ? region : "-"}</div>
      {err && <div className="card-err">{err}</div>}
    </div>
  );
}

function TrafficChart({ points }: { points: TrafficPoint[] }) {
  const data = useMemo(() => points.map((p) => ({
    t: new Date(p.ts).toLocaleTimeString(),
    frankfurt: p.region === "frankfurt" ? p.p95_ms : null,
    ireland: p.region === "ireland" ? p.p95_ms : null,
  })), [points]);
  return (
    <div className="chart-card">
      <div className="card-title">Lookup p95 latency by serving region</div>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={data} margin={{ top: 8, right: 24, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="#1e293b" />
          <XAxis dataKey="t" stroke="#94a3b8" minTickGap={60} />
          <YAxis stroke="#94a3b8" unit=" ms" />
          <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #1e293b" }} />
          <Legend />
          <Line dataKey="frankfurt" name="p95 Frankfurt (ms)" stroke={REGION_COLOR.frankfurt} dot={false} isAnimationActive={false} connectNulls={false} />
          <Line dataKey="ireland" name="p95 Ireland (ms)" stroke={REGION_COLOR.ireland} dot={false} isAnimationActive={false} connectNulls={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function EventLog({ log }: { log: Snapshot["log"] }) {
  return (
    <div className="event-log">
      <div className="card-title">Event log</div>
      {[...log].reverse().map((l, i) => (
        <div key={i} className="log-line"><span className="muted">{l.ts.slice(11, 19)}</span> {l.msg}</div>
      ))}
    </div>
  );
}
