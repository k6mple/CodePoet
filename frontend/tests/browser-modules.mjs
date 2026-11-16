// Bundle the local TypeScript module graph for real-browser checks, without a second build tool.
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

export async function browserModule(entry) {
  const files = new Map()
  const visit = async (filename) => {
    filename = path.resolve(filename)
    if (files.has(filename)) return
    const source = await readFile(filename, 'utf8')
    const code = ts.transpileModule(source, { compilerOptions: {
      target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
    } }).outputText
    const item = { code, imports: {} }
    files.set(filename, item)
    for (const match of code.matchAll(/require\("([^"]+)"\)/g)) {
      if (!match[1].startsWith('.') && !match[1].startsWith('@/')) throw new Error(`Browser check cannot load ${match[1]}`)
      const dependency = match[1].startsWith('@/')
        ? fileURLToPath(new URL('../src/' + match[1].slice(2) + '.ts', import.meta.url))
        : path.resolve(path.dirname(filename), match[1] + '.ts')
      item.imports[match[1]] = dependency
      await visit(dependency)
    }
  }
  const filename = entry instanceof URL ? fileURLToPath(entry) : path.resolve(entry)
  await visit(filename)
  return `(() => {
    const modules = {${[...files].map(([id, item]) => `${JSON.stringify(id)}: {
      imports: ${JSON.stringify(item.imports)}, run: (module, exports, require) => {\n${item.code}\n}
    }`).join(',')}};
    const cache = {};
    function load(id) {
      if (cache[id]) return cache[id].exports;
      const module = cache[id] = { exports: {} };
      const item = modules[id];
      item.run(module, module.exports, name => load(item.imports[name]));
      return module.exports;
    }
    return load(${JSON.stringify(filename)});
  })()`
}
