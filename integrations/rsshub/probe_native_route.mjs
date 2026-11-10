import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import Parser from 'rss-parser';

const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.join(here, 'audit', 'native_cags');
await mkdir(path.join(out, 'raw'), { recursive: true });
const probe = createServer();
await new Promise((resolve) => probe.listen(0, '127.0.0.1', resolve));
const port = probe.address().port;
await new Promise((resolve) => probe.close(resolve));
const child = spawn(process.execPath, [path.join(here, 'server.mjs')], { cwd: here, env: { ...process.env, RSSHUB_PORT: String(port) }, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
let logs = '';
child.stdout.on('data', (data) => { logs = (logs + data).slice(-20000); });
child.stderr.on('data', (data) => { logs = (logs + data).slice(-20000); });
const observations = [];
async function capture(url, file, headers) {
    const response = await fetch(url, { headers, signal: AbortSignal.timeout(120000) });
    const bytes = Buffer.from(await response.arrayBuffer());
    const meta = { requested_url: url, final_url: response.url, status: response.status, content_type: response.headers.get('content-type'), checked_at_utc: new Date().toISOString(), bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'), raw_file: `raw/${file}` };
    await writeFile(path.join(out, 'raw', file), bytes);
    await writeFile(path.join(out, 'raw', file + '.meta.json'), JSON.stringify(meta, null, 2));
    observations.push(meta);
    return { response, bytes };
}
let result;
try {
    let ready = false;
    for (let n = 0; n < 60; n++) {
        if (child.exitCode !== null) throw new Error(`RSSHub exited ${child.exitCode}: ${logs}`);
        try { ready = (await fetch(`http://127.0.0.1:${port}/healthz`, { signal: AbortSignal.timeout(1000) })).ok; } catch { /* startup */ }
        if (ready) break;
        await new Promise((resolve) => setTimeout(resolve, 500));
    }
    assert(ready, logs);
    const officialUrl = 'https://edu.cags.ac.cn/api/cms/cmsNews/pageByCmsNavBarId/zs_bss/1/10/0';
    const official = await capture(officialUrl, 'official-list.body', { Accept: 'application/json' });
    assert(official.response.ok, `Official API HTTP${official.response.status}`);
    const data = JSON.parse(official.bytes.toString('utf8')).data;
    assert(Array.isArray(data) && data.length > 0);
    const native = await capture(`http://127.0.0.1:${port}/cags/edu/zs_bss?format=rss`, 'rsshub-feed.xml');
    assert(native.response.ok, `Native RSSHub route HTTP${native.response.status}`);
    const feed = await new Parser().parseString(native.bytes.toString('utf8'));
    const ids = data.map((item) => String(item.id));
    const missing = ids.filter((id) => !feed.items.some((item) => item.link?.includes(`id=${id}/`)));
    assert.equal(missing.length, 0, `Official first-page IDs missing from native feed: ${missing}`);
    result = { passed: true, route: '/cags/edu/zs_bss', upstream_route: 'lib/routes/cags/edu/index.ts', official_list_api: officialUrl, official_items: data.length, feed_items: feed.items.length, all_firstpage_official_ids_in_feed: true, source_type: 'official public JSON API', archive_scope: 'This probe retained the official first-page JSON response and the genuine RSSHub RSS bytes. Feed descriptions are introduction summaries; full notice bodies and attachments are not certified by this probe.', history_complete: false, fulltext_complete: false, observations };
} catch (error) {
    result = { passed: false, route: '/cags/edu/zs_bss', error: String(error), history_complete: false, fulltext_complete: false, observations, logs };
} finally {
    if (child.exitCode === null) {
        child.kill();
        await new Promise((resolve) => { const timer = setTimeout(resolve, 5000); child.once('exit', () => { clearTimeout(timer); resolve(); }); });
    }
}
result.checked_at_utc = new Date().toISOString();
await writeFile(path.join(out, 'result.json'), JSON.stringify(result, null, 2));
console.log(JSON.stringify({ passed: result.passed, route: result.route, official_items: result.official_items, feed_items: result.feed_items, error: result.error }));
if (!result.passed) process.exitCode = 1;
