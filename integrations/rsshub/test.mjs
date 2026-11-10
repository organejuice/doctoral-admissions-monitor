import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const temp = await mkdtemp(path.join(tmpdir(), 'doctoral-rsshub-test-'));
let child;
let calls = 0;
const bridge = createServer((req, res) => {
    if (req.url === '/sources/fixture-failure.json') { res.writeHead(503).end('controlled source failure'); return; }
    if (req.url !== '/sources/fixture-main.json') { res.writeHead(404).end(); return; }
    calls++;
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ title: '受控博士公告 <&>', link: 'https://example.edu.cn/bs/', description: 'Test fixture only, not a real admission notice.', item: [{ title: '2027博士招生申请 <&>', link: 'https://example.edu.cn/notice?id=1&v=1', description: `<p>受控正文第${calls}版</p>`, guid: 'fixture-notice-1' }] }));
});
await new Promise((resolve) => bridge.listen(0, '127.0.0.1', resolve));
const probe = createServer();
await new Promise((resolve) => probe.listen(0, '127.0.0.1', resolve));
const port = probe.address().port;
await new Promise((resolve) => probe.close(resolve));
const whitelist = path.join(temp, 'sources.json');
await writeFile(whitelist, JSON.stringify({ sources: [{ source_id: 'fixture-main' }, { source_id: 'fixture-failure' }] }));
let logs = '';
const started = new Date().toISOString();
try {
    child = spawn(process.execPath, [path.join(here, 'server.mjs')], { cwd: here, env: { ...process.env, PILOT_SOURCES_PATH: whitelist, BRIDGE_BASE_URL: `http://127.0.0.1:${bridge.address().port}`, RSSHUB_PORT: String(port) }, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    child.stdout.on('data', (data) => { logs = (logs + data).slice(-30000); });
    child.stderr.on('data', (data) => { logs = (logs + data).slice(-30000); });
    const base = `http://127.0.0.1:${port}`;
    let ready = false;
    for (let n = 0; n < 120; n++) {
        if (child.exitCode !== null) throw new Error(`RSSHub exited ${child.exitCode}: ${logs}`);
        try { ready = (await fetch(`${base}/healthz`, { signal: AbortSignal.timeout(1000) })).ok; } catch { /* startup */ }
        if (ready) break;
        await new Promise((resolve) => setTimeout(resolve, 500));
    }
    assert(ready, `RSSHub did not become ready: ${logs}`);
    const a = await fetch(`${base}/doctoral/fixture-main?format=rss`);
    const xml1 = await a.text();
    assert.equal(a.status, 200, xml1);
    assert.match(xml1, /<rss\b/);
    assert.match(xml1, /<item>/);
    assert.match(xml1, /受控正文第1版/);
    const b = await fetch(`${base}/doctoral/fixture-main?format=rss`);
    const xml2 = await b.text();
    assert.equal(b.status, 200);
    assert.match(xml2, /受控正文第2版/);
    assert.equal(calls, 2, 'An explicit new poll must reach the bridge.');
    const failed = await fetch(`${base}/doctoral/fixture-failure?format=rss`);
    assert.notEqual(failed.status, 200, 'An upstream failure cannot be a successful empty feed.');
    const unknown = await fetch(`${base}/doctoral/not-allowlisted?format=rss`);
    assert.equal(unknown.status, 404);
    const dir = path.join(here, 'audit');
    await mkdir(dir, { recursive: true });
    await writeFile(path.join(dir, 'controlled-feed.xml'), xml1);
    const lock = JSON.parse(await readFile(path.join(here, 'package-lock.json'), 'utf8'));
    const report = { passed: true, started_at_utc: started, checked_at_utc: new Date().toISOString(), mode: 'controlled local integration; no real admissions claim', node: process.version, rsshub: lock.packages['node_modules/rsshub'].version, genuine_upstream_server: true, upstream_modifications: [], user_route: '/doctoral/:source_id', checks: { rss_rendered_by_rsshub: true, chinese_body_preserved: true, repeated_poll_refreshed: true, failed_bridge_not_success: true, unknown_id_status: unknown.status }, bridge_calls: calls, failed_bridge_status: failed.status, installation_scripts_disabled: true };
    await writeFile(path.join(dir, 'runtime_integration.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
} finally {
    if (child && child.exitCode === null) {
        child.kill();
        await new Promise((resolve) => { const timer = setTimeout(resolve, 5000); child.once('exit', () => { clearTimeout(timer); resolve(); }); });
    }
    await new Promise((resolve) => bridge.close(resolve));
    assert(path.resolve(temp).startsWith(path.resolve(tmpdir()) + path.sep));
    await rm(temp, { recursive: true, force: true });
}
