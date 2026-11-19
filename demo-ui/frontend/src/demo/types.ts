export type StepStatus = "pending" | "running" | "done" | "failed";

export interface Check {
  name: string;
  ok: boolean;
  detail: string;
}

export interface Step {
  n: number;
  id: string;
  title: string;
  status: StepStatus;
  evidence: Record<string, unknown> & { checks?: Check[] };
  error: string | null;
  started: string | null;
  finished: string | null;
}

export interface AccountStatus {
  account: string;
  failover_group: { is_primary: string; primary: string; next_scheduled_refresh?: string };
  table: { rows: number; max_event_ts: string | null; seconds_behind: number | null };
  last_refresh: { primary_snapshot_ts: string; end_time: string } | null;
  iw: { state: string; clusters: number; max_clusters: number };
}

export interface ProducerStatus {
  service: string;
  target: "primary" | "secondary";
  stats: { running: boolean; rate: number; sent: number; journal_rows: number; account: string; ts: number } | null;
}

export interface TrafficPoint {
  ts: number;
  region: "frankfurt" | "ireland";
  qps: number;
  p50_ms: number;
  p95_ms: number;
  errors: number;
  active: number;
  last_error?: string;
}

export interface Snapshot {
  steps: Step[];
  running_step: number | null;
  run: number;
  log: { ts: string; msg: string }[];
  live: {
    frankfurt: AccountStatus | null;
    ireland: AccountStatus | null;
    producer: ProducerStatus | null;
    errors: Record<string, string>;
  };
  traffic: { region: string | null; running: boolean; latest: TrafficPoint | null };
}
