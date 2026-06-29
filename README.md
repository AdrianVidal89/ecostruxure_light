# EcoStruxure Light

Internal Django web application for managing **Proof of Concept (POC)** projects:
structured phases, tasks and tests, with role-based access control and a clean
Schneider Electric–themed UI.

> Built incrementally. This repository currently contains **Step 1 — Project
> scaffold**. See the build order at the bottom of this file.

---

## Tech stack

| Layer        | Choice                                             |
|--------------|----------------------------------------------------|
| Backend      | Django 5.2 (Python 3.14)                            |
| Frontend     | HTMX + Alpine.js + Tailwind CSS (CDN)              |
| Database     | PostgreSQL (production) · SQLite (local dev)        |
| Markdown     | EasyMDE (browser) + markdown2 (server-side)         |
| Config       | django-environ (`.env`)                             |
| Auth         | Django auth with a custom `User` model + role system|

---

## Project layout

```
.
├── config/                 # Project package
│   ├── settings/
│   │   ├── base.py         # Shared settings (reads .env)
│   │   ├── development.py  # DEBUG, SQLite default
│   │   └── production.py   # Hardened, PostgreSQL, WhiteNoise
│   ├── urls.py
│   ├── wsgi.py             # → config.settings.production
│   └── asgi.py
├── apps/
│   ├── core/               # Base layout, context processors, mixins
│   ├── accounts/           # Custom User model (auth/UI in Step 2)
│   ├── pocs/               # POC/Phase/Task/Test/AuditLog (later steps)
│   └── reports/            # ReportRequest + placeholder (later step)
├── templates/              # base.html, components/, per-app templates
├── static/                 # css/, js/
├── fixtures/               # initial_data.json (added in Step 2)
├── .env.example            # Documented environment variables
├── requirements.txt
└── manage.py               # → config.settings.development
```

---

## Local setup

Requires **Python 3.14** and (for production) **PostgreSQL 18**.

```bash
# 1. Create & activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows PowerShell
# source .venv/bin/activate      # macOS / Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
copy .env.example .env           # Windows  (cp on macOS/Linux)
# Generate a SECRET_KEY:
python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"

# 4. Apply migrations (uses SQLite by default in development)
python manage.py migrate

# 5. (Optional) Load seed users for testing
python manage.py loaddata fixtures/initial_data.json

# 6. Run the development server
python manage.py runserver
```

Then open <http://127.0.0.1:8000/>.

### Seed users (development only)

`fixtures/initial_data.json` provides one user per role. All share the password
**`ChangeMe!123`** — change these before any non-local use.

| Username | Role        | Access                                    |
|----------|-------------|-------------------------------------------|
| `admin`  | Admin       | Everything, incl. user management         |
| `lead`   | POC Lead    | Manage phases/tasks/tests in their POCs   |
| `member` | Team Member | Execute assigned tasks/tests              |

### Using PostgreSQL locally (optional)

Set `DATABASE_URL` in your `.env`, e.g.:

```
DATABASE_URL=postgres://USER:PASSWORD@127.0.0.1:5432/ecostruxure_light
```

### Production

Use `config.settings.production` (the default for `wsgi.py`). It **requires**
`SECRET_KEY`, `ALLOWED_HOSTS` and a PostgreSQL `DATABASE_URL` in the environment,
serves static files via WhiteNoise (`python manage.py collectstatic`), and
enables HTTPS/security hardening.

---

## Build order

1. **Project scaffold** ✅ — settings split, base layout, Schneider theme
2. **Accounts app** ✅ — custom User, login/logout/profile, admin user-management UI
3. **POC app — models** ✅ — POC/Phase/Task/Test/AuditLog + migrations
4. **POC CRUD** ✅ — admin-only, HTMX member assignment
5. **Phase CRUD** ✅ — Sortable.js + HTMX drag-and-drop reorder
6. **Task CRUD + execution** ✅ — inline HTMX status/notes
7. **Test CRUD + execution** ✅ — inline HTMX result/verdict/evidence
8. **Dashboard** ✅ — POC cards, progress, admin stats
9. **Audit log** ✅ — signals + current-user middleware + POC detail tab
10. **Reports** ✅ — Markdown→DOCX generation (see below)

## Reports

Real report generation (not a placeholder), powered by a vendored
`python-docx` + `mistune` converter in `apps/reports/converter/`:

* **Phase reports** — attach a `.docx` template to a phase (admin/lead); generate
  a Word report from that phase's tests (summary table + per-test detail, with
  PASS/FAIL colouring) from the POC's **Reports** tab.
* **Custom reports** — an admin defines named **report types** (name + `.docx`
  template) in the global **Reports** area; any user picks a type and uploads a
  source document (`.md` recommended; `.docx`/`.txt` accepted) to convert into
  that template.

Generated reports inherit the template's cover page, table of contents and styles.
