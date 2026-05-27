# Nginx traffic monitoring

Daily (or periodic) scraper that counts nginx **combined** access log lines per site and calendar day, excluding User-Agents that match an ignore list. Counts are upserted into MySQL; Grafana charts them.

## Components

| Piece | Role |
|--------|------|
| `scrape.py` | Parse logs → success/fail counts per day → MySQL upsert |
| `test/generate_traffic.py` | Generate HTTP traffic to local nginx (see `test/README.md`) |
| MySQL (Docker) | One row per `(site, day)` |
| Grafana (Docker) | MySQL datasource + provisioned dashboard |

## Quick start

### 1. Environment

```bash
cp .env.example .env
cp config.example.json config.json
# Edit config.json: sites, log paths, mysql password (must match .env)
```

Update `grafana/provisioning/datasources/mysql.yml` → `secureJsonData.password` to match `MYSQL_PASSWORD` in `.env`.

### 2. MySQL

```bash
docker compose -f docker-compose.mysql.yml up -d
```

Creates database `nginx_monitoring`, user `nginx_monitor`, network `nginx_monitoring`, port `3306`.

### 3. Grafana

```bash
docker compose -f docker-compose.grafana.yml up -d
```

Open http://localhost:3000 (default `admin` / `admin` from `.env`). Dashboards under folder **Nginx Monitoring**:

- **Nginx daily traffic** — one site at a time (success / fail)
- **Nginx traffic — all sites** — every site on one chart plus **ALL (combined)** total lines, and a latest-day table

### 4. Scraper (on the nginx host)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp ignore_agents.example.txt ignore_agents.txt   # if needed
python scrape.py -c config.json
```

Install on server (example path):

```bash
sudo mkdir -p /opt/nginx_monitoring
sudo cp -r . /opt/nginx_monitoring/
cd /opt/nginx_monitoring && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

### 5. Schedule with cron (on the nginx host)

Example entries are in `cron/nginx-traffic-scrape.cron.example`. Install as root (or a user that can read nginx logs):

```bash
sudo crontab -e
```

Every 6 hours:

```cron
0 */6 * * * /opt/nginx_monitoring/.venv/bin/python /opt/nginx_monitoring/scrape.py -c /opt/nginx_monitoring/config.json >> /var/log/nginx-traffic-scrape.log 2>&1
```

Once per day (after logrotate):

```cron
30 1 * * * /opt/nginx_monitoring/.venv/bin/python /opt/nginx_monitoring/scrape.py -c /opt/nginx_monitoring/config.json >> /var/log/nginx-traffic-scrape.log 2>&1
```

Use absolute paths. Ensure MySQL is up before the job runs (e.g. run Docker MySQL on the same host, or point `config.json` at a remote DB).

## Configuration

**`config.json`**

| Field | Description |
|--------|-------------|
| `sites[].name` | Site id stored in MySQL |
| `sites[].log_dir` | Directory containing access logs |
| `sites[].log_file` | Log filename (e.g. `access.log`) |
| `ignore_agents_file` | Path to ignore list (one substring per line) |
| `mysql` | Connection settings (overridable via env) |

**Environment overrides** (optional): `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`.

**Logs read:** `{log_dir}/{log_file}` and `{log_file}.1` if present (plain or `.gz`).

**Log format:** nginx `combined` (`$time_local`, `$http_user_agent` as last quoted field).

## MySQL schema

Created automatically on first run:

```sql
nginx_daily_traffic (site, day, success_count, fail_count, updated_at)
PRIMARY KEY (site, day)
```

**Status rules** (after User-Agent filter):

| Status | Counted as |
|--------|------------|
| 2xx | `success_count` |
| 3xx | skipped (redirect — not counted) |
| 4xx, 5xx, other | `fail_count` |

Re-running the scraper the same day recomputes from the log files and overwrites that day's row. Older tables with a single `count` column are migrated automatically on the next run.

## Ignore list

If the User-Agent contains any line from `ignore_agents.txt` (case-insensitive), the line is not counted. Lines starting with `#` are comments.

## Grafana panel query

```sql
SELECT day AS time, success_count, fail_count
FROM nginx_daily_traffic
WHERE site = '$site'
ORDER BY day
```

Set dashboard refresh to manual or `1h`+ — data only changes when the scraper runs.

## Traffic generator

See **[test/README.md](test/README.md)** to send test requests to nginx (`targets.json`).
