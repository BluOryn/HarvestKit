export function clean(s: any): string {
  if (s == null) return "";
  return String(s).replace(/\s+/g, " ").trim();
}

export function htmlToText(html: string): string {
  if (!html) return "";
  const tmp = document.createElement("div");
  tmp.innerHTML = html;
  return clean(tmp.textContent || "");
}

export function safeJSON(s: string): any {
  if (!s) return null;
  try { return JSON.parse(s); } catch { return null; }
}

export function flatten(value: any): any[] {
  if (value == null) return [];
  if (Array.isArray(value)) return value.flatMap(flatten);
  if (typeof value === "object") {
    const graph = (value as any)["@graph"];
    if (graph) return flatten(graph);
    return [value];
  }
  return [value];
}

export function uniq<T>(arr: T[]): T[] { return [...new Set(arr.filter(Boolean) as any)] as T[]; }

export function getText(el: Element | Document | null | undefined): string {
  if (!el) return "";
  const anyEl = el as any;
  return (anyEl.innerText && anyEl.innerText.length > 0 ? anyEl.innerText : anyEl.textContent) || "";
}
