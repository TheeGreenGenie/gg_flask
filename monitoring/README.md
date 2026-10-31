# Blue-team monitoring checks

The suite has two deliberately small layers appropriate to the store's current traffic:

1. `security_probe.py` independently checks the deployed Node-to-Flask boundary without credentials.
2. The Flask application counts privileged sign-in failures, includes them in the daily owner
   summary, sends a one-hour-deduplicated alert after a correct admin password followed by three
   wrong codes in 15 minutes, and clears non-consent IP/browser details after 90 days.

The release probe does not import the application, open its environment file, authenticate, or
submit real commerce data.

It checks:

- homepage availability or the intentional pre-launch preview response;
- liveness and public readiness without dependency-detail leakage;
- CSP, HSTS, clickjacking, MIME, referrer and permissions headers;
- the unauthenticated admin boundary plus `no-store`/`noindex` on its login page;
- rejection of unsigned Stripe and PayPal webhook bodies;
- denial of common secret, repository, source, log and backup paths;
- disabled TRACE; and
- public server/framework banners.

Run it only against a host you own or are authorized to test:

```powershell
python -m monitoring.security_probe https://ggafrikaneleganz.com --expect-preview --output instance/security-probe.json
```

After the preview password is removed, omit `--expect-preview`. Exit `0` means no finding, `1`
means warnings, and `2` means a failed security/availability expectation. The JSON evidence contains
HTTP status, selected headers, captured-response length/hash and a truncation flag, never the
response body.

This is not a vulnerability scanner and does not replace provider reconciliation, log shipping,
alert acknowledgement, restore testing, incident runbooks, or an independent penetration test.

## Local regression command

Use the isolated runner so this suite never shares or rebuilds another developer's `gg_test`
schema:

```powershell
.\.venv\Scripts\python.exe scripts\run_isolated_tests.py --label blue-team -- -q tests/test_security_events.py tests/test_security_probe.py tests/test_isolated_test_runner.py
```

Current evidence (2026-10-30): 19 focused tests passed, including the four controls Claude added and
a counter-outage regression. Five existing daily-summary/job/ntfy interaction tests also passed. No
production database, live key, or environment file is needed.

Traffic-exhaustion controls and owner response steps are in
[DDOS_RUNBOOK.md](DDOS_RUNBOOK.md). The Node wrapper has a tested global/per-IP admission gate, but
volumetric DDoS protection remains SiteGround's upstream responsibility.

## Right-sized operating baseline

Before launch, the remaining owner actions are to complete the spare-database restore drill,
configure UptimeRobot, schedule the external probe with an owned alert destination, and verify one
real low-value payment/refund after live-key cutover. Central telemetry, tamper-evident audit storage,
SBOM/hash enforcement, enterprise dashboards, and MCP analyst automation are deferred unless
traffic, staffing, compliance requirements, or an incident justifies their cost.
