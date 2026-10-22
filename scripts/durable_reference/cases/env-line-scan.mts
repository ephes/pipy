// Pinned LineScanner oracle: no filesystem adapter, text projection or normalization.
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
const loaded: string[] = [];
const allowed = new Set(["./packages/durable/src/env/line-scan.ts", "./packages/durable/src/env/decode.ts"].map(p => new URL(p, import.meta.url).href));
registerHooks({load(url, context, nextLoad) {
 if (!allowed.has(url)) throw new Error("Unexpected scanner dependency: " + url);
 loaded.push(url);
 return nextLoad(url, context);
}});
const { LineScanner } = await import("./packages/durable/src/env/line-scan.ts");
const input = JSON.parse(readFileSync(process.argv[2]!, "utf8"));
const output = input.map((recipe: {hex: string; start: number; end: number | null; chunks: number[]}) => {
 const bytes = Buffer.from(recipe.hex, "hex");
 const scanner = new LineScanner(recipe.start, recipe.end ?? undefined);
 let offset = 0;
 for (const size of recipe.chunks) {
   scanner.push(bytes.subarray(offset, offset + size));
   offset += size;
 }
 if (offset !== bytes.length) throw new Error("incomplete chunk recipe");
 const r = scanner.finish();
 return {newlines:r.newlines, start:r.start, end:r.end, first_line_end:r.firstLineEnd,
 last_line_start:r.lastLineStart, selected_bytes:r.selectedBytes, first_line_bytes:r.firstLineBytes};
});
process.stdout.write(JSON.stringify({observed: output, loaded}));
