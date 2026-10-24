# Tamirabot foundation

Tamirabot is a self-hosted inventory and sales workspace. The foundation
provides user roles, token authentication, an audit trail, a responsive
installable React shell, and Docker Compose deployment wiring. Module 1 adds
product and component master data, revision-controlled bills of material, and
server-side role-enforced catalog APIs. Module 2A adds raw-material inventory
with a stock movement ledger. Module 2B adds prioritized work orders, BOM
reservations, assigned-admin packing, finished-unit serials, and printable
Code128 barcode labels. Module 2C adds finished-unit inventory, trace history,
serial scanning, and production reconciliation. Sales workflows, dashboard
reporting, and backup operations are not implemented yet.

## Architecture

```text
Browser ── HTTPS ── Caddy ──┬── /api/* ── Django REST Framework ── PostgreSQL
                            └── /* ───── React PWA (Nginx)
```

- `backend/accounts/`: custom user model, roles, authentication, user API,
  permissions, and initial Super Admin command.
- `backend/audit/`: generic audit model, recording helper, and Super Admin-only
  filtered read API.
- `backend/catalog/`: product and component master data, product revisions,
  mandatory BOM lines, image validation, catalog API, revision-freeze support,
  and ordered dummy-data seeding.
- `backend/inventory/`: stock movement ledger, transactionally updated balances,
  adjustment approval, inventory API, and raw-material screens.
- `backend/workorders/`: BOM revision snapshots, priority-aware reservations,
  work-order lifecycle, idempotent packing, finished-unit status and history,
  serials, barcode labels, reconciliation API, and tests.
- `frontend/src/`: login, role-aware responsive navigation, Super Admin user
  management, product/BOM, components, and raw-material inventory, plus
  work-order, packing, thermal-label, and finished-goods screens. Authentication
  tokens are held in tab-scoped session storage.
- The frontend uses the Tamirabot brand palette (orange `#EF4A50`, dark blue
  `#2E1F60`, and light grey `#F0F0F0`) and the supplied logo assets in
  `frontend/public/`. Its footer includes the official social profiles, email
  addresses, and call/WhatsApp contact.
- `deploy/Caddyfile`: HTTPS reverse proxy. Caddy obtains and renews public
  certificates for a real domain; `localhost` is the local-development default.
- PostgreSQL is only reachable on the Compose network; it has no published host
  port. The Django API and frontend are also only reachable through Caddy.
- Product and component photos are limited to 5 MB, JPEG/PNG/WebP, and
  20-megapixel source images. Valid images are resized to a maximum 1600-pixel
  edge and stored in the shared `media` volume.

## First run

1. Copy `.env.example` to `.env`.
2. Replace `POSTGRES_PASSWORD` and `DJANGO_SECRET_KEY` with long random values.
   Keep `.env` private; it is excluded from Git.
3. For local development, leave `DOMAIN=localhost` and
   `CADDY_BIND_ADDRESS=127.0.0.1`; this keeps the proxy reachable only from
   this machine. For public HTTPS, set `DOMAIN` to your domain and
   `CADDY_BIND_ADDRESS=0.0.0.0`, point its DNS A/AAAA record to this server,
   set `DJANGO_ALLOWED_HOSTS` to that domain, and open inbound TCP ports 80 and
   443 (and UDP 443 for HTTP/3). Set `DJANGO_CSRF_TRUSTED_ORIGINS` to the HTTPS
   origin when needed by future cookie-authenticated features.
4. Start the stack with `docker compose up --build -d`. Caddy is the only
   internet-facing service; do not publish PostgreSQL.
5. Create the first Super Admin interactively:

   ```sh
   docker compose exec web python manage.py seed_super_admin \
     --username your-admin-name --email you@example.com
   ```

   The command prompts for the password twice and applies Django password
   validators. It refuses to create another account when an active Super Admin
   already exists.
6. Open `https://localhost` for local use, or `https://your-domain` after DNS
   and HTTPS are ready. Caddy's local `localhost` certificate is not a
   publicly trusted certificate.

The web container applies migrations and collects static files on startup.
Database data is stored in the named `pgdata` volume; do not run
`docker compose down -v` unless you intentionally want to delete all persisted
database data. Keep tested backups on a separate device or server as well.

## Development checks

Backend checks use SQLite when selected, so they do not require a running
PostgreSQL service:

```sh
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
DJANGO_DB_ENGINE=django.db.backends.sqlite3 python manage.py makemigrations --check --dry-run
DJANGO_DB_ENGINE=django.db.backends.sqlite3 python manage.py test
```

Frontend:

```sh
cd frontend
npm ci
npm run build
```

## API foundation

