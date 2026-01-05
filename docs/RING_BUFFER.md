# Ring Buffer Storage for Fetch Records

## Overview

The EKZ LoxBerry Perl Plugin now maintains a rolling history of the last 10 successful API fetches in a ring buffer. This feature helps with troubleshooting, debugging, and analyzing historical fetch behavior.

## Location

Fetch records are stored in:
```
/opt/loxberry/data/plugins/ekz_plugin/fetch_records/
```

## File Structure

The ring buffer maintains exactly 10 numbered files:
- `fetch_record_00.json` - Most recent fetch (newest)
- `fetch_record_01.json` - Second most recent
- `fetch_record_02.json` - Third most recent
- ...
- `fetch_record_09.json` - Oldest fetch in buffer

## Rotation Logic

When a new fetch completes successfully:
1. `fetch_record_09.json` (oldest) is deleted
2. All existing files are renamed: 08→09, 07→08, ..., 01→02, 00→01
3. New fetch data is written to `fetch_record_00.json`

This ensures that:
- The buffer always contains the last 10 successful fetches
- The most recent fetch is always in `fetch_record_00.json`
- The oldest fetch is always in `fetch_record_09.json`

## Record Structure

Each `fetch_record_XX.json` file contains:

```json
{
  "timestamp": "2026-01-05T19:05:23+01:00",
  "request_id": "1736101523-12345",
  "fetch_metadata": {
    "schedule": "24",
    "force": false,
    "param_today": false,
    "grace_minutes": 5
  },
  "window": {
    "kind": "nextday",
    "from": "2026-01-06T00:00:00+01:00",
    "to": "2026-01-06T23:59:59+01:00"
  },
  "api_response": {
    "source": "customer",
    "intervals": 96,
    "integrated_nonzero_share": "0.9583"
  },
  "raw_payload": {
    ... (full raw API response from EKZ)
  },
  "normalized_payload": {
    ... (normalized/processed data)
  },
  "fallback_applied": false,
  "mqtt_published": true,
  "compute_completed": true
}
```

### Field Descriptions

- **timestamp**: ISO 8601 timestamp when the fetch completed
- **request_id**: Unique identifier for this fetch request (format: `epoch-pid`)
- **fetch_metadata**: Configuration and parameters used for this fetch
  - **schedule**: Fetch schedule configuration (1, 2, 12, or 24)
  - **force**: Whether this was a forced fetch (bypassing schedule)
  - **param_today**: Whether the `today` parameter was used
  - **grace_minutes**: Grace period in minutes for next-day fetches
- **window**: Time window requested from the API
  - **kind**: Either "today" or "nextday"
  - **from**: Start timestamp (ISO 8601)
  - **to**: End timestamp (ISO 8601)
- **api_response**: Summary of the API response
  - **source**: Initial data source ("customer" or "public")
  - **intervals**: Number of 15-minute intervals returned
  - **integrated_nonzero_share**: Ratio of non-zero integrated prices (0.0 to 1.0)
- **raw_payload**: Complete raw API response (includes all price data)
- **normalized_payload**: Processed and normalized data (ready for use)
- **fallback_applied**: Boolean indicating if public tariff fallback was used
- **mqtt_published**: Boolean indicating if MQTT publish succeeded
- **compute_completed**: Boolean indicating if compute_costs.cgi succeeded

## Use Cases

### 1. Compare Data Across Multiple Fetches
```bash
cd /opt/loxberry/data/plugins/ekz_plugin/fetch_records/
jq '.api_response.integrated_nonzero_share' fetch_record_*.json
```

### 2. Debug Fallback Behavior
```bash
jq 'select(.fallback_applied == true) | {timestamp, window, api_response}' fetch_record_*.json
```

### 3. Analyze Source Distribution
```bash
jq '.api_response.source' fetch_record_*.json | sort | uniq -c
```

### 4. Check MQTT Publishing Success Rate
```bash
jq '.mqtt_published' fetch_record_*.json | grep -c true
```

### 5. Review Historical Time Windows
```bash
jq '{timestamp, window}' fetch_record_*.json
```

## File Permissions

- **Directory**: `0750` (rwxr-x---)
- **Files**: `0640` (rw-r-----)

These permissions ensure that:
- The plugin can read/write records
- Other processes with appropriate permissions can read records
- Records are not world-readable (security)

## Error Handling

The ring buffer implementation uses defensive programming:
- All operations are wrapped in `eval {}` blocks
- Failures in ring buffer operations never cause the main fetch to fail
- Errors are logged as warnings to `fetch.log`
- If rotation fails, the new record is still attempted to be written

This ensures reliability: the main fetch process always completes successfully, even if the ring buffer has issues.

## Monitoring

To check if the ring buffer is working:

```bash
# Check that records directory exists
ls -ld /opt/loxberry/data/plugins/ekz_plugin/fetch_records/

# List all records with timestamps
ls -lh /opt/loxberry/data/plugins/ekz_plugin/fetch_records/

# Check the most recent record
cat /opt/loxberry/data/plugins/ekz_plugin/fetch_records/fetch_record_00.json | jq .

# Verify rotation is working (check that timestamps progress)
jq '.timestamp' /opt/loxberry/data/plugins/ekz_plugin/fetch_records/fetch_record_*.json
```

## Storage Considerations

Each record file is typically 50-200 KB depending on the number of intervals (96 for a full day). With 10 records, the total storage usage is approximately 500 KB to 2 MB.

Records are automatically rotated, so no manual cleanup is required. The buffer size is fixed at 10 records.

## Troubleshooting

### No fetch_records directory
The directory is created automatically on the first successful fetch after this feature was added.

### Permission denied errors
Check that the LoxBerry web server user has write permissions to the data directory:
```bash
sudo chown -R loxberry:loxberry /opt/loxberry/data/plugins/ekz_plugin/
```

### Missing records or gaps
Ring buffer operations are non-fatal. Check `fetch.log` for warnings:
```bash
grep "ring buffer" /opt/loxberry/log/plugins/ekz_plugin/fetch.log
```

### Record files not rotating
Ensure that fetches are completing successfully. The ring buffer only saves records for successful fetches (when the full fetch workflow completes without errors).

## Future Enhancements

Potential future improvements:
- Configurable buffer size via `fetch_record_count` in `ekz_config.json`
- Web UI page to browse fetch history
- Comparison tool to visualize differences between fetches
- Export functionality for specific time ranges

## Related Features

- **Event Logging**: `fetch_events-YYYYMMDD.log` - Structured event logs with retention
- **Raw Archiving**: `raw/` subdirectory - Raw API responses with retention
- **Last Fetch**: `last_fetch.json` - Tracks the last successful fetch timestamp

The ring buffer complements these features by providing a comprehensive rolling history of complete fetch operations.
