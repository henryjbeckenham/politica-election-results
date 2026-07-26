import {cp, mkdir, rm} from "node:fs/promises";

const source = new URL("../dist", import.meta.url);
const destination = new URL("../../src/politica_erd/app/results", import.meta.url);
const governedData = new URL("../src/data", import.meta.url);
const builtData = new URL("../dist/data", import.meta.url);

// These governed files are fetched by the compiled application at runtime.
// Observable cannot discover their string-built URLs, so copy them explicitly
// into both the host-ready build and the operator's embedded preview.
await mkdir(builtData, {recursive: true});
await cp(governedData, builtData, {recursive: true});

await rm(destination, {recursive: true, force: true});
await mkdir(destination, {recursive: true});
await cp(source, destination, {recursive: true});