- `GET /api/health/` — unauthenticated health check.
- `POST /api/auth/login/` — username/password authentication; limited to five
  attempts per minute per client IP by default. Returns a DRF token.
- `POST /api/auth/logout/` — revoke the current token.
- `GET /api/auth/me/` — return the current user's safe profile.
- `/api/users/` — list/create users; Super Admin only.
- `/api/users/<id>/` — retrieve/update role or active state; Super Admin only.
- `GET /api/audit-logs/` — Super Admin-only audit events. Filters include
  `actor`, `action`, `object_type` (`app_label.model`), `object_id`,
  `created_after`, and `created_before`.

The Super Admin-only **Users** page creates Admin accounts with a username,
email, and password, and allows Super Admins to change user roles or deactivate
and reactivate accounts. Passwords are validated server-side and never
returned to the browser. Admins cannot access user management, including via
direct API requests.

## Module 1 API

All catalog routes require authentication. Both roles can read; only Super
Admins can create, update, or delete catalog records.

- `/api/products/` — list/create products. Search with `search`, or filter by
  `product_number`, `name`, `sku`, `revision_number`, and `active`. Product
  creation requires a non-empty `bom_lines` array. Each line contains an
  existing active component ID and a positive `quantity_per_unit`; product,
  first revision, and BOM are created atomically.
- `/api/products/<id>/` — retrieve/update/delete a product. Creating a product
  also creates its first current revision (revision 1) and BOM.
- `/api/products/<id>/revisions/` — list revisions or create the next revision.
  A new revision copies the previous revision's BOM automatically; revisions
  used by work orders are read-only.
- `/api/revisions/<id>/` — read or update revision notes; frozen revisions
  cannot be changed or deleted, and the current revision cannot be deleted.
- `/api/revisions/<id>/bom/` — list or add components to a revision BOM.
  `units` is a positive whole number (maximum 1,000,000); each result includes
  `total_required = quantity_per_unit × units`. Filters include `sku`,
  `component_name`, `product_number`, and `revision_number`.
- `/api/bom-lines/<id>/` — read, update, or remove a BOM line.
- `/api/components/` and `/api/components/<id>/` — list/create and
  retrieve/update/delete components. Search with `search` and filter with
  `sku`, `name`, `category`, `revision`, and `active`. List items include
  `used_in_count`; component detail includes its product/revision `used_in`
  list. A component referenced by a BOM cannot be deleted, but can be
  deactivated.
- `POST /api/components/quick-create/` — create a component from the product
  form's inline component dialog. It uses the same validation, permission, and
  audit rules as normal component creation.

## Module 2A: Raw material inventory

- `GET /api/inventory/` — list component stock with `on_hand`, `reserved`,
  `available`, and `low_stock`; `search` matches component SKU, name, or
  category.
- `GET /api/inventory/<component_id>/` — component balance and full movement
  history.
- `POST /api/inventory/receive/` — Super Admin only; records a positive
  receipt with an optional note.
- `GET, POST /api/inventory/adjustments/` — Super Admin only. A non-zero
  signed delta and reason are required. Requests remain pending and do not
  change stock until approved.
- `POST /api/inventory/adjustments/<id>/approve/` or `/reject/` — Super Admin
  review. Approval writes an `ADJUST` movement and updates the balance in the
  same database transaction.
- `GET /api/inventory/low-stock/` — Super Admin-only alert list based on
  available quantity and component reorder levels.

Stock balances are transactionally maintained and constrained so on-hand,
reserved, and available quantities cannot be negative. Every change is also
recorded in the stock movement ledger with actor, time, optional work-order
reference, and note. The service layer provides receive, reserve, release,
consume, return, and adjustment-request operations. Admins can view stock and
movement history; only Super Admins can receive stock or request/review
adjustments. The Inventory screens include search, low-stock alerts, receipt
and adjustment forms, review controls, and per-component movement history.

## Module 2B: Work orders, packing, and barcodes

- Super Admins create orders with product, quantity, priority, due date, and
  assigned Admin. The order stores and freezes the current product revision.
- Assignment reserves the complete BOM quantity atomically. A shortage leaves
  every component unreserved and marks the order Blocked with the missing
  component and amount. The waiting queue is High, Medium, then Low priority,
  followed by due date. New higher-priority work can reclaim reservations from
  lower-priority orders that have not started; started orders are not
  preempted. Receiving stock, returning stock, approving a positive adjustment,
  releasing stock, or creating a work order re-evaluates the queue.
- Admins can list and work only on their own assigned orders. They start a
  reserved order before packing. Partial packing consumes its proportional
  reserved BOM quantity and creates one In Stock finished unit per product.
  Super Admins can reassign, reduce, or cancel an order; reassignments are
  audited and cancellation/reduction releases unbuilt reservations.
