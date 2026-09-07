#!/usr/bin/env python3
"""
Daily Garmin -> dashboard-data.json updater for the "Мій стан" dashboard.

Runs unattended (GitHub Actions). Logs in to Garmin Connect using a saved
token session (see generate_garmin_session.py) — never a raw password — so
it survives MFA and doesn't need interactive input.

For each day between the last processed date and yesterday (capped at
MAX_BACKFILL_DAYS), it pulls:
  - total steps
  - total sleep hours
  - activities, classified into strength / walk_run / bike / other, and for
    strength_training activities the best (heaviest) set for Bench Press
    (barbell only — dumbbell variants are skipped on purpose, see the
    BENCH_SKIP_NAMES rule below), Lat Pulldown, Leg Press and Hack Squat.

It only ever *adds* to data/dashboard-data.json — existing points are never
edited or removed by this script.

Note: this was written and syntax-checked without a live Garmin session
(no credentials are available to the assistant that wrote it — see
README.md). The exact method names / response shapes below match the
`garminconnect` PyPI package as of 2026; if Garmin changes its API or your
installed version differs, check the failing call's traceback and adjust
the method name — the overall structure (fetch day -> classify -> append)
should not need to change.
"""

import json
import os
import sys
from datetime import date, datetime, timedelta

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "dashboard-data.json")
MAX_BACKFILL_DAYS = 14

# Exercise-name substrings that mean "this is a dumbbell/other non-barbell
# variant — do NOT add it to the Bench Press chart". Case-insensitive.
BENCH_SKIP_NAMES = ["DUMBBELL", "DB ", "MACHINE", "SMITH"]

ACTIVITY_CATEGORY_MAP = {
    "strength_training": "strength",
    "running": "walk_run",
    "treadmill_running": "walk_run",
    "walking": "walk_run",
    "hiking": "walk_run",
    "cycling": "bike",
    "indoor_cycling": "bike",
    "virtual_ride": "bike",
    "road_biking": "bike",
    "mountain_biking": "bike",
}


def load_data():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def month_of(d):
    return d.strftime("%Y-%m")


def bump_monthly(monthly_list, month, value):
    """monthly_list is a list of {"month","sum","count"} dicts, sorted or not.
    Adds one day's value to the matching month, creating it if missing."""
    for m in monthly_list:
        if m["month"] == month:
            m["sum"] += value
            m["count"] += 1
            return
    monthly_list.append({"month": month, "sum": value, "count": 1})


def existing_dates(points):
    return {p["d"] for p in points}


def classify_activity_type(type_key):
    return ACTIVITY_CATEGORY_MAP.get(type_key, "other")


def is_bench_press(category, name):
    if category != "BENCH_PRESS":
        return False
    if name:
        upper = name.upper()
        if any(skip in upper for skip in BENCH_SKIP_NAMES):
            return False
    return True


def best_set_for_category(sets, wanted_category, name_filter=None):
    """sets: list of exercise-set dicts from get_activity_exercise_sets.
    Returns (weight_kg, reps) for the heaviest matching set, or None."""
    best = None
    for s in sets:
        category = s.get("category")
        name = s.get("name")
        if name_filter is not None:
            if not name_filter(category, name):
                continue
        elif category != wanted_category:
            continue
        weight_g = s.get("weight")
        reps = s.get("repetitionCount") or s.get("reps")
        if weight_g is None or not reps:
            continue
        weight_kg = round(weight_g / 1000.0)
        if best is None or weight_kg > best[0]:
            best = (weight_kg, reps)
    return best


