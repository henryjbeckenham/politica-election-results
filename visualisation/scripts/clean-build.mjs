import {rm} from "node:fs/promises";

await Promise.all([
  rm(new URL("../dist", import.meta.url), {recursive: true, force: true}),
  rm(new URL("../src/.observablehq", import.meta.url), {recursive: true, force: true})
]);

// Observable can begin its next process before an APFS/overlay-backed directory
// removal is fully visible. A short boundary prevents stale generated assets
// from being reported as output conflicts during immediate repeat builds.
await new Promise((resolve) => setTimeout(resolve, 1500));
