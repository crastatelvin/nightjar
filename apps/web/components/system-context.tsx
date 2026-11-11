"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api } from "@/lib/api";
import type { MetaInfo } from "@/lib/types";

interface SystemState {
  meta: MetaInfo | null;
  online: boolean;
  checkedAt: number | null;
  refresh: () => void;
}

const SystemContext = createContext<SystemState>({
  meta: null,
  online: false,
  checkedAt: null,
  refresh: () => {},
});

export function SystemProvider({ children }: { children: ReactNode }) {
  const [meta, setMeta] = useState<MetaInfo | null>(null);
  const [online, setOnline] = useState(false);
  const [checkedAt, setCheckedAt] = useState<number | null>(null);

  const refresh = useCallback(() => {
    void Promise.allSettled([api.health(), api.meta()]).then(([health, info]) => {
      setOnline(health.status === "fulfilled" && health.value.status === "ok");
      if (info.status === "fulfilled") setMeta(info.value);
      setCheckedAt(Date.now());
    });
  }, []);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 20000);
    window.addEventListener("nightjar:settings", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("nightjar:settings", refresh);
    };
  }, [refresh]);

  const value = useMemo(
    () => ({ meta, online, checkedAt, refresh }),
    [meta, online, checkedAt, refresh],
  );

  return <SystemContext.Provider value={value}>{children}</SystemContext.Provider>;
}

export function useSystem() {
  return useContext(SystemContext);
}
