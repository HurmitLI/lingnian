import { readFile, writeFile } from "node:fs/promises";

const serverPath = new URL("../.next/standalone/server.js", import.meta.url);
const original = await readFile(serverPath, "utf8");
const marker = '"distDir":"./.next"';
const matches = original.split(marker).length - 1;

if (matches !== 1) {
  throw new Error(`standalone server distDir marker count must be 1, received ${matches}`);
}

await writeFile(serverPath, original.replace(marker, '"distDir":"./runtime_next"'), "utf8");
