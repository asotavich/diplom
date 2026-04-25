# FEAnalyzer — Production Deployment Guide

This guide walks through deploying the full stack (Django + Celery + PostgreSQL + Redis + Nginx + React) on a single Ubuntu VPS using Docker Compose.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Initial VPS Setup](#2-initial-vps-setup)
3. [Install Docker Engine](#3-install-docker-engine)
4. [Deploy the Application](#4-deploy-the-application)
5. [SSL/HTTPS with Certbot](#5-ssl--https-with-certbot)
6. [Maintenance Runbook](#6-maintenance-runbook)
7. [Troubleshooting](#7-troubleshooting)

---

## 1. Prerequisites

### VPS Requirements

| Resource | Minimum | Recommended |
|---|---|---|
| OS | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |
| CPU | 1 vCPU | 2 vCPUs |
| RAM | 2 GB | 4 GB |
| Disk | 20 GB SSD | 40 GB SSD |
| Open ports | 22 (SSH), 80 (HTTP), 443 (HTTPS) |

> Providers that work well: Hetzner Cloud (CX22), DigitalOcean (2 GB Droplet), Vultr, Linode.

### Domain Name

Point an A record at your server's IP **before** setting up SSL.

```
A  feanalyzer.yourdomain.com  →  YOUR_SERVER_IP
```

DNS propagation can take up to 48 hours (usually under 15 minutes).

---

## 2. Initial VPS Setup

Connect as root, then create a dedicated non-root user.

```bash
# Log in
ssh root@YOUR_SERVER_IP

# Create a deploy user and add to sudo group
adduser deploy
usermod -aG sudo deploy

# Copy your SSH key to the new user so root login is no longer needed
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy

# Harden SSH: disable root login and password auth
sed -i 's/^PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart sshd

# Exit and reconnect as deploy
exit
ssh deploy@YOUR_SERVER_IP
```

### Configure the Firewall

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

### Keep the System Updated

```bash
sudo apt update && sudo apt upgrade -y
sudo apt autoremove -y
```

---

## 3. Install Docker Engine

Install using the official Docker repository (not the Ubuntu snap version).

```bash
# Install prerequisites
sudo apt install -y ca-certificates curl gnupg lsb-release

# Add Docker's official GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Add the Docker repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine + Compose plugin
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Add deploy user to docker group (avoids needing sudo for docker commands)
sudo usermod -aG docker deploy

# Apply group change without logging out
newgrp docker

# Verify installation
docker --version
docker compose version
```

---

## 4. Deploy the Application

### 4.1 Clone the Repository

```bash
# Choose a permanent location for the application
sudo mkdir -p /srv/feanalyzer
sudo chown deploy:deploy /srv/feanalyzer

git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git /srv/feanalyzer
cd /srv/feanalyzer
```

### 4.2 Configure Production Environment

The file `.env.production` is already in the repository with `CHANGE_ME_*` placeholders. Edit it in-place on the server — **do not commit real secrets to Git**.

```bash
cp .env.production .env.production.bak   # safety backup of the template
nano .env.production
```

Fill in every `CHANGE_ME_*` value:

```dotenv
# ---- Django ---------------------------------------------------------------
DJANGO_SECRET_KEY=<output of: python3 -c "import secrets; print(secrets.token_urlsafe(64))">
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=feanalyzer.yourdomain.com,www.feanalyzer.yourdomain.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://feanalyzer.yourdomain.com,https://www.feanalyzer.yourdomain.com

# ---- PostgreSQL -----------------------------------------------------------
POSTGRES_DB=feanalyzer
POSTGRES_USER=feanalyzer_user
POSTGRES_PASSWORD=<strong random password, e.g. openssl rand -hex 32>
POSTGRES_HOST=db
POSTGRES_PORT=5432

# ---- Redis / Celery -------------------------------------------------------
REDIS_HOST=redis
REDIS_PORT=6379
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1

# ---- CORS -----------------------------------------------------------------
CORS_ALLOWED_ORIGINS=https://feanalyzer.yourdomain.com,https://www.feanalyzer.yourdomain.com

# ---- JWT lifetimes --------------------------------------------------------
JWT_ACCESS_TOKEN_LIFETIME_MIN=15
JWT_REFRESH_TOKEN_LIFETIME_DAYS=7
```

**Generate a strong secret key:**

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

**Generate a strong DB password:**

```bash
openssl rand -hex 32
```

### 4.3 First Launch

```bash
cd /srv/feanalyzer

# Build all images and start services in detached mode.
# The web container automatically runs `migrate` and `collectstatic`
# on first start (controlled by DJANGO_MIGRATE=1 in docker-compose.yml).
docker compose --env-file .env.production up -d --build

# Watch logs during startup (Ctrl+C to stop tailing — containers keep running)
docker compose logs -f
```

Expected startup order:
1. `feanalyzer_db` and `feanalyzer_redis` become healthy (~10–15 s)
2. `feanalyzer_web` runs migrations and collectstatic, then starts Gunicorn
3. `feanalyzer_celery` connects to Redis and begins listening for tasks
4. `feanalyzer_frontend` (Nginx) starts serving the React SPA on port 80

### 4.4 Verify the Stack is Running

```bash
# All 5 services should show "Up" or "healthy"
docker compose ps

# Quick smoke test — expect {"status": "ok"}
curl -s http://localhost/healthz/
```

### 4.5 Create the Django Superuser

```bash
docker compose exec web python manage.py createsuperuser
```

Follow the prompts. You can then log in at `http://YOUR_SERVER_IP/admin/`.

---

## 5. SSL / HTTPS with Certbot

The Nginx container listens on port 80. We install a **host-level Nginx** as an HTTPS terminator in front of it, using Certbot to manage certificates. The Docker stack is not modified.

### 5.1 Bind the Container to Localhost Only

Edit `docker-compose.yml` so Docker's Nginx only listens on the loopback interface (not exposed directly to the internet):

```yaml
# In the `frontend` service, change:
ports:
  - "80:80"

# To:
ports:
  - "127.0.0.1:8080:80"
```

Apply the change:

```bash
docker compose --env-file .env.production up -d --build frontend
```

Update the firewall — port 8080 should NOT be publicly accessible:

```bash
sudo ufw delete allow 80/tcp   # we'll re-open it via host nginx
```

### 5.2 Install Host Nginx and Certbot

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
sudo systemctl enable nginx
sudo systemctl start nginx
```

### 5.3 Create a Temporary Nginx Config

```bash
sudo nano /etc/nginx/sites-available/feanalyzer
```

Paste the following (replace the domain):

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name feanalyzer.yourdomain.com www.feanalyzer.yourdomain.com;

    location / {
        proxy_pass         http://127.0.0.1:8080;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

Enable the site and test:

```bash
sudo ln -s /etc/nginx/sites-available/feanalyzer /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

# Re-open port 80 via the host (now served by host nginx)
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
```

### 5.4 Obtain the SSL Certificate

```bash
sudo certbot --nginx -d feanalyzer.yourdomain.com -d www.feanalyzer.yourdomain.com \
  --email YOUR_EMAIL@example.com --agree-tos --no-eff-email
```

Certbot automatically:
- Issues a Let's Encrypt certificate
- Modifies `/etc/nginx/sites-available/feanalyzer` to add HTTPS
- Sets up automatic renewal via a systemd timer

### 5.5 Verify Auto-Renewal

```bash
sudo certbot renew --dry-run
```

Your site is now reachable at `https://feanalyzer.yourdomain.com`.

### 5.6 Update Django Settings for HTTPS

In `.env.production`, confirm these values use your real domain with `https://`:

```dotenv
DJANGO_ALLOWED_HOSTS=feanalyzer.yourdomain.com,www.feanalyzer.yourdomain.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://feanalyzer.yourdomain.com,https://www.feanalyzer.yourdomain.com
CORS_ALLOWED_ORIGINS=https://feanalyzer.yourdomain.com,https://www.feanalyzer.yourdomain.com
```

Restart the web container to pick up the changes:

```bash
cd /srv/feanalyzer
docker compose --env-file .env.production up -d web
```

---

## 6. Maintenance Runbook

### View Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f web
docker compose logs -f celery
docker compose logs -f frontend
```

### Restart a Service

```bash
docker compose restart web
docker compose restart celery
```

### Stop / Start the Entire Stack

```bash
# Stop (containers removed, data volumes preserved)
docker compose down

# Start
docker compose --env-file .env.production up -d
```

### Deploy an Update (Rolling Upgrade)

```bash
cd /srv/feanalyzer
git pull origin main

# Rebuild images and restart — Compose only recreates changed services
docker compose --env-file .env.production up -d --build

# Watch migrations and startup
docker compose logs -f web
```

### Run Django Management Commands

```bash
# Database migrations (auto-run on web startup, but can be run manually)
docker compose exec web python manage.py migrate

# Create a new superuser
docker compose exec web python manage.py createsuperuser

# Open a Django shell
docker compose exec web python manage.py shell

# Check configuration
docker compose exec web python manage.py check
```

### Database Backup

```bash
# Dump the database to a local file
docker compose exec db pg_dump \
  -U feanalyzer_user feanalyzer \
  | gzip > backup_$(date +%Y%m%d_%H%M%S).sql.gz

# Restore from a backup
gunzip -c backup_YYYYMMDD_HHMMSS.sql.gz \
  | docker compose exec -T db psql -U feanalyzer_user feanalyzer
```

### Monitor Resource Usage

```bash
# Live CPU / memory per container
docker stats

# Disk used by volumes
docker system df -v
```

### Prune Old Images

```bash
# Remove unused images and build cache (frees disk space after updates)
docker image prune -af
docker builder prune -af
```

---

## 7. Troubleshooting

### Container fails to start — check the logs first

```bash
docker compose logs web
docker compose logs celery
```

### `django.db.OperationalError` or connection refused

The web container could not reach the database. Verify:

```bash
# Is the db container healthy?
docker compose ps db

# Test the connection manually
docker compose exec web python -c \
  "import django; django.setup(); from django.db import connection; connection.ensure_connection(); print('OK')"
```

If `db` is not healthy, check `POSTGRES_PASSWORD` in `.env.production` — a mismatch between the password the container was first initialized with and the current value is the most common cause.

> **Fix:** Stop the stack, delete the `postgres_data` volume (`docker compose down -v`), correct the password, and restart. This erases all data, so restore from backup if needed.

### `400 Bad Request` on the API

Django's `ALLOWED_HOSTS` or `CSRF_TRUSTED_ORIGINS` does not include your domain. Update `.env.production` and restart:

```bash
docker compose --env-file .env.production up -d web
```

### Celery tasks stuck in `PENDING`

1. Verify Redis is running: `docker compose ps redis`
2. Check the Celery worker for errors: `docker compose logs celery`
3. Verify `CELERY_BROKER_URL` in `.env.production` uses `redis://redis:6379/0` (the internal service name, not `localhost`)

### Frontend shows stale content after deployment

The React assets are hashed by Vite (`/assets/main-AbCdEf.js`) so browsers always get fresh files. If `index.html` is cached, force-refresh with `Ctrl+Shift+R`. The Nginx config sets `Cache-Control: no-store` on `index.html` to prevent this.

### SSL certificate not renewing

```bash
# Check the certbot timer
sudo systemctl status certbot.timer

# Test renewal manually
sudo certbot renew --dry-run

# If host nginx is misconfigured, test and reload
sudo nginx -t && sudo systemctl reload nginx
```

### `docker compose config` fails with substitution errors

The `.env.production` file must be present in the project root. On a fresh clone, it is there (with `CHANGE_ME_*` values). If you accidentally deleted it, restore it from Git:

```bash
git checkout HEAD -- .env.production
```

---

## Quick-Reference Cheat Sheet

| Task | Command |
|---|---|
| Start stack | `docker compose --env-file .env.production up -d` |
| Stop stack | `docker compose down` |
| View all logs | `docker compose logs -f` |
| View service logs | `docker compose logs -f <service>` |
| Update deployment | `git pull && docker compose --env-file .env.production up -d --build` |
| Backup database | `docker compose exec db pg_dump -U feanalyzer_user feanalyzer \| gzip > backup.sql.gz` |
| Open Django shell | `docker compose exec web python manage.py shell` |
| Run migrations | `docker compose exec web python manage.py migrate` |
| Container health | `docker compose ps` |
| Resource usage | `docker stats` |
| Free disk space | `docker image prune -af && docker builder prune -af` |
