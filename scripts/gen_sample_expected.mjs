#!/usr/bin/env node
/**
 * 用前端真实实现生成跨语言采样的期望值。
 *
 * `backend/tests/fixtures/sample-expected.json` 就是这么来的：拿
 * `frontend/src/renderers/scene.ts` 的 `sampleScene` 跑同一份 fixture，
 * 把坐标/透明度/顺序写下来。Python 侧再和它逐个比对（`tests/test_sample.py`）。
 *
 * 为什么不在 CI 里跑：需要 Node + tsc，而后端测试环境只有 Python。所以期望值
 * 提交进仓库，只有改了采样语义时才重新生成——**改了 TS 就必须重跑这一步**，
 * 否则 Python 会拿旧期望值继续"通过"。
 *
 * 用法（仓库根目录）：
 *   node scripts/gen_sample_expected.mjs
 *   node scripts/gen_sample_expected.mjs --scene frontend/src/renderers/scene.ts \
 *        --cases shared/demo/sample-cases.json \
 *        --out backend/tests/fixtures/sample-expected.json
 *
 * 依赖：前端 TypeScript 转译器；实际模块图在无 DOM 的 VM 中执行，不使用 any 类型替身。
 */

import { readFileSync, writeFileSync } from 'node:fs';
import vm from 'node:vm';
import { browserModule } from '../frontend/tests/browser-modules.mjs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

function arg(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index > -1 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

const scenePath = resolve(ROOT, arg('scene', 'frontend/src/renderers/scene.ts'));
const casesPath = resolve(ROOT, arg('cases', 'shared/demo/sample-cases.json'));
const outPath = resolve(ROOT, arg('out', 'backend/tests/fixtures/sample-expected.json'));
const { sampleScene, sampleTimes } = vm.runInNewContext(await browserModule(scenePath));

const fixture = JSON.parse(readFileSync(casesPath, 'utf8'));
const results = [];
for (const item of fixture.cases) {
  const times = sampleTimes(item.ir);
  for (const timeMs of item.times) {
    const scene = sampleScene(item.ir, timeMs);
    results.push({
      id: item.id,
      timeMs: scene.timeMs,
      sampleTimes: times,
      nodes: scene.nodes.map((node) => ({
        id: node.id,
        type: node.type,
        opacity: node.opacity,
        points: node.points,
      })),
    });
  }
}

writeFileSync(outPath, JSON.stringify(results), 'utf8');
console.log(`已写 ${outPath}（${results.length} 组，${fixture.cases.length} 个 case）`);
