# EcoStruxure Light — Deployment Runbook (Debian)

Production-grade deployment on two VMs, fed from GitHub Enterprise, with
PostgreSQL data living on a dedicated second disk.

## Architecture

| Environment | VM IP          | Git branch | DB                          |
|-------------|----------------|------------|-----------------------------|
| Production  | 10.144.15.61   | `prod`     | PostgreSQL on dedicated disk|
| Pre-prod    | 10.144.72.20   | `preprod`  | PostgreSQL on dedicated disk|

- Repo: `https://github.schneider-electric.com/SIT-Team/EcostruxureLight.git`
- OS user on the VMs: `OSPUser`
- App directory: `/home/OSPUser/EcostruxureLight`
- Stack: **Gunicorn** (WSGI) → **systemd** (process manager) → **Nginx** (reverse proxy, :80)
- Static files: `collectstatic` → `staticfiles/`, served by Nginx (and WhiteNoise as fallback)
- Each VM tracks ONE branch; updates are pulled with `deploy/deploy.sh`.

The two VMs are configured identically **except** the git branch they check out
and the values in their `.env`.

---

## ⚠️ Secrets — ground rules

- The GitHub token and DB passwords that appeared in the old "Despliegue en
  servidores.md" are **compromised** — rotate/revoke them now and never paste
  credentials into chats or committed files.
- Secrets live ONLY in the VM's `.env` (git-ignored). Never commit `.env`.
- The VMs pull from GitHub with a **read-only deploy key or fine-grained PAT**
  scoped to this one repo (Phase 2). The SSH keys you use to log in as `OSPUser`
  are a different thing and are not put on GitHub.
- Generate a **distinct** `SECRET_KEY` and a **distinct** DB password per VM.

---

## Phase 0 — Publish the code to GitHub (once, from your workstation)

Done from the Windows machine where the project lives. See the chat for the
exact commands; in short: `git init`, commit, add the remote, create branches
`prod` and `preprod`, and push both. After this, both branches exist on GitHub.

---

## Phase 1 — Per VM: base system + PostgreSQL on the dedicated disk

SSH in as `OSPUser` (PuTTY with your private key — see the PuTTY tutorial).

### 1.1 Packages

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip \
                    postgresql postgresql-contrib libpq-dev build-essential \
                    nginx rsync
```

> The pinned dependencies (Django 5.2, psycopg 3.3.4) run fine on Debian's
> Python 3.11+. You do NOT need Python 3.14 on the server.

### 1.2 Identify and format the second disk (the DB disk)

```bash
lsblk        # find the empty data disk, e.g. /dev/sdb (NOT the OS disk /dev/sda)
```

Assuming it is `/dev/sdb` (adjust!), create one partition and an ext4 filesystem:

```bash
sudo parted /dev/sdb --script mklabel gpt mkpart primary ext4 0% 100%
sudo mkfs.ext4 /dev/sdb1
```

### 1.3 Move PostgreSQL's data directory onto that disk

PostgreSQL was installed in 1.1 under `/var/lib/postgresql`. We relocate that
whole tree onto the dedicated disk by mounting the disk there.

```bash
sudo systemctl stop postgresql
sudo mv /var/lib/postgresql /var/lib/postgresql.orig
sudo mkdir /var/lib/postgresql

# Persist the mount by UUID:
sudo blkid /dev/sdb1            # copy the UUID="..."
echo 'UUID=<PASTE-UUID>  /var/lib/postgresql  ext4  defaults  0  2' | sudo tee -a /etc/fstab
sudo mount -a
df -h /var/lib/postgresql       # confirm it is mounted on /dev/sdb1

# Copy the cluster data onto the disk and fix ownership:
sudo rsync -a /var/lib/postgresql.orig/ /var/lib/postgresql/
sudo chown -R postgres:postgres /var/lib/postgresql
sudo systemctl start postgresql
sudo systemctl status postgresql   # active (running)

