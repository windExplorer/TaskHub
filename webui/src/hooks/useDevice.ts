import { useCallback, useEffect, useState } from "react";

import { fetchDevice } from "../api";
import type { DeviceResp } from "../types";

/** GET /device 轮询：默认 10s，页面不可见时暂停；手动 refresh 供刷新按钮。 */
export function useDevice(intervalMs = 10_000) {
  const [device, setDevice] = useState<DeviceResp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState(0);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const d = await fetchDevice();
      setDevice(d);
      setError(null);
      setUpdatedAt(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const id = window.setInterval(() => {
      if (document.visibilityState !== "hidden") void load();
    }, intervalMs);
    return () => window.clearInterval(id);
  }, [load, intervalMs]);

  return { device, error, updatedAt, loading, refresh: load };
}
