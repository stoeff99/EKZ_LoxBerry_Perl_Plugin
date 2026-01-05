# EKZ LoxBerry Perl Plugin

A LoxBerry plugin that fetches EKZ electricity tariffs and publishes them to MQTT. It includes an OAuth2/OIDC sign‑in to EKZ, an EMS “linking” flow, a rolling fetch window, and a simple web UI.

Important: You must host this plugin behind your own publicly‑reachable domain with HTTPS and a reverse proxy (e.g., Caddy or Nginx). EKZ’s identity provider must be able to redirect back to your domain on the plugin’s callback URL.

---

## Table of contents

- What you need
- Architecture overview
- Reverse proxy examples
  - Caddy
  - Nginx
- EKZ OIDC client setup
- Install the plugin
- Configure the plugin
- Sign-in and EMS linking
- Fetching and MQTT
- Scheduling (cron)
- Logs and troubleshooting
- Security notes
- Uninstall

---

## What you need

- A LoxBerry installation (tested on LoxBerry 3.x).
- A publicly reachable HTTPS domain you control (e.g., `https://ems.yourdomain.tld`).
- A reverse proxy in front of LoxBerry (Caddy or Nginx) that forwards requests for your domain to LoxBerry.
- Basic Perl runtime (bundled with LoxBerry) and these extras:
  - `mosquitto-clients` package (for CLI fallback to publish MQTT):  
    `sudo apt-get update && sudo apt-get install -y mosquitto-clients`
- An EKZ OIDC client configured for your domain’s callback URL.

---

## Architecture overview

- Browser opens plugin UI at `https://<your-domain>/admin/plugins/ekz_plugin/`.
- “Sign in (OIDC)” redirects to EKZ’s identity provider.
- EKZ redirects back to your callback URL:  
  `https://<your-domain>/admin/plugins/ekz_plugin/callback.cgi`
- Plugin exchanges the `code` for tokens and stores them in JSON.
- Plugin checks EMS link status and, if needed, redirects the user to link EMS.
- Scheduled or manual fetch reads tariffs (customer first, fallback to public), persists JSON, and publishes to MQTT.

---

## Reverse proxy examples

The proxy must terminate TLS and forward `/admin/plugins/ekz_plugin/*` to your LoxBerry host. It does not need special headers beyond the standard proxying; the plugin runs CGI under LoxBerry.

### Caddy (recommended)

```caddyfile
# Caddyfile
ems.yourdomain.tld {
  encode gzip
  tls you@yourdomain.tld

  # Proxy everything to your LoxBerry host
  reverse_proxy 192.168.20.45 {
    header_up X-Forwarded-Proto {scheme}
    header_up X-Forwarded-Host {host}
    header_up X-Forwarded-For {remote_host}
  }

  # Optional: increase limits if needed
  request_body {
    max_size 20MB
  }
}
```

### Nginx

```nginx
server {
  listen 443 ssl;
  server_name ems.yourdomain.tld;

  ssl_certificate     /etc/letsencrypt/live/ems.yourdomain.tld/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/ems.yourdomain.tld/privkey.pem;

  location / {
    proxy_pass http://192.168.20.45;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
  }
}
```

Common pitfalls:
- 502 on callback: proxy not forwarding to LoxBerry correctly or callback URL path is wrong.
- Redirect URI mismatch: EKZ sends back to your domain root or a different path; must match exactly the plugin’s callback.

---

## EKZ OIDC client setup

Create or update a client at EKZ with:

- Realm: `myEKZ` (per your environment).
- Auth server base:
  - Production: `https://login.ekz.ch/auth`
  - Test: `https://login-test.ekz.ch/auth`
- Client ID and Secret: your application credentials from EKZ.
- Redirect URI (exact match!):  
  `https://ems.yourdomain.tld/admin/plugins/ekz_plugin/callback.cgi`
- Response mode: `query`
- Scope:
  - `openid`
  - `offline_access` (recommended, for refresh tokens)
- API base:
  - Production: `https://api.tariffs.ekz.ch/v1`
  - Test: `https://test-api.tariffs.ekz.ch/v1`

Make sure your plugin config uses either all “test” endpoints or all “production” endpoints; do not mix.

---

## Install the plugin

- Clone or download this repository into LoxBerry’s plugin path or install via the LoxBerry plugin manager if packaged.
- Place an optional banner image:  
  `webfrontend/htmlauth/Icons/banner.jpg`
- Ensure the plugin files are readable by LoxBerry’s web server.

---

## Configure the plugin

The plugin’s runtime configuration is stored in a common JSON file:

- Path:  
  `/opt/loxberry/data/plugins/ekz_plugin/ekz_config.json`

You can configure via the web UI at:
- `https://<your-domain>/admin/plugins/ekz_plugin/settings.cgi`

