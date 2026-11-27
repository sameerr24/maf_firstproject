import { useEffect } from "react";

/** Run `load` now, every `ms`, and whenever the window regains focus or the tab becomes
 *  visible (a scheduler run or a Telegram reply can change things at any time). */
export function usePolling(load, ms) {
  useEffect(() => {
    load();
    const timer = setInterval(load, ms);
    const onVisible = () => document.visibilityState === "visible" && load();
    window.addEventListener("focus", load);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", load);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load, ms]);
}
