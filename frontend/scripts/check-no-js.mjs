// Guard: fail the build if any legacy .js / .jsx files exist in src/.
// The frontend is a TypeScript project (main.tsx -> App.tsx -> api.ts).
// Stray .js files are migration leftovers and must not be reintroduced.
import { readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const SRC_DIR = join(fileURLToPath(new URL(".", import.meta.url)), "..", "src");

const OFFENDING = [];

function walk(dir) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      walk(full);
    } else if (/\.jsx?$/.test(entry)) {
      OFFENDING.push(relative(SRC_DIR, full));
    }
  }
}

walk(SRC_DIR);

if (OFFENDING.length > 0) {
  console.error("❌ Found legacy .js/.jsx files in src/ (use .ts/.tsx instead):");
  for (const f of OFFENDING) console.error("   - " + f);
  console.error("\nDelete these files or rename them to .ts/.tsx and re-type them.");
  process.exit(1);
}

console.log("✅ No legacy .js/.jsx files in src/.");
