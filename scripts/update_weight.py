#!/usr/bin/env python3
"""
Appends (or updates) one weight/body-composition point in
data/dashboard-data.json, from a repository_dispatch payload sent by an
iPhone Shortcuts automation (see README.md — "Apple Health" section).

GitHub Actions cannot read Apple Health itself: Apple doesn't expose a
cloud API for HealthKit data, so the phone has to be the one sending it.
This script just trusts whatever numbers arrive in the payload and writes
them — the Shortcut is what decides what counts as "today's weight".

Expected environment variables (set from client_payload by the workflow):
    WEIGHT_DATE       required, YYYY-MM-DD
    WEIGHT_KG         required, float
    LEAN_MASS_KG      optional, float
    BODY_FAT_PCT      optional, float
    BMI               optional, float
"""

import json
import os
import sys

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "dashboard-data.json")


def main():
    date_str = os.environ.get("WEIGHT_DATE", "").strip()
    weight_str = os.environ.get("WEIGHT_KG", "").strip()
    if not date_str or not weight_str:
        print("WEIGHT_DATE and WEIGHT_KG are required.", file=sys.stderr)
        sys.exit(1)

    try:
        weight = round(float(weight_str), 1)
    except ValueError:
        print(f"WEIGHT_KG is not a number: {weight_str!r}", file=sys.stderr)
        sys.exit(1)

    point = {"d": date_str, "v": weight, "source": "scale"}

    for field, env_name in [
        ("lean_mass_kg", "LEAN_MASS_KG"),
        ("body_fat_pct", "BODY_FAT_PCT"),
        ("bmi", "BMI"),
    ]:
        raw = os.environ.get(env_name, "").strip()
        if raw:
            try:
                point[field] = round(float(raw), 1)
            except ValueError:
                print(f"[warn] ignoring non-numeric {env_name}={raw!r}", file=sys.stderr)

    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    weight_list = data.setdefault("weight", [])
    for i, existing in enumerate(weight_list):
        if existing["d"] == date_str:
            # same day already recorded (e.g. automation re-ran) — merge in
            # any new fields rather than duplicating the row
            existing.update(point)
            break
    else:
        weight_list.append(point)

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Recorded weight for {date_str}: {point}")


if __name__ == "__main__":
    main()
