# Device/catalog implementation ledger

Plan: 2026-10-21-device-catalog-plan.md. User approved design/plan and Native. No commits/push, no real tools/call or PIG changes.

Current: v3 CatalogStore, services/devices APIs, schema-aware publication, independent-detail hash UI, pinned network transport and official HTTP/SSE adapters implemented. 68 unittest checks passed; node --check and compose config passed. Counts include inherited historical test cases, not 68 independent new scenarios.

Network finding: installed MCP SDK can follow redirects even when HTTPX follow_redirects=false. Added failing transport test then blocked3xx at GuardedTransport; test GREEN. Real SDK initialize/list_tools through synthetic Streamable HTTP passed; actual legacy SSE roundtrip still to cover.

Fresh reviewer780f2f62 completed. Important migration/legacy bypass/cancel/UI/legacyAPI findings fixed with regressions. Runtime v3 deployed after config/image backup;74 checks passed, HTTP/SSE official SDK synthetic discovery passed, actual guarded Higress list_tools passed. Page/static200, anonymous devices/services401. Live migration preserves2 devices4 services8 grants; config0600 and v2 backup present. Browser authenticated workflow and true two-WSS connected status not independently verified. Need validate resource cancellation/partial startup, full discovery snapshots, public URL redaction, UI input preservation, migration legacy hashes, and v3 Docker startup. No real second WSS/platform supplied, so future true multi-device acceptance remains Not run.

Ruling: v3 isolated CatalogStore reuses existing atomic writer rather than mutate v2 compatibility implementation further. Runtime switches at assembly; legacy tests remain to catch prior regressions. Cost: two config validators during migration; old global/endpoints APIs must not remain callable against v3 (review/fix before release).
