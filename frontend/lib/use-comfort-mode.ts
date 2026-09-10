"use client";

import { useSyncExternalStore } from "react";

const KEY = "lingnian.comfortMode";
const EVENT = "lingnian:comfort-mode";

function snapshot() {
  try { return window.localStorage.getItem(KEY) === "on"; }
  catch { return document.documentElement.dataset.comfortMode === "on"; }
}

function subscribe(onChange: () => void) {
  window.addEventListener(EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

export function useComfortMode() {
  return useSyncExternalStore(subscribe, snapshot, () => false);
}

export function setComfortMode(enabled: boolean) {
  document.documentElement.dataset.comfortMode = enabled ? "on" : "off";
  try { window.localStorage.setItem(KEY, enabled ? "on" : "off"); }
  catch { /* The current page can still use the mode if storage is unavailable. */ }
  window.dispatchEvent(new Event(EVENT));
}