- Each confirm request requires an idempotency key. A retry with the same key
  and quantity returns the original confirmation; reusing a key with a
  different quantity is rejected. Serial sequences are locked by product
  number and local year/month and have a unique database constraint. Serials
  use `PRODUCTCODE-YYMM-000001`; Code128 barcodes encode the serial directly in
  the locally bundled frontend barcode library.
- `GET /api/work-orders/` — queue; Admin results are restricted to their
  assignments. Results sort by priority and due date.
- `POST /api/work-orders/` — Super Admin create. JSON fields: `product`,
  `quantity`, `priority` (`HIGH`, `MEDIUM`, `LOW`), `due_date` (ISO date), and
  `assigned_admin`.
- `GET, PATCH /api/work-orders/<id>/` — retrieve; Super Admin PATCH may
  reassign (`assigned_admin`) or reduce (`quantity`), one change at a time.
- `POST /api/work-orders/<id>/start/`, `/pack/`, and `/cancel/` — assigned
  Admin starts and packs; Super Admin cancels. Packing JSON includes
  `quantity` and `idempotency_key`.
- `GET /api/finished-units/<id>/label/` — read a unit label. The label page's
  Print button sends `POST /api/finished-units/<id>/reprint/`, which writes an
  audit event before opening the browser's 50×30 mm print layout.
- `GET /api/work-orders/assignees/` — active Admin choices for Super Admins.

SQLite tests cover reservation all-or-nothing behavior, priority and shortage
handling, ownership, status transitions, idempotency, serial formatting,
reduction, and cancellation. The simultaneous serial-allocation test is
skipped on SQLite because it requires `SELECT FOR UPDATE`; run the suite
against PostgreSQL to execute that concurrency test.

## Module 2C: Finished goods inventory

- `GET /api/finished-units/` — list units for all active Admins and Super
  Admins. Optional filters: `product`, `revision`, `status`, `work_order`,
  `date_from`, `date_to`, and serial `search`.
- `GET /api/finished-units/summary/` — per-product counts for In Stock, Sold,
  and Returned units.
- `GET /api/finished-units/<id>/` — unit details, including its work order,
  frozen revision, packer, pack time, and chronological status/sale/return
  history.
- `GET /api/finished-units/lookup/?serial_number=<serial>` — exact
  case-insensitive serial lookup for USB and camera scanners.
- `GET /api/finished-units/reconciliation/` — Super Admin report comparing
  BOM quantity × packed units with component-consumption movements linked to
  packing confirmations. Any missing or excess deduction is flagged by
  component and work order.

Finished unit statuses are In Stock, Reserved for sale, Sold, Returned, and
Under inspection. Status changes are workflow-only (not exposed as editable
unit fields or a manual status API); the service layer validates transitions
and records each event with its actor, timestamp, note, and optional sale
reference. The Finished Goods page includes product summaries, filters,
serial lookup, full unit history, and the Super Admin reconciliation report.
The camera scanner supports Code128 and QR camera decoding in secure browser
contexts; camera access requires HTTPS or localhost. USB scanners work by
typing the serial into the lookup field.

The required user flow is to create or reuse components first, then create a
product with its BOM in the two-step form. If a needed component is missing,
the inline dialog creates it and selects it for that BOM. Product detail lets
you choose a revision and calculate component totals for a requested build
quantity. Component detail shows the products and revisions using it.
Super Admins can edit; Admins see the catalog read-only.

The database enforces unique product numbers/SKUs, unique revision numbers per
product, one line per component per revision, and positive BOM quantities.
Deactivated components cannot be added to a BOM. Every BOM revision must keep
at least one line. Component deletion is prevented while a BOM references it;
deactivate it instead. Catalog writes and revision creation/copying are audited.

Load the bundled sample data (components first, then products, revisions, and
BOM lines) with:

```sh
docker compose exec web python manage.py seed_tamirabot_dummy_data
```

The command is repeatable, reuses components/products with matching keys, and
leaves existing master fields and populated BOMs unchanged. It intentionally
ignores the fixture's `opening_stock` section; inventory stock is loaded by
Module 2A. To load a different JSON file, pass its path as the optional command
argument.

Authentication is enforced by DRF by default. Login and health check are
explicit public exceptions; user and audit endpoints use server-side
permissions. Passwords use Django's configured password hashers and validators.
Successful logins, logouts, user creation, user changes, and initial Super
Admin seeding are written to the audit log.
The optional second-factor hook is disabled by default; enabling it requires
configuring `TWO_FACTOR_VERIFIER` as a dotted path to a verifier callable that
accepts `(user, code)`.

The current login throttle uses Django's local-memory cache and the web service
is intentionally configured as one Gunicorn worker. Use a shared cache before
scaling web processes or replicas.
