// JobHarvester build: bundles content/background/sidepanel via esbuild.
import { build, context } from "esbuild";
import { mkdirSync, copyFileSync, writeFileSync, readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

mkdirSync("dist", { recursive: true });

const watch = process.argv.includes("--watch");

// Tailwind-like utility CSS authored manually (no external runtime).
const baseCss = readFileSync("app/src/styles/base.css", "utf8");
writeFileSync("dist/sidepanel.css", baseCss);

const sidepanelHtml = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>JobHarvester</title>
<link rel="stylesheet" href="sidepanel.css">
</head><body><div id="root"></div><script type="module" src="sidepanel.js"></script></body></html>`;
writeFileSync("dist/sidepanel.html", sidepanelHtml);

const common = {
  bundle: true,
  format: "esm",
  target: ["chrome120"],
  logLevel: "info",
  legalComments: "none",
  sourcemap: false,
  jsx: "automatic",
  loader: { ".js": "jsx", ".ts": "ts", ".tsx": "tsx" },
  define: { "process.env.NODE_ENV": '"production"' },
};

const targets = [
  {
    entryPoints: ["app/src/content/inject.tsx"],
    outfile: "dist/content.js",
    format: "iife",
    ...common,
  },
  {
    entryPoints: ["app/src/background/main.ts"],
    outfile: "dist/background.js",
    ...common,
  },
  {
    entryPoints: ["app/src/sidepanel/main.tsx"],
    outfile: "dist/sidepanel.js",
    format: "esm",
    ...common,
  },
];

if (watch) {
  for (const t of targets) {
    const ctx = await context(t);
    await ctx.watch();
  }
  console.log("watching…");
} else {
  await Promise.all(targets.map((t) => build(t)));
  console.log("build complete");
}
