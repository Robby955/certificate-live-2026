# certificate-live-2026

Daily precipitation predictions for 24 cities, committed and externally timestamped before the day they predict, and exact Brier-risk certificates issued after the outcomes resolve. See `PROTOCOL.md` for what is claimed and what is not.

Layout

- `frozen/`: the design, fitted model, tilt catalog and protocols, copied from the weather-calibration-v1 study and never changed here.
- `jobs/live.py`: the daily job (`predict`, `anchor`, `upgrade`, `resolve`, `certify`, `status`, `dryrun`).
- `live/predictions.csv`, `live/outcomes.csv`: the tables, append-only.
- `live/manifests/DATE.json`: per-day hashes of the raw response and the table at the time of the prediction commit.
- `live/anchors/DATE.json.ots`: OpenTimestamps proof requested on the manifest; `DATE.request.json` records the request time; `DATE.status.json` records confirmation checks.
- `live/raw/`: retained provider responses. `live/fetch-log.jsonl`: every request with status, time and hash.
- `live/receipts/DATE/`: certificates issued on the prefix resolved through day D, with the prepared table and both receipt bundles.

Replay a day's timing, shown for the valid day 2026-09-21 whose forecast run is 2026-09-20

```
sha256sum live/raw/forecast-2026-09-20.json live/predictions.csv
ots verify live/anchors/2026-09-21.json.ots -f live/manifests/2026-09-21.json
git log --format='%H %cI' -- live/manifests/2026-09-21.json
```

Replay a certificate (needs the q100 source tree at revision 7115830 with its Lean toolchain)

```
python3 scripts/formalslt_brier_mixture.py verify live/receipts/2026-09-28/calibrated_gfs/certificate.json --protocol frozen/protocols/calibrated_gfs.json --data live/receipts/2026-09-28/prepared.csv
python3 scripts/formalslt_brier_mixture.py verify live/receipts/2026-09-28/constant_fit/certificate.json --protocol frozen/protocols/constant_fit.json --data live/receipts/2026-09-28/prepared.csv
```

Data attribution: forecasts from Open-Meteo (NOAA/NCEP GFS), outcomes from Open-Meteo (ECMWF/Copernicus ERA5), both CC BY 4.0.