# Once verified working, remove the backup:
sudo rm -rf /var/lib/postgresql.orig
```

### 1.4 Create the database and application user

```bash
sudo -u postgres psql <<'SQL'
CREATE DATABASE ecostruxure_light;
CREATE USER ecx_app WITH PASSWORD '__STRONG_DB_PASSWORD__';
ALTER DATABASE ecostruxure_light OWNER TO ecx_app;
GRANT CONNECT ON DATABASE ecostruxure_light TO ecx_app;
\c ecostruxure_light
GRANT ALL ON SCHEMA public TO ecx_app;
SQL
```

> Making `ecx_app` the database owner avoids the PostgreSQL 15 public-schema
> privilege pitfalls when Django runs migrations.

### 1.5 Tune PostgreSQL for concurrent users

Dozens of users is a light load, so PostgreSQL's defaults already cope — but
these settings make it comfortably fast. **Size them to the VM's RAM** (check
with `free -h`). The example below assumes ~4 GB dedicated to this VM; scale
`shared_buffers`/`effective_cache_size` up proportionally if you have more.

```bash
sudo -u postgres psql <<'SQL'
ALTER SYSTEM SET shared_buffers        = '1GB';     -- ~25% of RAM
ALTER SYSTEM SET effective_cache_size  = '3GB';     -- ~50-75% of RAM
ALTER SYSTEM SET work_mem              = '16MB';    -- per-sort/hash memory
ALTER SYSTEM SET maintenance_work_mem  = '256MB';   -- VACUUM / index builds
ALTER SYSTEM SET max_connections       = '100';     -- ample for dozens of users
ALTER SYSTEM SET random_page_cost      = '1.1';     -- assume SSD storage
SQL
sudo systemctl restart postgresql        # shared_buffers/max_connections need a restart
```

Why this is enough for dozens of users:
- The app uses **persistent DB connections** (`CONN_MAX_AGE`, set in
  `production.py`), so each Gunicorn worker reuses one connection instead of
  reconnecting per request. With 3-5 workers that's only a handful of
  connections — far below `max_connections=100`.
- Static/media are served by **Nginx**, not Python, so user browsing doesn't tie
  up app/DB workers.

> Don't set `max_connections` very high "just in case" — each connection costs
> RAM. Persistent connections + a sane worker count is the right model here.

---

## Phase 2 — Per VM: get the code from GitHub

### 2.1 Grant the VM read-only pull access

Pick ONE method (deploy key preferred):

**A) SSH deploy key (read-only, per repo)**
```bash
ssh-keygen -t ed25519 -C "ecx-$(hostname)-deploy" -f ~/.ssh/ecx_deploy -N ""
cat ~/.ssh/ecx_deploy.pub
```
Add that public key in GitHub → repo **Settings → Deploy keys → Add** (leave
"Allow write access" UNCHECKED). Then tell git/ssh to use it:
```bash
cat >> ~/.ssh/config <<'CFG'
Host github.schneider-electric.com
    IdentityFile ~/.ssh/ecx_deploy
    IdentitiesOnly yes
CFG
```
Clone over SSH:
```bash
cd ~
git clone git@github.schneider-electric.com:SIT-Team/EcostruxureLight.git
```

**B) HTTPS + fine-grained PAT (fallback if SSH/:22 is blocked)**
Create a fine-grained token scoped to ONLY this repo with **Contents: Read-only**,
then:
```bash
cd ~
git clone https://github.schneider-electric.com/SIT-Team/EcostruxureLight.git
git config --global credential.helper store   # caches the PAT for pulls
```
(Enter the PAT as the password on first pull.)

### 2.2 Check out this VM's branch

```bash
cd ~/EcostruxureLight
git checkout prod        # on PRD (10.144.15.61)
# git checkout preprod   # on PPRD (10.144.72.20)
```

### 2.3 Virtualenv + dependencies

```bash
cd ~/EcostruxureLight
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 2.4 Environment file

