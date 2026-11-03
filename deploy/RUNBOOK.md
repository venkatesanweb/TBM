# Tamirabot production runbook

This deployment keeps PostgreSQL, uploaded files, and the application on your
server. Cloudflare Tunnel is the public HTTPS ingress and does not require
opening inbound web ports. Cloudflare is still an external edge/network
provider; if you require no external provider at all, use the private
Tailscale option below and accept that only tailnet members can connect.

## 1. Prepare the host

Use a supported Ubuntu LTS server with a stable internet connection, SSH
access, adequate disk space, and Docker Engine plus the Docker Compose plugin.
Create a dedicated deploy account with Docker access and keep SSH protected by
keys. Allow inbound SSH only in the host firewall; do not allow inbound TCP
80/443 or PostgreSQL 5432. The tunnel makes outbound connections.

Clone/copy this repository to a stable location such as `/opt/tamirabot`.
Create the backup directory on a separate mounted disk when possible:

```sh
sudo install -d -o 10001 -g 10001 -m 0750 /mnt/tamirabot-backups
cd /opt/tamirabot
cp .env.example .env
chmod 600 .env
```

The web container runs as UID 10001; make the backup folder writable by that
UID. Choose a directory on a filesystem mounted at boot, and keep an additional
off-machine copy of important backups.

## 2. Configure secrets and domain

Edit `.env` without posting or sharing its contents:

- Set unique random `POSTGRES_PASSWORD` and `DJANGO_SECRET_KEY` values.
- Set `BACKUP_HOST_DIRECTORY=/mnt/tamirabot-backups`.
- Set `DJANGO_ALLOWED_HOSTS` to the exact public hostname, for example
  `tamirabot.example.com`.
- Set `DJANGO_CSRF_TRUSTED_ORIGINS` to the matching HTTPS origin, for example
  `https://tamirabot.example.com`.
- Keep `DJANGO_DEBUG=False`.
- Set `CLOUDFLARE_TUNNEL_TOKEN` to the token from your Cloudflare-managed
  tunnel. `.env` is Git-ignored; do not put secrets in Compose files or logs.
- Keep `DJANGO_SECURE_HSTS_SECONDS=31536000` after HTTPS is confirmed.

In the Cloudflare dashboard, create a remotely managed Tunnel and add one
public hostname. Route it to `http://caddy:80` using the `tamirabot` Compose
network. Enable **Always Use HTTPS** for the public hostname. Do not configure
an origin IP or add public DNS records pointing directly at the server.

Cloudflare terminates browser TLS and transports traffic through the
authenticated tunnel. The production Caddy proxy accepts traffic only on the
private Compose network and sets the trusted forwarded protocol to HTTPS for
Django. Do not publish the Caddy or web ports to the LAN.

## 3. Start and check production

From the repository directory:

```sh
docker compose -f compose.production.yaml --profile production config --quiet
docker compose -f compose.production.yaml --profile production up -d --build
docker compose -f compose.production.yaml --profile production ps
docker compose -f compose.production.yaml --profile production logs --tail 100 db web frontend caddy cloudflared
curl -fsS http://127.0.0.1:8080/api/health/
```

Confirm the public hostname redirects HTTP to HTTPS, has a valid certificate,
and returns `{"status":"ok"}` at `/api/health/`. Confirm no host listener is
published on ports 80, 443, or 5432. The production Compose file binds only
the Caddy origin check to `127.0.0.1:8080`; this is not reachable from the LAN.

Create the initial Super Admin if needed:

```sh
docker compose -f compose.production.yaml --profile production exec web \
  python manage.py seed_super_admin --username YOUR_ADMIN --email YOU@example.com
```

Login is throttled to five attempts per minute. Sensitive backup and restore
operations have additional scoped limits. The same-origin frontend/API sends
no CORS allow-origin headers; keep the API on the same hostname as the app.

## 4. Optional Super Admin TOTP

TOTP is disabled by default. To enable it, provision a separate Base32 seed
for each Super Admin using a trusted authenticator and add a JSON object
mapping each exact username to its seed in the private `.env` file:

```dotenv
TWO_FACTOR_AUTHENTICATION_ENABLED=True
SUPER_ADMIN_TOTP_SECRETS_JSON={"owner":"BASE32_SEED_FOR_THIS_USER"}
```

Restart/recreate the web service after changing environment configuration.
The login screen already accepts the six-digit authenticator code. Admin
accounts do not require or accept this second factor. Protect `.env` and
restrict server access: these seeds are credentials. Rotate the seed in the
authenticator and `.env` if it is exposed.

## 5. Backups and disk alerts

The web image includes the PostgreSQL 16 client. Create an immediate backup
from the Super Admin **Backup & Restore** page, or run the scheduled command:

```sh
docker compose -f compose.production.yaml --profile production exec -T web \
  python manage.py scheduled_backup
```

The command writes a PostgreSQL dump, portable table exports, uploaded media,
and a checksummed manifest; it retains the latest 14 archives by default.
Install the included weekly backup timer (adjust the service working
directory and user):

```sh
sudo cp deploy/tamirabot-backup.service.template /etc/systemd/system/tamirabot-backup.service
sudo cp deploy/tamirabot-backup.timer.template /etc/systemd/system/tamirabot-backup.timer
sudo systemctl daemon-reload
sudo systemctl enable --now tamirabot-backup.timer
systemctl list-timers tamirabot-backup.timer
```

