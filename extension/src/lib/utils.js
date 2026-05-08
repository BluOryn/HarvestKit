export function clean(s) {
  if (s == null) return "";
  return String(s).replace(/\s+/g, " ").trim();
}

export function htmlToText(html) {
  if (!html) return "";
  const tmp = document.createElement("div");
  tmp.innerHTML = html;
  return clean(tmp.textContent || "");
}

export function pickLongest(...vals) {
  let best = "";
  for (const v of vals) {
    const s = clean(v);
    if (s.length > best.length) best = s;
  }
  return best;
}

export function uniq(arr) {
  return [...new Set(arr.filter(Boolean))];
}

export function safeJSON(s) {
  if (!s) return null;
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}

export function flatten(value) {
  if (value == null) return [];
  if (Array.isArray(value)) return value.flatMap(flatten);
  if (typeof value === "object") {
    const graph = value["@graph"];
    if (graph) return flatten(graph);
    return [value];
  }
  return [value];
}

export function fingerprint(job) {
  const parts = [
    (job.apply_url || job.job_url || location.href),
    (job.title || "").toLowerCase(),
    (job.company || "").toLowerCase(),
    (job.location || "").toLowerCase(),
  ];
  return parts.join("|");
}
