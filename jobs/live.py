#!/usr/bin/env python3
"""Prospective certificate run: predictions committed before outcomes, certificates after.

Stages (each idempotent, each leaves a record):
  predict  --valid-date D   fetch the (D-1) 00Z GFS run, compute both frozen predictions for D, append rows,
                            write the manifest for D, and request an OpenTimestamps anchor on it
  upgrade                   try to upgrade pending anchors to confirmed Bitcoin attestations
  resolve  --valid-date D   fetch the ERA5 day D (available about five days later), append outcomes
  certify                   issue, replay and kernel-check both certificates on the resolved prefix
  dryrun   --raw-dir DIR    run predict and resolve offline against retained raw responses and compare
                            with the frozen study's prepared table (plumbing test, not a timing test)
  status                    print what is committed, anchored, resolved and certified

Everything numerical is byte-for-byte the frozen weather-calibration-v1 design: same 24 cities, same
00Z run and 24-hour aggregation, same six bins and Laplace-smoothed probabilities fitted on April 3 to 24,
same quantization, same catalog, same delta and prior. Nothing is fitted here.
"""
from __future__ import annotations

import argparse, csv, hashlib, io, json, shutil, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
FROZEN = HERE / "frozen"
LIVE = HERE / "live"
SOURCE = Path("/Users/robsneiderman/Projects/FormalSLT-q100-verify-20260919")  # built q100 tree; replay needs only its scripts and Lean
OTS = Path.home() / "Library/Python/3.13/bin/ots"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def design():
    return json.loads((FROZEN / "design.json").read_text())


def model():
    return json.loads((FROZEN / "model.json").read_text())


# ---------- frozen numerics, copied from the study (weather-calibration-v1/study.py) ----------