Edit the supplied service template's `WorkingDirectory` to `/opt/tamirabot`.

Install the backup disk-space alert, edit the unit's backup directory and
threshold, then enable its daily timer:

```sh
sudo install -m 0755 deploy/check-backup-disk-space.sh /usr/local/sbin/tamirabot-backup-disk-alert.sh
sudo cp deploy/tamirabot-backup-disk-alert.service.template /etc/systemd/system/tamirabot-backup-disk-alert.service
sudo cp deploy/tamirabot-backup-disk-alert.timer.template /etc/systemd/system/tamirabot-backup-disk-alert.timer
sudo systemctl daemon-reload
sudo systemctl enable --now tamirabot-backup-disk-alert.timer
systemctl list-timers tamirabot-backup-disk-alert.timer
```

At 85% full, the check logs a warning and fails so it is visible in
`systemctl status` and the journal. Configure the optional email setting with
a local mail command or connect this unit to your host monitoring if you need
an actively delivered alert:

```sh
sudo journalctl -u tamirabot-backup-disk-alert.service
```

## PostgreSQL tuning and index review

The production profile enables `pg_stat_statements` preload and logs queries
taking at least one second. After first startup, enable the extension in the
application database (this does not delete or rewrite application rows):

```sh
docker compose -f compose.production.yaml --profile production exec db \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"'
```

Inspect the highest total-time queries before adding indexes:

```sql
SELECT query, calls, round(total_exec_time::numeric, 1) AS total_ms,
       round(mean_exec_time::numeric, 1) AS mean_ms, rows
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 20;
```

Existing indexes cover work-order status/priority/due-date and assignee/status,
inventory movements by component/time, and sales by date/status. The sales
report/list also filters by platform/date and status-history lookups use
sale/status/latest timestamp; the additive sales migration adds those two
composite indexes. Confirm their impact against query plans and workload before
adding further indexes.

The defaults target a small server with roughly 2 GB RAM. `shared_buffers`
defaults to 256 MB, `effective_cache_size` to 1 GB, and `work_mem` to 8 MB.
Tune the values in `.env` to the machine's actual RAM and concurrent query
load; `work_mem` can be consumed per sort/hash operation and per connection,
so do not raise it based only on total RAM. Keep `max_connections` bounded and
use a connection pooler only if the workload requires one.

## 6. Routine updates

1. Schedule a maintenance window and create/download a fresh backup.
2. Verify there is free disk space and the most recent backup is present.
3. Update the checked-out application version; do not replace `.env`.
4. Review the release notes and pending migrations. Keep a recoverable copy
   outside the server before updates.
5. Build/start using the production Compose command above. Web startup applies
   Django migrations and collects static files.
6. Check health, HTTPS, login, scanner camera permission, API behavior, logs,
   and the backup timer. Keep the prior tested backup until the new release is
   verified.
7. If an update fails, inspect logs and restore a matching-schema backup
   through the Super Admin Restore screen. Never run `down -v`, delete volumes,
   or manually truncate the database.

Container output is kept with Docker's `json-file` rotation at 10 MB per file,
five files per service. Inspect it with:

```sh
docker compose -f compose.production.yaml --profile production logs --tail 200
```

## 7. Restore

Restore replaces all current database rows and all uploaded media. First take
a separate copy of the current data. Sign in as Super Admin, open **Backup &
Restore**, select the ZIP, enter the current password, review the verified
table/media change preview, and explicitly confirm. The backup must have the
same applied migration set as the running application; restore an older
archive using its matching application/schema version rather than bypassing
the check. The database import is transactional; media is rolled back if the
database import fails. Check the audit log and logs after restore.

## 8. Move to a new machine

1. On the old server, create a backup and download/copy it to a separate
   trusted device. Preserve the exact Tamirabot application version.
2. Prepare the new host and backup disk using sections 1–2. Do not copy the
   old PostgreSQL volume onto a different major PostgreSQL version.
3. Configure the same application release, schema, hostname, secure secrets,
   and a new or deliberately transferred Cloudflare Tunnel token.
4. Start the production services and verify health. Upload the archive in the
   Super Admin restore page and complete the preview/restore confirmation.
5. Verify product/component photos, logins, sales, work orders, health, and
   audit logs. Reconfigure the Cloudflare hostname route and backup/disk timers.
6. Keep the old machine and its backup intact until the new host has been
   checked. Then revoke the old tunnel token and remove access deliberately.

## Private Tailscale alternative

If the site should be accessible only to your own devices, do not start the
Cloudflare Tunnel container. Install Tailscale on the server and client
devices, restrict tailnet access with ACLs, then publish the loopback-only Caddy
origin with Tailscale Serve:

```sh
sudo tailscale serve --bg --https=443 http://127.0.0.1:8080
tailscale serve status
```

Add the server's MagicDNS hostname to `DJANGO_ALLOWED_HOSTS` and its HTTPS
origin to `DJANGO_CSRF_TRUSTED_ORIGINS`; make sure the TLS hostname matches.
Tailscale access is private to the tailnet and is not public internet access.
