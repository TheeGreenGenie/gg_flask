#!/bin/sh
# Stage 1 launch check (security and hosting), run on SiteGround over SSH:
#   sh ~/.ggflask/current/deploy/siteground/launch-check.sh [domain]
# Read-only. Prints PASS / FAIL / WARN per item and never prints a secret value.
set -u
D="${1:-ggafrikaneleganz.com}"
G="${GG_HOME:-$HOME/.ggflask}"
PY="$G/venv/bin/python"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
P=0; F=0; W=0
pass() { P=$((P + 1)); echo "PASS  $*"; }
fail() { F=$((F + 1)); echo "FAIL  $*"; }
warn() { W=$((W + 1)); echo "WARN  $*"; }
s() { echo; echo "== $*"; }
# get URL -> status in $CODE, headers in $TMP/h, body in $TMP/b
get() { CODE=$(curl -s -m 15 -D "$TMP/h" -o "$TMP/b" -w '%{http_code}' "$@"); }
hdr() { grep -i "^$1:" "$TMP/h" | tail -1 | cut -d: -f2- | tr -d '\r' | sed 's/^ *//'; }
setting() { "$PY" - "$G/env" "$1" <<'PY'
import re, sys
val = ""
for raw in open(sys.argv[1], encoding="utf-8"):
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line: continue
    k, v = line.split("=", 1)
    if k.strip() != sys.argv[2]: continue
    v = v.strip()
    if v and v[0] in "\"'" and v.find(v[0], 1) > 0: v = v[1:v.find(v[0], 1)]
    elif v.startswith("#"): v = ""
    else: v = re.split(r"\s#", v)[0].strip()
    val = v
print(val)
PY
}

s "1. private files must never be downloadable"
DEPLOY=$(readlink "$G/current" | sed 's#.*/public_html##')
for path in /.env /.git/HEAD /.git/config /package.json /package-lock.json /requirements.lock \
    /wsgi.py /app/config.py /deploy/siteground/server.js /config/studio_users.json \
    /migrations/env.py /.nodeapp/ "$DEPLOY/package.json" "$DEPLOY/app/config.py" "$DEPLOY/wsgi.py"; do
    get "https://$D$path"
    if [ "$CODE" = 200 ] && grep -qE 'SECRET_KEY|DATABASE_URL|\[core\]|^ref:|gg-afrikan-eleganz|Flask==|create_app|class BaseConfig|createWrapper|"users"|run_migrations|Index of' "$TMP/b"; then
        fail "$path is downloadable (HTTP 200 with its contents)"
    else
        pass "$path not exposed (HTTP $CODE)"
    fi
done

s "2. HTTPS and certificates"
CODE=$(curl -s -m 15 -o /dev/null -w '%{http_code} %{redirect_url}' "http://$D/shop/")
case "$CODE" in
    30[178]\ https://*) pass "plain http redirects to https ($CODE)" ;;
    *) fail "plain http does not redirect to https (got: $CODE); turn on HTTPS Enforce in Site Tools" ;;
esac
for h in "$D" "www.$D"; do
    END=$(echo | timeout 10 openssl s_client -connect "$h:443" -servername "$h" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
    if [ -z "$END" ]; then fail "$h has no valid certificate"; continue; fi
    DAYS=$(( ($(date -d "$END" +%s) - $(date +%s)) / 86400 ))
    if [ "$DAYS" -gt 14 ]; then pass "$h certificate valid for $DAYS more days"; else warn "$h certificate expires in $DAYS days"; fi
done

s "3. security headers"
get "https://$D/"
for h in strict-transport-security content-security-policy x-content-type-options x-frame-options referrer-policy permissions-policy cross-origin-opener-policy; do
    if [ -n "$(hdr "$h")" ]; then pass "$h present"; else fail "$h missing"; fi
done
CT=$(hdr content-type)
case "$CT" in text/html*) pass "pages are labelled text/html ($CT)" ;; *) fail "pages labelled '$CT' (browsers would show raw HTML)" ;; esac

s "4. personal pages are never stored by the host's page cache"
for path in / /shop/ /cart/ /account/login /admin/login; do
    get "https://$D$path"; get "https://$D$path"
    CC=$(hdr cache-control); PC=$(hdr x-proxy-cache)
    if echo "$CC" | grep -q "no-store" && [ "$PC" != HIT ]; then
        pass "$path: '$CC'${PC:+, host cache $PC}"
    else
        fail "$path: cache-control '$CC', host cache '$PC' (second request)"
    fi
done
get "https://$D/static/css/tokens.css?v=check"
if hdr cache-control | grep -q "public"; then pass "static files cacheable ($(hdr cache-control))"; else warn "static files not cacheable ($(hdr cache-control))"; fi

s "5. preview lock and search engines"
PW=$(setting PREVIEW_PASSWORD)
get "https://$D/"
if [ -n "$PW" ]; then
    if [ "$CODE" = 503 ] && grep -q "Our new shop is on its way" "$TMP/b"; then pass "public sees the coming-soon page"; else fail "lock is set but / returned $CODE"; fi
    if hdr x-robots-tag | grep -q noindex; then pass "X-Robots-Tag noindex while locked"; else fail "no noindex header while locked"; fi
    get "https://$D/robots.txt"
    if grep -q "Disallow: /$" "$TMP/b"; then pass "robots.txt blocks everything while locked"; else fail "robots.txt does not block crawlers while locked"; fi
