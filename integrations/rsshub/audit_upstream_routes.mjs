import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url));
const dist = fileURLToPath(new URL('./', import.meta.resolve('rsshub')));
const candidates = [];
for (const file of await readdir(dist)) {
    if (!file.endsWith('.mjs') || file.startsWith('routes-')) continue;
    const code = await readFile(path.join(dist, file), 'utf8');
    const original = code.match(/\/\/ (lib\/routes\/[^\r\n]+)/)?.[1];
    if (!original || !/博士|研究生招生|招生信息|招生通知/.test(code)) continue;
    const example = code.match(/example:\s*"([^"]+)"/)?.[1];
    const name = code.match(/\n\s*name:\s*"([^"]+)"/)?.[1];
    if (!example || !name) continue;
    candidates.push({ upstream_file: original, distributed_file: file, example, name,
        contains_doctoral_word: code.includes('博士'),
        official_urls: [...new Set([...code.matchAll(/https?:\/\/[^\s"'`<>]+/g)].map((m) => m[0]))].slice(0, 8),
        scope_status: /博士后|招聘/.test(name) ? 'not_doctoral_admissions' : 'candidate_requires_official_column_comparison' });
}
const lock = JSON.parse(await readFile(path.join(here, 'package-lock.json'), 'utf8'));
const report = { checked_at_utc: new Date().toISOString(), package: lock.packages['node_modules/rsshub'].version,
    commit: 'bbc351a29d44f58aa66cfaf4eec5a6af61205ffb',
    method: 'Read route metadata and source references from the official published package; a university name or doctoral word is not coverage approval.',
    candidates, caveat: 'This is a route discovery inventory, not a claim that any candidate route currently works or covers all doctoral notices.' };
await mkdir(path.join(here, 'audit'), { recursive: true });
await writeFile(path.join(here, 'audit', 'upstream_route_inventory.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify({ candidates: candidates.length, examples: candidates.map((x) => ({ example: x.example, name: x.name, source: x.upstream_file })) }));
