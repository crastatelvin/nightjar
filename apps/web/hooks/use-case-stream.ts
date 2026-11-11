"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getApiBase, getToken } from "@/lib/api";
import type { CaseFile } from "@/lib/types";

interface StreamState {
  caseFile: CaseFile | null;
  connected: boolean;
  fallback: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * Live case-file state.
 *
 * The SSE endpoint emits timeline events, not whole snapshots (and EventSource
 * cannot attach an Authorization header). So the stream is used as a change
 * signal: each event triggers a snapshot fetch. With a bearer token configured
 * the hook polls instead, and a slow safety poll covers dropped SSE connections.
 *
 * This hook owns the first load and every refresh. Components must not keep a
 * parallel copy of the case file: a slower duplicate fetch can otherwise land
 * after a newer stream snapshot and revert the UI to stale state.
 */
export function useCaseStream(incidentId: string): StreamState {
  const [caseFile, setCaseFile] = useState<CaseFile | null>(null);
  const [connected, setConnected] = useState(false);
  const [fallback, setFallback] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const inFlight = useRef(false);
  const queued = useRef(false);
  const closed = useRef(false);
  const seen = useRef(false);

  const load = useCallback(async () => {
    if (!incidentId) return;
    if (inFlight.current) {
      queued.current = true;
      return;
    }
    inFlight.current = true;
    try {
      const token = getToken();
      const response = await fetch(`${getApiBase()}/v1/incidents/${incidentId}`, {
        headers: token ? { authorization: `Bearer ${token}` } : {},
        cache: "no-store",
      });
      if (closed.current) return;
      if (response.ok) {
        setCaseFile((await response.json()) as CaseFile);
        setConnected(true);
        setError(null);
        seen.current = true;
      } else if (!seen.current) {
        setConnected(false);
        setError(
          response.status === 404
            ? "No incident exists with this id."
            : `The control plane returned ${response.status}.`,
        );
      }
    } catch (caught) {
      if (closed.current) return;
      setConnected(false);
      if (!seen.current) {
        setError(
          caught instanceof Error
            ? caught.message
            : "Could not reach the control plane.",
        );
      }
    } finally {
      inFlight.current = false;
      if (queued.current && !closed.current) {
        queued.current = false;
        void load();
      }
    }
  }, [incidentId]);

  const reload = useCallback(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!incidentId) return;
    closed.current = false;
    seen.current = false;
    const token = getToken();

    void load();

    const startPolling = (ms: number) => {
      if (timer.current) clearInterval(timer.current);
      timer.current = setInterval(() => void load(), ms);
    };

    if (token) {
      setFallback(true);
      startPolling(2500);
      return () => {
        closed.current = true;
        if (timer.current) clearInterval(timer.current);
      };
    }

    const source = new EventSource(
      `${getApiBase()}/v1/incidents/${incidentId}/stream`,
    );
    source.addEventListener("open", () => {
      setConnected(true);
      setFallback(false);
      startPolling(10000);
    });
    const onTimeline = () => {
      setConnected(true);
      void load();
    };
    source.addEventListener("timeline", onTimeline as EventListener);
    source.addEventListener("error", () => {
      setConnected(false);
      source.close();
      if (!closed.current) {
        setFallback(true);
        startPolling(3000);
      }
    });

    return () => {
      closed.current = true;
      source.close();
      if (timer.current) clearInterval(timer.current);
    };
  }, [incidentId, load]);

  return { caseFile, connected, fallback, error, reload };
}
