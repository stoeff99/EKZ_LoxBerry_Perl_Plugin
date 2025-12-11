# EKZ Plugin Scheduling

## Overview

The EKZ Dynamic Price plugin fetches tariff data from EKZ on a configurable schedule. The data is published by EKZ at 18:00 daily for the following day.

## Rolling 24h Window

The plugin uses a **rolling 24-hour window** strategy:
- **Start time**: 18:00 today (in Europe/Zurich timezone)
- **End time**: 18:00 tomorrow (+24 hours)

This ensures you always have:
- Remaining current day data (18:00 → 24:00)
- Full next day data (00:00 → 18:00)

Example: If you run the fetch at 14:30, you still get data starting from 18:00 today through 18:00 tomorrow.

## Schedule Options

Configure the fetch frequency in the Settings page:

### 1x per day (Recommended)
- **Cron**: Daily at 18:05
- **File**: `cron.daily/ekz_loxberry_perl_plugin`
- **Use case**: Normal operation - fetches new data 5 minutes after EKZ publishes it

### 2x per day
- **Cron**: At 18:05 and 06:05
- **File**: `cron.hourly/ekz_loxberry_perl_plugin` (with time check)
- **Use case**: Extra fetch in the morning to catch any late updates

### 12x per day
- **Cron**: Every 2 hours (hourly cron with modulo check)
- **File**: `cron.hourly/ekz_loxberry_perl_plugin`
- **Use case**: Testing or scenarios where you want frequent refreshes

### 24x per day
- **Cron**: Every hour
- **File**: `cron.hourly/ekz_loxberry_perl_plugin`
- **Use case**: Maximum freshness (though data only updates at 18:00)

## How It Works

1. **Settings Save**: When you save settings, `update_cron_schedule()` is called
2. **Old Crons Removed**: All existing cron files for the plugin are deleted
3. **New Cron Created**: A new cron file is written to the appropriate directory
4. **Script Execution**: Cron calls `run_rolling_fetch.cgi` via curl
5. **Data Fetch**: The script:
   - Ensures valid access token (refreshes if needed)
   - Builds the 18:00→18:00+24h window
   - Fetches from `/customerTariffs` (falls back to `/tariffs` if needed)
   - Saves JSON to data directory
   - Publishes to MQTT topics (if enabled)

## Manual Fetch

You can always manually fetch data by:
- Clicking "Fetch now" button in the UI
- Visiting `run_rolling_fetch.cgi` directly
- Running: `curl http://localhost/admin/plugins/ekz_loxberry_perl_plugin/run_rolling_fetch.cgi`

## Cron File Locations

LoxBerry cron directories:
```
/opt/loxberry/system/cron/
├── cron.01min/
├── cron.03min/
├── cron.05min/
├── cron.10min/
├── cron.15min/
├── cron.30min/
├── cron.hourly/   ← Used for 2x, 12x, 24x schedules
└── cron.daily/    ← Used for 1x schedule
```

## Token Management

- Access tokens expire after ~5 minutes
- The plugin automatically refreshes using the `refresh_token`
- Refresh happens 30 seconds before expiry
- If refresh fails, you'll need to sign in again via the UI

## Data Output

Files created:
- **JSON**: `$LBPDATA/ekz_customer_tariffs_now_plus_24h.json`
- **Format**:
  ```json
  {
    "from": "2025-12-10T18:00:00",
    "to": "2025-12-11T18:00:00",
    "source": "customer",
    "interval_count": 96,
    "rows": [
      {"start": "...", "end": "...", "price": 0.123, "unit": "CHF/kWh"},
      ...
    ]
  }
  ```

## MQTT Topics

If MQTT is enabled:
- **Raw topic** (default: `ekz/ems/tariffs/raw`): Full API response
- **Summary topic** (default: `ekz/ems/tariffs/now_plus_24h`): Processed data with window info

## Troubleshooting

### Cron not running
```bash
# Check if cron file exists
ls -la /opt/loxberry/system/cron/*/ekz_loxberry_perl_plugin

# Check cron file permissions (should be 0755)
chmod 0755 /opt/loxberry/system/cron/cron.daily/ekz_loxberry_perl_plugin

# Manually test the cron script
/opt/loxberry/system/cron/cron.daily/ekz_loxberry_perl_plugin
```

### No data fetched
```bash
# Check if tokens exist
cat /opt/loxberry/data/ekz/tokens.json

# Manually run fetch
curl http://localhost/admin/plugins/ekz_loxberry_perl_plugin/run_rolling_fetch.cgi

# Check Apache logs
tail -f /var/log/apache2/error.log
```

### Token expired
- Go to plugin UI and click "Sign in (OIDC)"
- Log in with your EKZ credentials
- Tokens will be refreshed automatically
