import * as greenhouse from "./greenhouse.js";
import * as lever from "./lever.js";
import * as ashby from "./ashby.js";
import * as workday from "./workday.js";
import * as personio from "./personio.js";
import * as smartrecruiters from "./smartrecruiters.js";
import * as linkedin from "./linkedin.js";

export const ADAPTERS = [greenhouse, lever, ashby, workday, personio, smartrecruiters, linkedin];

export function pickAdapter() {
  for (const a of ADAPTERS) {
    try {
      if (a.isMatch && a.isMatch()) return a;
    } catch {}
  }
  return null;
}
