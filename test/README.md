# Traffic generator

Sends HTTP GET requests to a list of endpoints defined in `targets.json`. Use it to populate nginx access logs (e.g. normal traffic and bot User-Agents like `Googlebot`).

## Setup

```bash
cp test/targets.example.json test/targets.json
```

Edit `test/targets.json` for your nginx vhost.

| Field | Description |
|--------|-------------|
| `ip` | IP to connect to (usually `127.0.0.1`) |
| `host` | `Host` header — must match nginx `server_name` |
| `port` | Listen port (must match nginx, e.g. `8081`) |
| `path` | URL path (e.g. `/`) |
| `calls` | Number of requests for this endpoint |
| `user_agent` | Optional `User-Agent` header (default `NginxMonitorTest/1.0`) |

| Field (root) | Description |
|--------|-------------|
| `timeout_seconds` | Per-request timeout (default `5`) |

**Port:** use the port nginx listens on, e.g. `http://127.0.0.1:8081/` — not bare `http://localhost` (that is port 80).

## Run


```bash
python generate_traffic.py -c targets.json
```

Dry run (no HTTP requests):

```bash
python generate_traffic.py -c targets.json --dry-run
```

## Output

Per endpoint and total:

- **attempted** — requests sent  
- **reached** — got an HTTP response (any status)  
- **success (2xx)** — HTTP 200–299  
- **failed** — connection error or timeout  

Exit code `0` if nothing failed; `1` if any request could not reach the server.

## Check logs

```bash
sudo tail -f /var/log/nginx/test_page_1/access.log
```

Example manual request:

```bash
curl -i http://127.0.0.1:8081/ -H "Host: testpage_1"
```

## Example config

`targets.example.json` includes:

1. 50 requests with a normal test User-Agent  
2. 25 requests with `Googlebot` (for testing scraper ignore rules separately)

## Files

| File | Purpose |
|------|---------|
| `generate_traffic.py` | HTTP client |
| `targets.example.json` | Example endpoints |
| `targets.json` | Your config (gitignored) |
