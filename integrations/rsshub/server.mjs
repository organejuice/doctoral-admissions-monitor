import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const bridge = new URL(process.env.BRIDGE_BASE_URL || 'http://127.0.0.1:8765');
if (bridge.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(bridge.hostname) || bridge.username || bridge.password) {
    throw new Error('The pilot bridge must be a credential-free loopback HTTP URL.');
}
process.env.BRIDGE_BASE_URL = bridge.origin;
process.env.PILOT_SOURCES_PATH ||= path.join(root, 'config', 'pilot_sources.json');
process.env.PORT = process.env.RSSHUB_PORT || '1200';
process.env.NODE_ENV = 'production';
process.env.USER_ROUTES_PATH = path.join(here, 'routes');
process.env.LISTEN_INADDR_ANY = 'false';
process.env.ENABLE_CLUSTER = 'false';
process.env.IS_PACKAGE = '';
// An explicit polling run must reach the bridge; a cached previous feed is not a new source check.
process.env.CACHE_TYPE = '';
process.env.LOGGER_LEVEL ||= 'warn';
process.env.DISALLOW_ROBOT ||= 'true';
process.env.ALLOW_USER_SUPPLY_UNSAFE_DOMAIN = 'false';
process.env.ALLOW_USER_HOTLINK_TEMPLATE = 'false';

// RSSHub's published server bundle invokes its unchanged app, middleware and RSS renderer.
// The package's /server export is a Magpie plugin, so invoke its sibling server entry instead.
const packageEntry = new URL(import.meta.resolve('rsshub'));
await import(new URL('./magpie-server.mjs', packageEntry));
