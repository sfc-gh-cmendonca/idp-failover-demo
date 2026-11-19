import { useEffect, useRef, useState } from "react";
import type { Snapshot, TrafficPoint } from "./types";

// Same-origin: the backend serves this UI and the browser carries basic-auth credentials.
async function post(path: string): Promise<void> {
  const res = await fetch(path, { method: "POST" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? res.statusText);
  }
}

export const runStep = (n: number) => post(`/api/demo/steps/${n}/run`);
export const resetStory = () => post("/api/demo/reset-state");
export const stopTraffic = () => post("/api/demo/traffic/stop");

const MAX_POINTS = 900;

export function useDemoStream() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [points, setPoints] = useState<TrafficPoint[]>([]);
  const [connected, setConnected] = useState(false);
  const lastTs = useRef(0);

  useEffect(() => {
    const es = new EventSource("/api/demo/stream");
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.addEventListener("history", (e) => {
      const hist = JSON.parse((e as MessageEvent).data) as TrafficPoint[];
      lastTs.current = hist.length ? hist[hist.length - 1].ts : 0;
      setPoints(hist.slice(-MAX_POINTS));
    });
    es.addEventListener("snapshot", (e) => {
      const s = JSON.parse((e as MessageEvent).data) as Snapshot;
      setSnap(s);
      const p = s.traffic.latest;
      if (p && p.ts > lastTs.current) {
        lastTs.current = p.ts;
        setPoints((prev) => [...prev, p].slice(-MAX_POINTS));
      }
    });
    return () => es.close();
  }, []);

  return { snap, points, connected };
}
