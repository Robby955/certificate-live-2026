# certificate-live-2026: prospective replayable Brier-risk certificates

Frozen on 2026-09-20 before the first live prediction. This file, `frozen/` and `jobs/live.py` do not change after the freeze commit; any later change is a new protocol version in a new directory.

## What is committed, and when

- Each UTC day D, after the (D minus 1) 00Z GFS run is published, the job fetches that run for 24 fixed cities, computes two predictions of whether ERA5 daily precipitation on day D reaches 0.2 mm, appends 24 rows to `live/predictions.csv`, writes `live/manifests/D.json` with the SHA-256 of the raw response and of the table, commits, and requests an OpenTimestamps attestation on the manifest (`live/anchors/D.json.ots`, with a request record beside it).
- A prediction row for day D qualifies as pre-outcome when its commit and its anchor request precede D at 00:00 UTC, the start of the day whose precipitation it predicts, and when the anchor is later confirmed by a Bitcoin block whose time also precedes that instant. Rows that miss either condition are kept and marked late in the certificate report; they are never deleted.
- About seven days later, the job fetches the ERA5 day D from the archive, appends 24 outcome rows to `live/outcomes.csv`, commits, and issues, replays and kernel-checks both certificates on the resolved prefix in the frozen order (valid date, then alphabetical city id). Receipts land in `live/receipts/D/`.
- Missed days are recorded in `live/fetch-log.jsonl` and never backfilled: a prediction fetched after its valid day has begun is recorded as late.

## What is frozen

Byte-for-byte copies of the weather-calibration-v1 study that the paper reports: `frozen/design.json` (24 cities and coordinates, the 00Z run, the 24-hour aggregation ending 01:00 through next-day 00:00 UTC, the 0.2 mm event, quantization by floor(65535 p + 1/2)), `frozen/model.json` (six-bin Laplace-smoothed predictor and the pooled constant, fitted on April 3 to 24, 2026), `frozen/catalog.json` (the fifteen-atom rational tilt catalog of q100), and `frozen/protocols/*.json` (prior 1/2 each, delta 1/20). Nothing is fitted or tuned in this repository.

## What a certificate here claims

An exact rational upper endpoint for the posterior-averaged encountered conditional prefix Brier risk of the reported model over the resolved rows, valid at every reporting time and after post-data selection of the model, on the complement of one event of probability at most delta under the bounded predictable-mean model of the theorem. It does not claim station-observed, population, stationary or future risk; ERA5 is a reanalysis proxy; the certificate checks arithmetic and the theorem instance, not the truth of the statistical assumptions. What the anchors add is evidence that the predictions existed before the outcomes did.

## Replay

Anyone can replay a day: recompute the manifest hashes from the raw file and the table, verify the `.ots` proof against the manifest, and run the certificate replay from the q100 source at revision 7115830 with `frozen/protocols/*.json` and `live/receipts/D/prepared.csv`. The README lists the commands.
