import Dexie, { type Table } from "dexie";
import type { Job } from "./schema";
import type { GeneralRecord } from "./generalSchema";

export interface Run {
  id?: number;
  started_at: number;
  finished_at?: number;
  source_url: string;
  total: number;
  done: number;
  ok: number;
  failed: number;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  type: "single" | "list-snapshot" | "deep-crawl" | "bulk" | "general-scrape";
  log?: string;
  mode?: "jobs" | "general";
}

export interface Failure {
  id?: number;
  run_id: number;
  url: string;
  reason: string;
  attempts: number;
  last_attempt_at: number;
  resolved: 0 | 1;
}

export interface SavedSearch {
  id?: number;
  name: string;
  filters: Record<string, any>;
  created_at: number;
}

export interface Setting {
  key: string;
  value: any;
}

class JobHarvesterDB extends Dexie {
  jobs!: Table<Job, string>;
  records!: Table<GeneralRecord, string>;
  runs!: Table<Run, number>;
  failures!: Table<Failure, number>;
  searches!: Table<SavedSearch, number>;
  settings!: Table<Setting, string>;

  constructor() {
    super("jobharvester");
    this.version(1).stores({
      jobs: "id, source_domain, source_ats, scraped_at, company, title, country, remote_type, *tags",
      runs: "++id, started_at, status, type",
      searches: "++id, name, created_at",
      settings: "key",
    });
    // v2 — failure log + run.mode for general-scrape support
    this.version(2).stores({
      jobs: "id, source_domain, source_ats, scraped_at, company, title, country, remote_type, *tags",
      runs: "++id, started_at, status, type, mode",
      failures: "++id, run_id, url, resolved, last_attempt_at",
      searches: "++id, name, created_at",
      settings: "key",
    });
    // v3 — general-purpose records table (LocalBusiness/Restaurant/Place)
    this.version(3).stores({
      jobs: "id, source_domain, source_ats, scraped_at, company, title, country, remote_type, *tags",
      records: "id, source_domain, scraped_at, name, city, country",
      runs: "++id, started_at, status, type, mode",
      failures: "++id, run_id, url, resolved, last_attempt_at",
      searches: "++id, name, created_at",
      settings: "key",
    });
  }
}

export const db = new JobHarvesterDB();

export async function getSetting<T = any>(key: string, defaultValue: T): Promise<T> {
  const row = await db.settings.get(key);
  return (row?.value ?? defaultValue) as T;
}

export async function setSetting(key: string, value: any) {
  await db.settings.put({ key, value });
}
