import { useCallback, useEffect, useRef, useState } from "react";
import { get } from "./api";

/** Fetch `url` now and again every `interval(data)` ms (null: stop). Keeps the last data on errors, backs off. */
export function usePoll<T>(url: string | null, interval: (data: T | null) => number | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const timer = useRef<number | undefined>(undefined);
  const backoff = useRef(0);
  const intervalRef = useRef(interval);
  intervalRef.current = interval;

  const load = useCallback(async () => {
    if (!url) return;
    window.clearTimeout(timer.current);
    let next: number | null;
    try {
      const d = await get<T>(url);
      setData(d);
      setError(null);
      backoff.current = 0;
      next = intervalRef.current(d);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      backoff.current = Math.min((backoff.current || 1000) * 2, 8000);
      next = backoff.current;
    } finally {
      setLoading(false);
    }
    if (next !== null) timer.current = window.setTimeout(load, document.hidden ? Math.max(next, 5000) : next);
  }, [url]);

  useEffect(() => {
    setLoading(true);
    setData(null);
    load();
    return () => window.clearTimeout(timer.current);
  }, [load]);

  return { data, error, loading, reload: load };
}

/** Board cadence (design): 2 s while anything is live, 5 s otherwise. */
export const boardInterval = (live: number | undefined) => (live ? 2000 : 5000);
/** Run cadence: 1 s while live, then stop. */
export const runInterval = (live: boolean | undefined) => (live ? 1000 : null);
