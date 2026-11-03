# Tamirabot deployment security checklist

## Network and proxy

- [ ] Production uses `compose.production.yaml` and the `production` profile.
- [ ] Only Cloudflare Tunnel reaches the Caddy origin; no public 80/443 ports
      or public DNS records point to the server.
- [ ] Cloudflare **Always Use HTTPS** is enabled and HTTP redirects to HTTPS.
- [ ] The database, Django API, and media volume have no public host ports.
- [ ] Host firewall permits SSH only; PostgreSQL 5432 is not open.
- [ ] Cloudflare tunnel token is private, not in Git, and rotated if exposed.
- [ ] Tailscale alternative is ACL-restricted and not mistaken for public access.

## Django and browser

- [ ] `DJANGO_DEBUG=False`; hostname allow-list and HTTPS CSRF origin are exact.
- [ ] `DJANGO_HTTPS_ENABLED=True`; session and CSRF cookies use Secure.
- [ ] HSTS is enabled only after verified HTTPS; no preload or subdomain scope.
- [ ] CSRF middleware is active and only the app's HTTPS origin is trusted.
- [ ] CORS remains same-origin: no wildcard or cross-site
      `Access-Control-Allow-Origin` response.
- [ ] Caddy supplies nosniff, Referrer-Policy, frame protection, HSTS, and a
      camera-only self `Permissions-Policy`; no permissive CSP was added.
- [ ] Login, user-management, backup, and restore throttles are active.
- [ ] Optional TOTP is enabled only after each Super Admin seed is privately
      provisioned and `.env` permissions are restricted.
- [ ] Passwords and tunnel/database/Django secrets are unique and stored only
      in protected environment configuration.

## Data, updates, and monitoring

- [ ] PostgreSQL and media use named persistent volumes; backup directory is
      separate and preferably on a separate mounted disk.
- [ ] Backups are scheduled, retained, checksummed, and copied off-machine.
- [ ] Restore preview, schema match, dry-run summary, and confirmation are
      required before restore.
- [ ] Disk-space timer is enabled; journal/email/monitoring alerts are checked.
- [ ] Container log rotation is set to 10 MB × 5 files.
- [ ] `/api/health/` reports database connectivity; errors are checked in
      Django, Caddy, cloudflared, and PostgreSQL logs.
- [ ] PostgreSQL tuning is sized to host RAM and concurrent query load.
- [ ] Slow queries are reviewed with `pg_stat_statements` or PostgreSQL logs
      before adding indexes; no unmeasured index bloat is introduced.
- [ ] Before an update, a current backup exists and pending migrations are
      reviewed.
- [ ] A restore/machine-move drill has been completed and documented.
