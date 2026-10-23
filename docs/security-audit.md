# Security controls and denial logs

## VPS SSH (2026-10-23)

`/etc/ssh/sshd_config.d/00-poopbot-key-only.conf` requires public-key
authentication, disables passwords and keyboard-interactive authentication, and
sets `PermitRootLogin prohibit-password`. The existing authorized key is retained.
Validate with `sshd -t` and `sshd -T` before reloading `ssh`; verify a **new** SSH
connection with connection sharing disabled before cancelling the rollback timer.
No ports, keys or SSH usernames were changed.

Fail2ban's `sshd` jail uses the systemd journal and `nftables-multiport`, port 22
only: six failures within ten minutes cause a one-hour ban. Loopback and the
current maintenance egress IP are in `ignoreip` in
`/etc/fail2ban/jail.d/poopbot-sshd.local`. Update the maintenance exception when
needed; a successful key login from a new address is still permitted.
HTTP/HTTPS and Docker forwarding rules are not managed by this jail.

Root-only rollback scripts and pre-change SSH configuration are kept in
`/root/poopbot-security-20261023/`. Rollback timers are temporary safeguards,
cancelled only after fresh key login / ban-unban verification succeeds.

## Application audit

`app.security` records structured `access_denied` events with UTC timestamp,
surface, fixed action/reason, status and numeric actor/chat IDs where available.
Mini App actor IDs are recorded **only after** valid Telegram authentication.
It covers rejected Mini App requests, `/debug`, `/streak`, privileged callbacks,
shared group settings, another user's delete confirmation and recap scope gates.
Expected stale requests or ordinary authorization failures are not automatically
classified as attacks.

These audit records contain no raw commands, callback payloads, arbitrary URLs,
query strings, bodies, Authorization headers, Telegram initData, usernames or
mark contents.
Existing permission checks and API responses are unchanged.

The fallback handlers for unknown commands and stale/unknown buttons also log
only the event type and numeric chat/user IDs in ordinary application logs.
Command names, arguments and callback payloads are omitted entirely. User-facing
replies are unchanged. This applies to new records; it does not scrub older logs
or backups created before the change.

Records go to container logs and dedicated host files, retained across container
replacement. Each file rotates at 2 MiB with five backups (about 24 MiB combined).
The separate bind mounts prevent Mini App access to the bot audit directory.

- `/var/log/poopbot-security/bot/bot.jsonl`
- `/var/log/poopbot-security/mini-app/mini-app.jsonl`

Prepare these directories **before** updating the containers:

```sh
install -d -m 0750 /var/log/poopbot-security
install -d -m 0750 -o root -g root /var/log/poopbot-security/bot
install -d -m 0750 -o 65534 -g 65534 /var/log/poopbot-security/mini-app
```

## Nginx API audit

Install `deploy/nginx/poopcheck-security-log.conf` as
`/etc/nginx/conf.d/poopcheck-security-log.conf` (http context) **before** reloading
the updated `poopcheck-demo.conf` site include. Run `nginx -t`, then reload nginx.
The existing FinAsist server block and upstream remain unchanged.

Only failed `/poopcheck-api/` responses (4xx/5xx) are recorded in
`/var/log/nginx/poopcheck-security.log`: actual peer IP, allowlisted route/method,
status, duration and Nginx-generated request ID. Nginx overwrites the corresponding
proxy header; the same ID joins the application reason and verified actor to the
network event. A forged `X-Forwarded-For` does not change the recorded IP.
Normal successful requests and health checks produce no audit lines.
The existing `/etc/logrotate.d/nginx` policy rotates this `*.log` file.

Verification: missing/invalid Telegram authorization must return 401 and appear
in both logs with the same request ID; a private successful read must return 200
without new security records. Use test marker strings in headers/query/body to
verify they never appear in these audit files. Do not generate failed SSH
password attempts from a real maintenance address to test bans; use a reserved
documentation IP for an explicit fail2ban ban/unban check instead.