Key fields in the JSON:
- `auth_server_base`, `realm`, `client_id`, `client_secret`
- `redirect_uri` (the callback URL on your domain)
- `api_base` (EKZ tariffs API)
- `ems_instance_id` (identifier used for customer tariffs)
- `scope`, `response_mode`, `timezone`
- `mqtt_enabled`, `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password`
- `mqtt_topic_raw`, `mqtt_topic_summary`
- `fallback_tariff_name`
- `retries` (HTTP/API retry attempts)
- `token_store_path` (optional custom path for tokens)
- `fetch_schedule` (see Scheduling below)

Saving settings will write to the JSON file and update the cron wrapper.

---

## Sign‑in and EMS linking

Steps:
1. Open `index.cgi` and click “Sign in (OIDC)”.
2. Authenticate at EKZ; you’ll be redirected back to the plugin’s `callback.cgi`.
3. The plugin exchanges the code for tokens and stores them (refresh + access).
4. The plugin checks EMS link status:
   - If `link_required`, you’ll be shown a link to complete the EMS linking flow at EKZ.
   - After linking, return to the plugin UI.

Tokens are stored with mode `0640`. If you enable `offline_access`, refresh tokens are used to renew access tokens automatically.

---

## Fetching and MQTT

- Manual fetch: click “Fetch now (rolling 24h)” on the UI.
- MQTT:
  - The plugin tries `Net::MQTT::Simple`. If the library refuses insecure password login (common on port 1883), it falls back to `mosquitto_pub`.
  - Install `mosquitto-clients` for the fallback.
- Topics:
  - Raw data: `mqtt_topic_raw` (e.g., `ekz/ems/tariffs/raw`)
  - Summary: `mqtt_topic_summary` (e.g., `ekz/ems/tariffs/now_plus_24h`)

Tariffs JSON is persisted in:
- `/opt/loxberry/data/plugins/ekz_plugin/tariffs_latest.json`
- Plus windowed files named with the fetch interval.

---

## Scheduling (cron)

Schedules are hour‑based for reliability:

- 1× per day at 19:00
- 2× per day at 07:00 and 19:00
- 12× per day (every 2 hours, even hours)
- 24× per day (every hour)

Implementation details:
- A small wrapper script is written to LoxBerry’s `cron.hourly` directory.
- The wrapper checks the current hour and executes the fetch CGIs via Perl:
  `/usr/bin/env perl "<path>/run_rolling_fetch.cgi"`

After saving settings, verify:
- Wrapper exists and is executable:
  - `/opt/loxberry/system/cron/cron.hourly/ekz_plugin`
- Log shows runs around expected times.

---

## Logs and troubleshooting

Plugin log:
- `/opt/loxberry/log/plugins/ekz_plugin/fetch.log`

Cron update log (if an error occurs while writing cron):
- `/opt/loxberry/data/plugins/ekz_plugin/cron_update.log`

Fetch records (ring buffer - last 10 successful fetches):
- `/opt/loxberry/data/plugins/ekz_plugin/fetch_records/fetch_record_00.json` (most recent)
- `/opt/loxberry/data/plugins/ekz_plugin/fetch_records/fetch_record_09.json` (oldest)
- See [docs/RING_BUFFER.md](docs/RING_BUFFER.md) for details

Common issues:
- 401 unauthorized_client during sign‑in:
  - Check `client_id/client_secret`, realm, and environment (prod vs test).
  - Ensure `redirect_uri` matches exactly the EKZ client setting.
- 502 on callback:
  - Reverse proxy not forwarding `/admin/plugins/ekz_plugin/`.
  - Callback URL points to the wrong path (e.g., site root).
- No scheduled fetch:
  - Ensure `fetch_schedule` is set.
  - Confirm the cron wrapper exists and is executable.
  - Check that your LoxBerry time/timezone is correct.
- MQTT publish failures:
  - Install `mosquitto-clients`.
  - Verify broker credentials and connectivity.
  - Ensure the broker allows credentials on port 1883 (or use TLS).

---

## Security notes

- Use HTTPS on your domain; EKZ redirects should not use plain HTTP.
- Keep `client_secret` confidential; it is only updated if you enter a new value in the UI.
- Token JSON is written with `0640` permissions; consider a dedicated secure path via `token_store_path`.

---

## Uninstall

- Remove the plugin directory from LoxBerry’s plugins.
- Delete the runtime data (optional):
  - `/opt/loxberry/data/plugins/ekz_plugin/`
- Remove cron wrappers:
  - `/opt/loxberry/system/cron/cron.hourly/ekz_plugin`

---

## Disclaimer

This project is not affiliated with EKZ. Use at your own risk. API endpoints, realms, and authentication requirements are controlled by EKZ and may change.

If you encounter issues or have feature requests, please open an issue with logs and your environment details (domain, reverse proxy, and which EKZ environment you’re using).
