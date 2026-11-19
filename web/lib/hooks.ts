"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

/** Fetch now and every `ms` while the tab is visible. `reload()` refetches immediately. */
export function usePoll<T>(fetcher: () => Promise<T>, ms: number, deps: unknown[] = []) {
  const [data, setData] = useState<T | undefined>();
  const [error, setError] = useState<string | undefined>();
  const fetchRef = useRef(fetcher);
  useEffect(() => {
    fetchRef.current = fetcher;
  });

  const reload = useCallback(async () => {
    try {
      setData(await fetchRef.current());
      setError(undefined);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      if (!alive) return;
      if (document.visibilityState === "visible") await reload();
      timer = setTimeout(tick, ms);
    };
    tick();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ms, reload, ...deps]);

  return { data, error, reload, setData };
}

export type FlashAction = { label: string; onClick: () => void };

/** A transient message (toast) that clears itself, optionally with one action (e.g. Undo). */
export function useFlash(ms = 2500) {
  const [msg, setMsg] = useState<{ text: string; kind: "ok" | "err"; action?: FlashAction } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const flash = useCallback((text: string, kind: "ok" | "err" = "ok", action?: FlashAction, forMs?: number) => {
    setMsg({ text, kind, action });
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setMsg(null), forMs ?? (kind === "err" ? ms * 2 : ms));
  }, [ms]);
  const clear = useCallback(() => { clearTimeout(timer.current); setMsg(null); }, []);
  return { msg, flash, clear };
}

const subscribeHash = (cb: () => void) => {
  window.addEventListener("hashchange", cb);
  return () => window.removeEventListener("hashchange", cb);
};

/** The URL hash without "#", e.g. "12" for /clips#12. Empty on the server. */
export function useHash(): string {
  return useSyncExternalStore(subscribeHash, () => window.location.hash.slice(1), () => "");
}