def fetch_day(garmin, d, data):
    date_str = d.isoformat()
    month = month_of(d)

    # ---- steps ----
    try:
        steps_result = garmin.get_steps_data(date_str)
        total_steps = sum(x.get("steps") or 0 for x in steps_result) if isinstance(steps_result, list) else None
        if not total_steps:
            summary = garmin.get_user_summary(date_str)
            total_steps = summary.get("totalSteps")
        if total_steps:
            bump_monthly(data["steps_monthly"], month, total_steps)
    except Exception as e:
        print(f"[warn] steps for {date_str} failed: {e}", file=sys.stderr)

    # ---- sleep ----
    try:
        sleep_result = garmin.get_sleep_data(date_str)
        seconds = (sleep_result.get("dailySleepDTO") or {}).get("sleepTimeSeconds")
        if seconds:
            bump_monthly(data["sleep_monthly"], month, round(seconds / 3600.0, 3))
    except Exception as e:
        print(f"[warn] sleep for {date_str} failed: {e}", file=sys.stderr)

    # ---- activities ----
    try:
        activities = garmin.get_activities_by_date(date_str, date_str)
    except Exception as e:
        print(f"[warn] activities for {date_str} failed: {e}", file=sys.stderr)
        activities = []

    tmonth = None
    for tm in data["training_monthly"]:
        if tm["month"] == month:
            tmonth = tm
            break
    if tmonth is None:
        tmonth = {"month": month, "strength": 0, "walk_run": 0, "bike": 0, "other": 0}
        data["training_monthly"].append(tmonth)

    bench_dates = existing_dates(data["bench"])
    lat_dates = existing_dates(data["lat"])
    legpress_dates = existing_dates(data["legpress"])
    hacksquat_dates = existing_dates(data["hacksquat"])

    for act in activities:
        type_key = (act.get("activityType") or {}).get("typeKey")
        category = classify_activity_type(type_key)
        tmonth[category] += 1

        if type_key != "strength_training":
            continue

        activity_id = act.get("activityId")
        try:
            sets = garmin.get_activity_exercise_sets(activity_id).get("exerciseSets", [])
        except Exception as e:
            print(f"[warn] exercise sets for activity {activity_id} failed: {e}", file=sys.stderr)
            continue

        if date_str not in bench_dates:
            best = best_set_for_category(sets, "BENCH_PRESS", name_filter=is_bench_press)
            if best:
                data["bench"].append({"d": date_str, "v": best[0], "reps": str(best[1])})
                bench_dates.add(date_str)
            else:
                # record any bench-family set we skipped, for visibility
                for s in sets:
                    if s.get("category") == "BENCH_PRESS" and not is_bench_press(s.get("category"), s.get("name")):
                        w = s.get("weight")
                        r = s.get("repetitionCount") or s.get("reps")
                        if w and r:
                            data.setdefault("bench_excluded_variants", []).append({
                                "d": date_str, "v": round(w / 1000.0), "reps": str(r),
                                "exercise": s.get("name") or "BENCH_PRESS (variant)",
                                "note": "не штанга — автоматично виключено скриптом"
                            })
                        break

        if date_str not in lat_dates:
            best = best_set_for_category(sets, "LAT_PULLDOWN")
            if best:
                data["lat"].append({"d": date_str, "v": best[0], "reps": str(best[1])})
                lat_dates.add(date_str)

        if date_str not in legpress_dates:
            best = best_set_for_category(sets, "LEG_PRESS")
            if best:
                data["legpress"].append({"d": date_str, "v": best[0], "reps": str(best[1])})
                legpress_dates.add(date_str)

        if date_str not in hacksquat_dates:
            best = best_set_for_category(sets, "HACK_SQUAT")
            if best:
                data["hacksquat"].append({"d": date_str, "v": best[0], "reps": str(best[1])})
                hacksquat_dates.add(date_str)


def main():
    try:
        from garminconnect import Garmin
    except ImportError:
        print("garminconnect is not installed — pip install -r requirements.txt", file=sys.stderr)
        sys.exit(1)

    token_dir = os.environ.get("GARMINTOKENS", os.path.expanduser("~/.garminconnect"))
    if not os.path.isdir(token_dir):
        print(f"No saved session at {token_dir}. Run generate_garmin_session.py once locally first.", file=sys.stderr)
        sys.exit(1)

    garmin = Garmin()
    garmin.login(token_dir)

    data = load_data()
    last_processed = data["meta"].get("last_processed_date")
    if last_processed:
        start = date.fromisoformat(last_processed) + timedelta(days=1)
    else:
        start = date.today() - timedelta(days=1)

    yesterday = date.today() - timedelta(days=1)
    if start > yesterday:
        print("Nothing to do — already up to date.")
        return

    if (yesterday - start).days + 1 > MAX_BACKFILL_DAYS:
        start = yesterday - timedelta(days=MAX_BACKFILL_DAYS - 1)
        print(f"[info] capping backfill to last {MAX_BACKFILL_DAYS} days")

    last_ok = last_processed
    for d in daterange(start, yesterday):
        print(f"Fetching {d.isoformat()}...")
        fetch_day(garmin, d, data)
        last_ok = d.isoformat()

    data["meta"]["last_processed_date"] = last_ok
    data["meta"]["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    save_data(data)
    print(f"Updated through {last_ok}.")


if __name__ == "__main__":
    main()