else
    warn "PREVIEW_PASSWORD not set: the shop is open to the public"
fi

s "6. things that must stay reachable"
get "https://$D/health/live"
[ "$CODE" = 200 ] && pass "/health/live 200" || fail "/health/live returned $CODE"
get "https://$D/health/ready"
if grep -qiE '"database"|"redis"' "$TMP/b"; then fail "/health/ready shows internal details to the public"; else pass "/health/ready hides internal details (HTTP $CODE)"; fi
get "https://$D/admin/login"
[ "$CODE" = 200 ] && pass "/admin/login 200" || fail "/admin/login returned $CODE"
SP=$(setting STUDIO_PATH)
get "https://$D/$SP/"
[ "$CODE" = 200 ] && pass "Studio sign-in page 200" || fail "Studio sign-in page returned $CODE"
for w in stripe paypal; do
    get -X POST -H 'Content-Type: application/json' --data '{}' "https://$D/webhooks/$w"
    if [ "$CODE" = 400 ] && grep -q '"error"' "$TMP/b"; then
        pass "/webhooks/$w reaches the app and rejects an unsigned request (400)"
    else
        fail "/webhooks/$w returned $CODE (expected the app's 400); payments could not confirm"
    fi
done
echo "      note: these run from the server; Stage 2 confirms Stripe/PayPal get through SiteGround's anti-bot check from outside"

s "7. private files and settings on the server"
[ "$(stat -c %a "$G")" = 700 ] && pass "~/.ggflask is owner-only (700)" || fail "~/.ggflask mode $(stat -c %a "$G")"
[ "$(stat -c %a "$G/env")" = 600 ] && pass "settings file is owner-only (600)" || fail "settings file mode $(stat -c %a "$G/env"): run chmod 600 ~/.ggflask/env"
[ "$(stat -c %a "$G/run")" = 700 ] && pass "socket folder is owner-only (700)" || fail "socket folder mode $(stat -c %a "$G/run")"
[ "$(setting APP_ENV)" = production ] && pass "APP_ENV=production" || fail "APP_ENV is not production"
DBG=$(setting GG_DEBUG_FORWARDING)
[ -z "$DBG" ] || [ "$DBG" = 0 ] && pass "IP debug logging off" || warn "GG_DEBUG_FORWARDING=$DBG: set it to 0"
if grep -qvE '^[[:space:]]*($|#|[A-Za-z_][A-Za-z0-9_]*=)' "$G/env"; then
    fail "settings file has lines that are not KEY=value (they break the helper scripts)"
else
    pass "settings file lines are all KEY=value"
fi

s "8. no secrets in the logs"
"$PY" - "$G/env" "$G/logs" <<'PY'
import os, re, sys
secret_keys = ("SECRET", "PASSWORD", "TOKEN", "KEY", "DATABASE_URL", "WEBHOOK", "TOPIC", "STUDIO_PATH", "CLIENT_ID")
values = {}
for raw in open(sys.argv[1], encoding="utf-8"):
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line: continue
    k, v = line.split("=", 1); k = k.strip(); v = v.strip().strip("\"'")
    v = re.split(r"\s#", v)[0].strip()
    if any(s in k for s in secret_keys) and len(v) >= 8:
        values[k] = v
        if k == "DATABASE_URL" and "@" in v:  # the password part alone
            pw = v.split("://", 1)[-1].split("@", 1)[0].split(":", 1)[-1]
            if len(pw) >= 6: values["DATABASE_URL password"] = pw
found = set()
for name in os.listdir(sys.argv[2]):
    path = os.path.join(sys.argv[2], name)
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        continue
    for k, v in values.items():
        if v in text: found.add((k, name))
    if re.search(r"sk_(live|test)_[A-Za-z0-9]{10,}|whsec_[A-Za-z0-9]{10,}", text):
        found.add(("a Stripe key pattern", name))
if found:
    for k, name in sorted(found):
        print(f"FAIL  {k} appears in logs/{name}")
    print("      clear that log with: : > ~/.ggflask/logs/<name>  and change the exposed value")
    sys.exit(1)
print(f"PASS  none of {len(values)} secret settings appear in the logs")
PY
[ $? -eq 0 ] && P=$((P + 1)) || F=$((F + 1))

s "9. outbound connections the shop needs"
for target in api.stripe.com:443 api-m.paypal.com:443 api-m.sandbox.paypal.com:443 smtp.gmail.com:587 ntfy.sh:443 autosuggest.search.hereapi.com:443; do
    host=${target%:*}; port=${target#*:}
    if timeout 6 bash -c "exec 3<>/dev/tcp/$host/$port" 2>/dev/null; then pass "can reach $target"; else fail "cannot reach $target"; fi
done

echo
echo "== RESULT: $P passed, $F failed, $W warnings"
