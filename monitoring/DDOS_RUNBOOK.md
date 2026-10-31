# DDoS and abusive-traffic runbook

No application can guarantee that a distributed denial-of-service attack will never cause an
outage. Volumetric filtering must happen before traffic reaches the hosting account. SiteGround
states that every plan includes network-level DDoS protection, hardware/software firewalls,
connection limits and anti-bot filtering. CDN Premium adds an optional **Under Attack Mode**.

Official references:

- [SiteGround DDoS protection](https://www.siteground.com/kb/does_siteground_offer_any_protection_from_my_site_getting_dd/)
- [SiteGround hosting security and Under Attack Mode](https://www.siteground.com/academy/secure-web-hosting-services/)

## Layers already in place

1. SiteGround filters floods and known abusive traffic before the application.
2. The Node wrapper bounds headers, body size, request duration, upstream duration, keep-alive,
   cold-start waiters and active work admitted to Python.
3. At most 32 requests may occupy or queue for Gunicorn, and one visitor IP may hold at most 12.
   Excess work receives `503`, `Retry-After: 5`, and a closed connection.
4. Gunicorn has two workers, 30-second worker timeouts and worker recycling.
5. Flask uses shared PostgreSQL/Redis limits for login, approval, email, contact, review, wishlist,
   newsletter and account operations. High-risk paths fail closed if the limiter is unavailable.
6. UptimeRobot and the external security probe provide off-host outage detection once scheduled.

Do not add a database-backed rate-limit write to every public GET. During a flood that would move
the bottleneck into PostgreSQL and could make checkout and administration fail sooner.

## Before launch

- Configure UptimeRobot for the homepage and readiness endpoint with owner notifications.
- Schedule the credential-free release probe from outside SiteGround.
- Keep SiteGround support access and the Site Tools traffic/security pages available to the owner.
- Decide whether CDN Premium is worth buying only after actual abusive traffic or an availability
  requirement justifies it; the included protection is the baseline for current traffic.
- Do not run a production load/DDoS test. Any staging load test needs SiteGround authorization and
  conservative limits.

## Detection and triage

Treat repeated external failures, SiteGround resource warnings, or sustained wrapper `503`/`502`/
`504` responses as an availability incident.

1. Check SiteGround's status page and control panel before assuming an attack.
2. Check UptimeRobot from more than one location and the public release probe.
3. Check bounded wrapper/Python logs for startup loops, database/provider failure, or `shop is busy`
   responses. Do not copy raw IPs, private URLs, tokens or customer data into chat/tickets.
4. Use SiteGround's traffic/blocked-traffic view to distinguish a traffic flood from a deployment,
   database, payment-provider or process-lifecycle failure.

## Containment

1. For a volumetric or distributed flood, contact SiteGround support. Application code cannot absorb
   traffic that has already saturated the network or shared-host resource allowance.
2. If CDN Premium is active, SiteGround documents **Under Attack Mode** as a temporary challenge
   mode that turns itself off after 24 hours. Before enabling it, ask SiteGround whether Stripe and
   PayPal webhook paths can bypass the challenge. If they cannot, pause checkout or expect delayed
   webhooks and reconcile every pending payment afterward. Never weaken webhook signatures.
3. Block a source or country only from SiteGround's edge tools and only with evidence that legitimate
   customers will not be excluded. Do not maintain a large emergency `.htaccess` IP list.
4. Do not raise Node admission limits, Gunicorn workers, database pool size or timeouts during the
   incident; that increases resource exhaustion on the shared account.
5. Do not automatically refund, cancel orders, rotate credentials or change payment state merely
   because the site is unavailable.

## Recovery

1. Confirm homepage, readiness, account login, cart and checkout behavior.
2. Reconcile Stripe/PayPal webhooks, pending payments, refunds, stock holds and paid orders covering
   the incident window.
3. Confirm daily jobs, backup heartbeat, email and ntfy delivery.
4. Save aggregate timing/status evidence and SiteGround's incident/support reference; avoid raw
   visitor data unless it is necessary for an active investigation.
5. If attacks repeat or the included edge cannot keep the store available, evaluate CDN Premium or
   a hosting/edge upgrade. Do not try to solve recurring volumetric attacks inside Flask.
