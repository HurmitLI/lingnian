#!/bin/sh
set -eu

npm run build

cp -R public .next/standalone/public
chmod -R a+rX .next/standalone/public
mkdir -p .next/standalone/.next
cp -R .next/static .next/standalone/.next/static
mv .next/standalone/node_modules .next/standalone/runtime_deps
mv .next/standalone/.next .next/standalone/runtime_next
node scripts/patch_standalone_server.mjs

test -f .next/standalone/server.js
test -f .next/standalone/public/showcase/shen-suqin-story.mp4
test -f .next/standalone/runtime_next/BUILD_ID
test -d .next/standalone/runtime_next/static
test -f .next/standalone/runtime_deps/next/package.json
grep -q '"distDir":"./runtime_next"' .next/standalone/server.js
