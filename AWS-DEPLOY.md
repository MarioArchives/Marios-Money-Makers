# Deploying Mario's Money Makers on a single EC2 instance

One small VM running two containers with Docker Compose:

```
browser ──:80/:443──> [ frontend: Caddy ]  serves the built React app (dist/)
                            │  /api/*  ──> [ backend: uvicorn/FastAPI ] ──> Alpaca
                            │                      │
                            │                 SQLite cache (named volume)
                       Let's Encrypt (only when DOMAIN is set)
```

- Only Caddy is published to the internet; the backend is reached solely through
  Caddy's `/api` proxy, so page and API share one origin (no CORS, one port).
- The box pulls the **public** repo with `git` and builds the images locally —
  no registry, no CI deploy step, nothing to configure on the AWS side beyond
  the instance itself.
- SQLite is a cache of Alpaca data: the backfill sweep repopulates it on a cold
  start, so there is nothing worth backing up.
- Tested on **Amazon Linux 2023, t3.micro (1 GB RAM + 2 GB swap), eu-north-1**.
  A `t3.small` (2 GB) builds noticeably faster; upgrade later by stop → change
  instance type → start.

Cost: ~$8–9/mo for a t3.micro on-demand (+ ~$3.6/mo for the public IPv4
address, charged whether or not it is an Elastic IP) — or free for 12 months on
the AWS free tier.

---

## 1. AWS side (console)

| What | Where | Why |
|---|---|---|
| Security group: inbound **22** from *My IP*, **80** from anywhere (add **443** only when you set a domain) | EC2 → Security Groups → edit inbound rules | 80/443 is how browsers reach the site; 22 should not be world-open |
| **Elastic IP** → Allocate → Associate with the instance | EC2 → Elastic IPs | the auto-assigned IP changes on every stop/start; an attached EIP costs nothing extra |
| Domain (optional): A record → the Elastic IP | your DNS provider / Route 53 | needed for HTTPS; without it the site is plain HTTP on the IP |

## 2. One-time setup on the box

```bash
ssh -i "<key>.pem" ec2-user@<ip>

# git, Docker, sqlite3 CLI
sudo dnf install -y docker git sqlite
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user

# Docker Compose v2 plugin (not in the AL2023 docker package)
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# 2 GB swap — required on 1 GB instances, or the frontend build gets OOM-killed
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Log out and back in (docker group), then `docker compose version` should work
without sudo.

```bash
# clone (any directory works; /opt/m3 is the conventional spot for deployed apps)
sudo git clone https://github.com/MarioArchives/Marios-Money-Makers.git /opt/m3
sudo chown -R ec2-user:ec2-user /opt/m3
cd /opt/m3

# secrets/config — never committed (.env is gitignored)
cp .env.example .env && chmod 600 .env
nano .env          # KEY_ID, SECRET (Alpaca); leave DOMAIN empty for HTTP-only
```

## 3. Deploy (first time and every time after)

```bash
cd /opt/m3
git pull --ff-only
docker compose -f docker-compose.prod.yml up -d --build
```

First build on a t3.micro takes ~5–8 minutes (npm ci + Vite build + uv sync);
later builds reuse cached layers and take well under a minute unless
`package-lock.json` / `uv.lock` changed.

Check:

```bash
docker compose -f docker-compose.prod.yml ps        # both "running", backend "healthy"
curl -fsS http://127.0.0.1/api/health               # {"status":"ok"}
curl -fsS http://127.0.0.1/api/stocks | head -c 300         # leaderboard JSON (prices null until Alpaca answers)
```

Open `http://<ip>` in a browser. On first start the backend runs its startup
backfill against Alpaca; the leaderboard fills within seconds and the charts
within a minute or two.

Deploy a specific version instead of `master`:

```bash
git fetch && git checkout <sha-or-tag>
docker compose -f docker-compose.prod.yml up -d --build
```

Roll back = check out the previous SHA and run the same `up -d --build`
(`git log --oneline` shows the history).

## 4. Day-2 operations

```bash
# logs
docker compose -f docker-compose.prod.yml logs -f backend
docker compose -f docker-compose.prod.yml logs -f frontend

# restart / stop / start (data volumes survive)
docker compose -f docker-compose.prod.yml restart backend
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml up -d

# change secrets: edit .env, then
docker compose -f docker-compose.prod.yml up -d backend

# disk (8 GB root is tight): drop dangling images after a few deploys
df -h / && docker system df
docker image prune -f

# peek at the SQLite cache
docker compose -f docker-compose.prod.yml exec backend \
  python -c "import sqlite3;c=sqlite3.connect('/app/data/stocks.db');print(c.execute('select tier,count(*) from fetch_log group by tier').fetchall())"

# wipe the cache (it rebuilds itself from Alpaca)
docker compose -f docker-compose.prod.yml down -v && docker compose -f docker-compose.prod.yml up -d

# OS updates
sudo dnf update -y && sudo reboot        # containers restart on their own (restart: unless-stopped)
```

## 5. Adding HTTPS later (optional)

1. Point a DNS A record at the Elastic IP.
2. Open **443** in the security group (80 must stay open — Let's Encrypt's
   HTTP challenge and the HTTP→HTTPS redirect use it).
3. `DOMAIN=m3.example.com` in `.env`, then
   `docker compose -f docker-compose.prod.yml up -d frontend`.

Caddy obtains and renews the certificate by itself (stored in the `caddy-data`
volume). Nothing in the app changes.

## 6. What this deliberately leaves out

- **Registry / CI deploy / image pipeline** — one box and one deployer; `git pull`
  + local build is simpler and just as reproducible (images are built from the
  committed Dockerfiles and lockfiles). Worth adding only with several
  instances or deployers.
- **Backups** — SQLite here is a re-fetchable cache.
- **Multiple backend instances** — SQLite is single-host. The schema already
  tolerates several backend *processes* on the same DB (`summaries` table +
  `fetch_claims` leases), so scaling the backend means moving the DB to
  Postgres/RDS first, not adding boxes.
- **Rate limiting at Caddy** — the backend is DB-first with freshness checks and
  backoff, so an abusive client mostly hits SQLite, not your Alpaca quota. Add
  Caddy's `rate_limit` if that ever changes.
