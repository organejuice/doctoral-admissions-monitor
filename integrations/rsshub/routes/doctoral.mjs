import { readFile } from 'node:fs/promises';

const registry = JSON.parse(await readFile(process.env.PILOT_SOURCES_PATH, 'utf8'));
const sources = Array.isArray(registry) ? registry : registry.sources;
if (!Array.isArray(sources) || !sources.length) throw new Error('The fixed pilot source allowlist is empty.');
const allowed = new Set(sources.map((source) => source.source_id));
if (allowed.size !== sources.length || [...allowed].some((id) => typeof id !== 'string' || !/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/.test(id))) {
    throw new Error('Invalid or duplicated pilot source_id.');
}
const bridge = new URL(process.env.BRIDGE_BASE_URL || 'http://127.0.0.1:8765');
if (bridge.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(bridge.hostname)) {
    throw new Error('A loopback-only bridge is required.');
}

export const namespace = { name: '博士招生官方来源试点', url: 'localhost', lang: 'zh-CN' };
export const routes = [{
    path: '/:source_id',
    name: '固定官方来源的博士招生公告',
    example: `/doctoral/${sources[0].source_id}`,
    categories: ['university'],
    maintainers: [],
    handler: async (ctx) => {
        const id = ctx.req.param('source_id');
        if (!allowed.has(id)) return new Response('Unknown fixed source_id', { status: 404 });
        const url = new URL(`/sources/${encodeURIComponent(id)}.json`, bridge);
        const response = await fetch(url, { redirect: 'error', signal: AbortSignal.timeout(120000) });
        if (!response.ok) throw new Error(`Source bridge rejected ${id}: HTTP ${response.status}`);
        const data = await response.json();
        if (data.status === 'failed' || data.success === false || typeof data.title !== 'string' || typeof data.link !== 'string' || !Array.isArray(data.item)) {
            throw new Error(`Invalid or failed source bridge result: ${id}`);
        }
        for (const item of data.item) {
            if (!item || typeof item.title !== 'string' || typeof item.link !== 'string' || !/^https?:\/\//.test(item.link)) {
                throw new Error(`Invalid announcement item in source ${id}`);
            }
        }
        return { title: data.title, link: data.link, description: data.description || '', language: 'zh-cn', item: data.item };
    },
}];