```bash
cp deploy/env.production.example .env
nano .env
```
Set, for this VM:
- `SECRET_KEY` — a freshly generated key (command is in the file).
- `ALLOWED_HOSTS` — `10.144.15.61` (PRD) or `10.144.72.20` (PPRD).
- `DATABASE_URL` — with the `ecx_app` password from Phase 1.4.
- `CSRF_TRUSTED_ORIGINS` — `http://<this VM IP>`.
- Leave the `*_SECURE` flags `False` for now (plain HTTP).

### 2.5 Initialise the app

```bash
export DJANGO_SETTINGS_MODULE=config.settings.production
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser     # first admin account
```

> `manage.py` defaults to *development* settings, so always export
> `DJANGO_SETTINGS_MODULE=config.settings.production` (the deploy script and the
> systemd unit do this for you). `gunicorn` already defaults to production via
> `config/wsgi.py`.

Smoke test before wiring up services:
```bash
gunicorn --bind 0.0.0.0:8000 config.wsgi:application   # Ctrl+C after checking
```

---

## Phase 3 — Gunicorn under systemd

```bash
sudo cp deploy/ecostruxure-light.service /etc/systemd/system/ecostruxure-light.service
sudo systemctl daemon-reload
sudo systemctl enable --now ecostruxure-light
sudo systemctl status ecostruxure-light       # active (running)
```
This creates the socket `/home/OSPUser/EcostruxureLight/app.sock`.

> **Worker sizing:** the unit ships with `--workers 3`. The rule of thumb is
> `(2 × CPU cores) + 1`. Check cores with `nproc`; if the VM has e.g. 4 cores,
> edit the `--workers` value to `9`, then
> `sudo systemctl daemon-reload && sudo systemctl restart ecostruxure-light`.
> For dozens of users, 3-5 workers is plenty.

---

## Phase 4 — Nginx reverse proxy (:80)

```bash
# Let Nginx into the home directory to reach the socket/static:
sudo chmod 755 /home/OSPUser

sudo cp deploy/nginx-ecostruxure-light.conf /etc/nginx/sites-available/ecostruxure-light
# Edit server_name to this VM's IP:
sudo nano /etc/nginx/sites-available/ecostruxure-light

sudo ln -s /etc/nginx/sites-available/ecostruxure-light /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t            # must say: syntax is ok / test is successful
sudo systemctl restart nginx
```

Browse to `http://10.144.15.61` (PRD) / `http://10.144.72.20` (PPRD) and log in.

---

## Phase 5 — The update workflow (every time you push to GitHub)

1. On your workstation, push to the branch for the target environment:
   - `git push origin preprod`  → updates the PPRD VM's source of truth
   - `git push origin prod`     → updates the PRD VM's source of truth
2. SSH into that VM and run the one-liner:
   ```bash
   cd /home/OSPUser/EcostruxureLight && ./deploy/deploy.sh
   ```
   It force-syncs to the remote branch, installs deps, migrates, collects
   static, and restarts the service.

Make the script executable once: `chmod +x deploy/deploy.sh`.

> `deploy.sh` runs `git reset --hard origin/<branch>` — the VM is a mirror of
> GitHub. Never edit code directly on the server; change it in git and redeploy.

### Recommended promotion flow

```
work locally ──push──> preprod ──(validate on PPRD)──> merge ──push──> prod
```
e.g. `git checkout prod && git merge --ff-only preprod && git push origin prod`,
then run `deploy.sh` on PRD.

---

## Notes & gotchas

- **Media is not in git** (`/media/` is git-ignored). Uploaded files — evidence,
  report templates, the branding logo — live only on each VM. Upload the logo
  again via the Branding page on each environment (or copy `media/` across with
  `scp`/`rsync` if you want them identical).
- **Moving to HTTPS** later = put a TLS cert in Nginx (443), then flip the
  `*_SECURE` flags and `SECURE_HSTS_SECONDS` in `.env` and restart. No code
  change needed.
- **Logs**: `sudo journalctl -u ecostruxure-light -f` (app),
  `sudo tail -f /var/log/nginx/error.log` (proxy).
- **DB backups**: schedule `pg_dump ecostruxure_light` to a file on the data
  disk (e.g. a daily cron) — not covered here but do it before go-live.
