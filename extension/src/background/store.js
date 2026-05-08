// IndexedDB job store. No external deps; small wrapper over IDBDatabase.
const DB_NAME = "jobharvester";
const STORE = "jobs";
const VERSION = 1;

function open() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const os = db.createObjectStore(STORE, { keyPath: "fingerprint" });
        os.createIndex("source_domain", "source_domain", { unique: false });
        os.createIndex("source_ats", "source_ats", { unique: false });
        os.createIndex("scraped_at", "scraped_at", { unique: false });
        os.createIndex("title", "title", { unique: false });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function fingerprint(job) {
  const parts = [
    (job.apply_url || job.job_url || "").toLowerCase(),
    (job.title || "").toLowerCase(),
    (job.company || "").toLowerCase(),
    (job.location || "").toLowerCase(),
  ];
  return parts.join("|");
}

export async function putJob(job) {
  const db = await open();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    const os = tx.objectStore(STORE);
    job.fingerprint = fingerprint(job);
    job.scraped_at = job.scraped_at || new Date().toISOString();
    const req = os.put(job);
    req.onsuccess = () => resolve(job);
    req.onerror = () => reject(req.error);
  });
}

export async function listJobs() {
  const db = await open();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readonly");
    const req = tx.objectStore(STORE).getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
}

export async function clearJobs() {
  const db = await open();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    const req = tx.objectStore(STORE).clear();
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

export async function deleteJob(fingerprintKey) {
  const db = await open();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    const req = tx.objectStore(STORE).delete(fingerprintKey);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

export async function countJobs() {
  const db = await open();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readonly");
    const req = tx.objectStore(STORE).count();
    req.onsuccess = () => resolve(req.result || 0);
    req.onerror = () => reject(req.error);
  });
}
