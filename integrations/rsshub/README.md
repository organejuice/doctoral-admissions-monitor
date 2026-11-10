# RSSHub doctoral pilot integration

This integration runs the genuine, unchanged RSSHub server and its RSS/Atom rendering middleware. Official package `rsshub@1.0.0-master.bbc351a` corresponds to commit `bbc351a29d44f58aa66cfaf4eec5a6af61205ffb`, release `v2026.11.10-8618.bbc351a`. The upstream license at this revision is **AGPL-3.0**; the original license is retained in `LICENSE.RSSHub`. Source: https://github.com/DIYgod/RSSHub/tree/bbc351a29d44f58aa66cfaf4eec5a6af61205ffb .

No upstream package files are patched. `server.mjs` invokes the official published server entry and uses its supported `USER_ROUTES_PATH` extension. `routes/doctoral.mjs` loads the fixed pilot `source_id` allowlist from `../../config/pilot_sources.json`, calls only the local collector bridge, and returns structured items to RSSHub. It does not implement an alternative RSS serializer.

The collector bridge performs source-specific official HTML/API parsing, archives the actual responses and supplies title/link/description/items. Thus this is an explicit custom RSSHub adapter backed by that collector; it is not a claim that RSSHub already has working native routes for all 26 sources. Unknown IDs return 404. A failed bridge result raises a source failure and is not converted to a normal empty feed.

## Runtime and commands

Use Node **24.22.1** (tested); this upstream revision requires `^24.15.0 || ^26.13.0`. This integration's lock file pins npm transitive dependencies. On a fresh Linux runner:

```sh
cd integrations/rsshub
npm ci --ignore-scripts --no-audit --no-fund
node test.mjs
node server.mjs
```

On Windows, use an existing Node 24.22.1 executable without changing the global Node installation. The development host reused its bundled runtime. Normal loopback/network access is required; this host's restricted sandbox failed readiness checks, while the identical test with ordinary authorized network access passed.

The bridge must be started separately by the collector CLI. Defaults:

- Bridge: `http://127.0.0.1:8765/sources/{source_id}.json`.
- RSSHub: `http://127.0.0.1:1200/doctoral/{source_id}?format=rss`.
- Override `BRIDGE_BASE_URL`, `RSSHUB_PORT`, and `PILOT_SOURCES_PATH` as needed. The bridge URL must be a credential-free loopback HTTP URL. RSSHub listens on loopback only.

Route cache is disabled for the trial so each explicit poll reaches the bridge; the collector controls whether a source has already been refreshed during a run. This causes RSSHub's normal "Cache not available" warning. It does not by itself indicate a failed feed. Per-domain fetch pacing and run deduplication are the collector's responsibility.

Installation scripts were disabled, so no browser binaries were downloaded. The development source volume ran out of space during initial project setup. The one reproducible dependency tree was moved to another local volume and linked back through the local `node_modules` junction. All 34,262 files were compared by SHA256 before removing the duplicate tree; 322,062,872 bytes were relocated. Tests passed again after the move. The exact machine paths are recorded only in the local runtime audit; the junction must not be traversed when building the software release. Fresh runners use `npm ci` normally.

## Evidence and limitations

- `test.mjs`: starts temporary real RSSHub plus a controlled bridge, verifies RSS XML/Chinese body, repeated-poll refresh, failed-source handling and unknown-ID rejection, then stops services and deletes temporary fixtures. Its fixture is explicitly not a real admissions notice. Results: `audit/runtime_integration.json` and `audit/controlled-feed.xml`.
- `probe_native_route.mjs`: actually requests the official built-in `/cags/edu/zs_bss` route and independently fetches the same institute's public JSON list. All 10 first-page official IDs matched the 10 RSS items in this trial. Original API and RSS bytes, HTTP observations and hashes are retained under `audit/native_cags/`.
- The native CAGS route supplies introductions and only its configured first page. Its successful RSS response does not certify full text, attachments or historical coverage. The collector's full-text/API and pagination checks remain necessary.
- `audit_upstream_routes.mjs` records a discovery inventory, not source acceptance. For example, `/pku/admission/sszs` reads a master's column, and `/ucas/job` is recruitment/postdoc material; neither may count as doctoral-admissions coverage just because a university name or the word 博士 appears.
- `audit/upstream_runtime_provenance.json` records package integrity, commit, license, local runtime relocation and post-relocation validation. No cloud resource, paid service, credentials or external notification was configured by this integration.

Temporary servers must be stopped after the authorized pilot run. The parent collector run maintains the real 26-source observations and must report its unresolved failures separately.
