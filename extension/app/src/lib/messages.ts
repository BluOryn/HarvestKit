import type { Job } from "./schema";

export type ListCard = {
  title: string;
  company: string;
  location: string;
  url: string;
  snippet: string;
};

export type Msg =
  | { type: "DETECT_NOW" }
  | { type: "EXTRACT_NOW" }
  | { type: "EXTRACT_LIST" }
  | { type: "EXTRACT_LIST_ACTIVE" }
  | { type: "EXTRACT_ACTIVE_TAB" }
  | { type: "JOB_FOUND"; job: Job; detection?: any }
  | { type: "SAVE_JOB"; job: Job }
  | { type: "SAVE_LIST"; cards: ListCard[]; source_domain: string; source_url: string }
  | { type: "CRAWL_URLS"; urls: string[]; options?: { concurrency?: number; timeoutMs?: number } }
  | { type: "CANCEL_RUN"; runId: number }
  | { type: "GET_RUN_STATUS"; runId: number }
  | { type: "OPEN_SIDEPANEL" };

export type Resp =
  | { ok: true; [k: string]: any }
  | { ok: false; error?: string; reason?: string };

export function send<T extends Resp = Resp>(msg: Msg): Promise<T> {
  return new Promise((resolve) =>
    chrome.runtime.sendMessage(msg, (r: T) => resolve(r ?? ({ ok: false, error: "no-response" } as T)))
  );
}