def responses(path: Path, d):
    value = json.loads(path.read_text(), parse_float=Decimal,
                       parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    if not isinstance(value, list) or len(value) != len(d["locations"]):
        raise ValueError(f"expected one response per location: {path}")
    result = []
    for index, item in enumerate(value):
        if item.get("location_id", index) != index or item["utc_offset_seconds"] != 0:
            raise ValueError("location index or UTC offset mismatch")
        if item["hourly_units"].get("precipitation") != "mm":
            raise ValueError("precipitation units must be mm")
        hours = item["hourly"]["time"]; amounts = item["hourly"]["precipitation"]
        if len(hours) != len(amounts) or len(set(hours)) != len(hours):
            raise ValueError("hour arrays are ragged or duplicated")
        loc = d["locations"][index]
        if abs(float(item["latitude"]) - loc["latitude"]) > 1 or abs(float(item["longitude"]) - loc["longitude"]) > 1:
            raise ValueError("response grid coordinate not near requested location")
        result.append(dict(zip(hours, amounts, strict=True)))
    return result


def daily_amount(series, day: date) -> Fraction:
    midnight = datetime.combine(day, datetime.min.time())
    total = Fraction(0)
    for hour in range(1, 25):
        key = (midnight + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%M")
        x = series.get(key)
        if isinstance(x, bool) or not isinstance(x, (int, Decimal)):
            raise ValueError(f"missing/non-numeric required precipitation at {key}: {x!r}")
        value = Fraction(x)
        if value < 0:
            raise ValueError("negative required precipitation")
        total += value
    return total


def bin_index(amount: Fraction) -> int:
    for i, edge in enumerate(map(Fraction, ("0", "1/5", "1", "5", "10"))):
        if amount <= edge:
            return i
    return 5


def quantize(p: Fraction) -> int:
    value = p * 65535 + Fraction(1, 2)
    return value.numerator // value.denominator


# ---------- transport ----------

def fetch(url: str, target: Path, phase: str, valid: str) -> None:
    """Fetch once, retain the bytes and headers, refuse to overwrite."""
    if target.exists():
        raise ValueError(f"response already retained, refusing to refetch: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    log = LIVE / "fetch-log.jsonl"
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                raw, status, headers = r.read(), r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            raw, status, headers = e.read(), e.code, dict(e.headers)
        entry = {"phase": phase, "valid_date": valid, "url": url, "http_status": status, "attempt": attempt,
                 "retrieved_utc": now_utc(), "bytes": len(raw), "sha256": sha_bytes(raw),
                 "date_header": headers.get("Date")}
        with log.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        if status in (429, 500, 502, 503, 504) and attempt < 3:
            time.sleep(65); continue
        if status != 200:
            raise ValueError(f"stopped on HTTP {status}: {raw[:200]!r}")
        target.write_bytes(raw)
        return


def coords(d):
    locs = d["locations"]
    return {"latitude": ",".join(str(x["latitude"]) for x in locs), "longitude": ",".join(str(x["longitude"]) for x in locs),
            "timezone": "UTC", "hourly": "precipitation", "precipitation_unit": "mm"}


# ---------- stages ----------

def predict(valid: date, raw_dir: Path | None = None) -> dict:
    d, m = design(), model()
    run = valid - timedelta(days=1)
    if raw_dir is None:
        target = LIVE / "raw" / f"forecast-{run}.json"
        params = {**coords(d), "models": d["forecast"]["model"], "run": f"{run}T00:00", "forecast_days": 3}
        fetch(d["forecast"]["endpoint"] + "?" + urllib.parse.urlencode(params), target, "forecast", str(valid))
    else:
        target = raw_dir / f"forecast-{run}.json"
    forecast = responses(target, d)
    rows = []
    for i, loc in enumerate(d["locations"]):
        amount = daily_amount(forecast[i], valid)
        calibrated = Fraction(m["bin_counts"][bin_index(amount)]["probability"])
        rows.append({"valid_date": str(valid), "location": loc["id"], "forecast_mm": str(amount),
                     "calibrated_gfs_q": quantize(calibrated), "constant_fit_q": quantize(Fraction(m["constant_probability"])),
                     "run": f"{run}T00:00Z", "raw_sha256": sha(target), "predicted_utc": now_utc()})
    LIVE.mkdir(parents=True, exist_ok=True)
    table = LIVE / "predictions.csv"
    existing = list(csv.DictReader(table.open())) if table.exists() else []
    if any(r["valid_date"] == str(valid) for r in existing):
        raise ValueError(f"predictions for {valid} already recorded")
    fields = ["valid_date", "location", "forecast_mm", "calibrated_gfs_q", "constant_fit_q", "run", "raw_sha256", "predicted_utc"]
    with table.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        if not existing:
            w.writeheader()
        for r in rows:
            w.writerow(r)
    manifest = {"valid_date": str(valid), "run": f"{run}T00:00Z", "raw_file": str(target.relative_to(HERE)) if raw_dir is None else str(target),
                "raw_sha256": sha(target), "predictions_sha256": sha(table), "rows": rows, "written_utc": now_utc(),
                "frozen": {"design_sha256": sha(FROZEN / "design.json"), "model_sha256": sha(FROZEN / "model.json"),
                           "catalog_sha256": sha(FROZEN / "catalog.json")}}
    write_json(LIVE / "manifests" / f"{valid}.json", manifest)
    return manifest


def anchor(valid: date) -> dict:
    """Request an OpenTimestamps attestation on the manifest; record request time; confirmation comes later."""
    manifest = LIVE / "manifests" / f"{valid}.json"
    proof = LIVE / "anchors" / f"{valid}.json.ots"
    proof.parent.mkdir(parents=True, exist_ok=True)
    if proof.exists():
        return {"status": "already_requested"}
    result = subprocess.run([str(OTS), "stamp", str(manifest)], capture_output=True, text=True, timeout=300)
    made = manifest.with_suffix(".json.ots")
    if made.exists():
        shutil.move(str(made), str(proof))
    record = {"valid_date": str(valid), "manifest_sha256": sha(manifest), "anchor_requested_utc": now_utc(),
              "ots_exit": result.returncode, "ots_output": (result.stdout + result.stderr)[-600:],
              "proof_present": proof.exists(), "qualifies_if_confirmed_before_utc": f"{valid}T00:00:00+00:00"}
    write_json(LIVE / "anchors" / f"{valid}.request.json", record)
    return record


def upgrade() -> list:
    out = []
    for proof in sorted((LIVE / "anchors").glob("*.json.ots")):
        r = subprocess.run([str(OTS), "upgrade", str(proof)], capture_output=True, text=True, timeout=300)
        v = subprocess.run([str(OTS), "verify", str(proof), "-f", str(LIVE / "manifests" / proof.name.replace(".json.ots", ".json"))],
                           capture_output=True, text=True, timeout=300)
        rec = {"proof": proof.name, "upgrade_exit": r.returncode, "verify_exit": v.returncode,
               "verify_output": (v.stdout + v.stderr)[-500:], "checked_utc": now_utc()}
        write_json(LIVE / "anchors" / proof.name.replace(".json.ots", ".status.json"), rec)
        out.append(rec)
    return out


def resolve(valid: date, raw_dir: Path | None = None) -> dict:
    d = design()
    if raw_dir is None:
        target = LIVE / "raw" / f"outcome-{valid}.json"
        params = {**coords(d), "models": d["outcome"]["model"], "start_date": str(valid), "end_date": str(valid + timedelta(days=1))}
        fetch(d["outcome"]["endpoint"] + "?" + urllib.parse.urlencode(params), target, "outcome", str(valid))
        outcome = responses(target, d)
    else:
        target = raw_dir / "outcome.json"
        outcome = responses(target, d)
    LIVE.mkdir(parents=True, exist_ok=True)
    table = LIVE / "outcomes.csv"
    existing = list(csv.DictReader(table.open())) if table.exists() else []
    if any(r["valid_date"] == str(valid) for r in existing):
        raise ValueError(f"outcomes for {valid} already recorded")
    rows = []
    for i, loc in enumerate(d["locations"]):
        observed = daily_amount(outcome[i], valid)
        rows.append({"valid_date": str(valid), "location": loc["id"], "outcome_mm": str(observed),
                     "outcome": int(observed >= Fraction(1, 5)), "raw_sha256": sha(target), "resolved_utc": now_utc()})
    with table.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
        if not existing:
            w.writeheader()
        for r in rows:
            w.writerow(r)
    return {"valid_date": str(valid), "rows": len(rows), "positives": sum(r["outcome"] for r in rows)}


def prepared_rows():
    """Join predictions and outcomes in the frozen order: valid date, then alphabetical city id."""
    preds = {(r["valid_date"], r["location"]): r for r in csv.DictReader((LIVE / "predictions.csv").open())}
    outs = {(r["valid_date"], r["location"]): r for r in csv.DictReader((LIVE / "outcomes.csv").open())}
    keys = sorted(k for k in outs if k in preds)
    rows = []
    for i, k in enumerate(keys, 1):
        rows.append({"sequence_index": i, "outcome": outs[k]["outcome"], "constant_fit_q": preds[k]["constant_fit_q"],
                     "calibrated_gfs_q": preds[k]["calibrated_gfs_q"], "valid_date": k[0], "location": k[1]})
    return rows


def certify() -> dict:
    rows = prepared_rows()
    n = len(rows)
    if n < 4:
        return {"status": "TOO_FEW_RESOLVED_ROWS", "rows": n}
    last = rows[-1]["valid_date"]
    out_dir = LIVE / "receipts" / last
    if out_dir.exists():
        return {"status": "already_certified", "through": last, "rows": n}
    out_dir.mkdir(parents=True)
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["sequence_index", "outcome", "constant_fit_q", "calibrated_gfs_q"])
    for r in rows:
        w.writerow([r["sequence_index"], r["outcome"], r["constant_fit_q"], r["calibrated_gfs_q"]])
    data = out_dir / "prepared.csv"; data.write_text(buf.getvalue())
    write_json(out_dir / "rows.json", rows)
    results = {}
    for selected in ("calibrated_gfs", "constant_fit"):
        protocol = FROZEN / "protocols" / f"{selected}.json"
        target = out_dir / selected
        cmd = [sys.executable, str(SOURCE / "scripts/formalslt_brier_mixture.py"), "issue", str(protocol), str(data), str(target)]
        r = subprocess.run(cmd, cwd=SOURCE, capture_output=True, text=True, timeout=1800)
        v = subprocess.run([sys.executable, str(SOURCE / "scripts/formalslt_brier_mixture.py"), "verify", str(target / "certificate.json"),
                            "--protocol", str(protocol), "--data", str(data)], cwd=SOURCE, capture_output=True, text=True, timeout=1800)
        results[selected] = {"issue_exit": r.returncode, "issue_tail": (r.stdout + r.stderr)[-400:],
                             "verify_exit": v.returncode, "verify_tail": (v.stdout + v.stderr)[-400:]}
        cert = target / "certificate.json"
        if cert.exists():
            c = json.loads(cert.read_text())
            results[selected]["upper_bound"] = c["claim"]["upper_bound"]
            results[selected]["empirical"] = c["statistics"]["posterior_empirical_brier_risk"]
    summary = {"status": "CERTIFIED" if all(v.get("issue_exit") == 0 and v.get("verify_exit") == 0 for v in results.values()) else "FAILED",
               "through_valid_date": last, "rows": n, "results": results, "certified_utc": now_utc(),
               "data_sha256": sha(data), "source": str(SOURCE)}
    write_json(out_dir / "summary.json", summary)
    return summary


def dryrun(raw_dir: Path, dates: list[date], prepared_csv: Path) -> dict:
    """Offline plumbing test on retained study responses; compares quantized predictions with the frozen table."""
    scratch = HERE / "dryrun"
    if scratch.exists():
        shutil.rmtree(scratch)
    global LIVE
    LIVE = scratch
    for v in dates:
        predict(v, raw_dir=raw_dir)
        resolve(v, raw_dir=raw_dir)
    rows = prepared_rows()
    frozen = list(csv.DictReader(prepared_csv.open()))
    d = design()
    ncity = len(d["locations"])
    # the frozen table is ordered valid date then city, starting 2026-05-02; find the block for the first dryrun date
    start = (dates[0] - date.fromisoformat(d["evaluation"]["valid_date_start"])).days * ncity
    ref = frozen[start:start + len(rows)]
    mismatches = [(a, b) for a, b in zip(rows, ref) if (int(a["outcome"]), int(a["constant_fit_q"]), int(a["calibrated_gfs_q"])) != (int(b["outcome"]), int(b["constant_fit_q"]), int(b["calibrated_gfs_q"]))]
    return {"dates": [str(v) for v in dates], "rows": len(rows), "reference_rows": len(ref), "mismatches": len(mismatches),
            "first_mismatch": mismatches[0] if mismatches else None}


def status() -> dict:
    p = LIVE / "predictions.csv"; o = LIVE / "outcomes.csv"
    preds = list(csv.DictReader(p.open())) if p.exists() else []
    outs = list(csv.DictReader(o.open())) if o.exists() else []
    anchors = sorted(x.name for x in (LIVE / "anchors").glob("*.request.json")) if (LIVE / "anchors").exists() else []
    receipts = sorted(x.name for x in (LIVE / "receipts").iterdir()) if (LIVE / "receipts").exists() else []
    return {"prediction_days": sorted({r["valid_date"] for r in preds}), "outcome_days": sorted({r["valid_date"] for r in outs}),
            "anchors": anchors, "receipts": receipts}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["predict", "anchor", "upgrade", "resolve", "certify", "dryrun", "status"])
    ap.add_argument("--valid-date")
    ap.add_argument("--raw-dir")
    ap.add_argument("--dates", help="comma-separated valid dates for dryrun")
    ap.add_argument("--prepared-csv")
    a = ap.parse_args()
    if a.stage == "predict":
        print(json.dumps({"predicted": predict(date.fromisoformat(a.valid_date))["valid_date"]}))
    elif a.stage == "anchor":
        print(json.dumps(anchor(date.fromisoformat(a.valid_date))))
    elif a.stage == "upgrade":
        print(json.dumps(upgrade()))
    elif a.stage == "resolve":
        print(json.dumps(resolve(date.fromisoformat(a.valid_date))))
    elif a.stage == "certify":
        print(json.dumps(certify()))
    elif a.stage == "dryrun":
        dates = [date.fromisoformat(x) for x in a.dates.split(",")]
        print(json.dumps(dryrun(Path(a.raw_dir), dates, Path(a.prepared_csv))))
    else:
        print(json.dumps(status(), indent=1))


if __name__ == "__main__":
    main()
