/**
 * hooks/useHealth.ts
 * ------------------
 * Polls GET /health every 15 seconds.
 * Returns current health data + last-checked timestamp.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { apiHealth, type HealthResponse } from '../api/client';

const POLL_INTERVAL_MS = 15_000;

export interface HealthState {
  data: HealthResponse | null;
  error: string | null;
  lastChecked: Date | null;
  loading: boolean;
}

export function useHealth(): HealthState {
  const [state, setState] = useState<HealthState>({
    data: null,
    error: null,
    lastChecked: null,
    loading: true,
  });

  const check = useCallback(async () => {
    try {
      const data = await apiHealth();
      setState({ data, error: null, lastChecked: new Date(), loading: false });
    } catch (err: unknown) {
      const msg =
        err && typeof err === 'object' && 'detail' in err
          ? String((err as { detail: string }).detail)
          : String(err);
      setState((prev) => ({
        ...prev,
        error: msg,
        lastChecked: new Date(),
        loading: false,
      }));
    }
  }, []);

  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    check(); // immediate first check
    timerRef.current = setInterval(check, POLL_INTERVAL_MS);
    return () => {
      if (timerRef.current !== null) clearInterval(timerRef.current);
    };
  }, [check]);

  return state;
}
