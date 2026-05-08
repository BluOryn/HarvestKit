import { create } from "zustand";

type Toast = { id: number; kind: "default" | "green" | "red"; text: string };
type UI = {
  view: "dashboard" | "library" | "runs" | "settings";
  setView: (v: UI["view"]) => void;
  mode: "jobs" | "general";
  setMode: (m: UI["mode"]) => void;
  selectedJobId: string | null;
  selectJob: (id: string | null) => void;
  toasts: Toast[];
  toast: (text: string, kind?: Toast["kind"]) => void;
  removeToast: (id: number) => void;
};

let toastSeq = 0;
const MODE_KEY = "jh_mode";
const initMode = (typeof localStorage !== "undefined" && (localStorage.getItem(MODE_KEY) as "jobs" | "general")) || "jobs";

export const useUI = create<UI>((set) => ({
  view: "dashboard",
  setView: (view) => set({ view }),
  mode: initMode,
  setMode: (mode) => {
    try { localStorage.setItem(MODE_KEY, mode); } catch {}
    set({ mode });
  },
  selectedJobId: null,
  selectJob: (id) => set({ selectedJobId: id }),
  toasts: [],
  toast: (text, kind = "default") => {
    const id = ++toastSeq;
    set((s) => ({ toasts: [...s.toasts, { id, kind, text }] }));
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), 3500);
  },
  removeToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));
