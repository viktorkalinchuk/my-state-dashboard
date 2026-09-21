#!/usr/bin/env python3
"""
ONE-TIME, LOCAL-ONLY backfill script.

best_set_for_category() (used to feed the bench/lat/legpress/hacksquat
progress charts) had the same "reads fields that don't exist on the set
dict" bug as the workout log did — Garmin nests the exercise id under
s['exercises'][0], not on the set itself — so it silently matched nothing.
fetch_garmin.py is now fixed, but because each day is only ever visited
once (data["meta"]["last_processed_date"] moves forward and never revisits
a date), the daily run can't retroactively fill in points it missed.

This script walks every strength_training day already in
data/workout-log.json (that data source is unaffected by the chart bug,
so it's a reliable list of which days to check) and, for any of the four
charts missing that date, re-fetches that day's exercise sets and applies
the now-fixed matching logic — same as fix_workout_log_names.py, but for
data/dashboard-data.json's bench/lat/legpress/hacksquat arrays instead of
the workout log.

Reuses your existing local Garmin session at ~/.garminconnect — no fresh
login needed unless it's expired (run generate_garmin_session.py if so).

Usage:
    cd ~/my-state-dashboard
    python3 scripts/backfill_charts.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_garmin import (
    WORKOUT_LOG_PATH,
    best_set_for_category,
    exercise_info,
    existing_dates,
    is_bench_press,
    load_data,
    load_json_list,
    save_data,
)


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
    workout_log = load_json_list(WORKOUT_LOG_PATH)

    bench_dates = existing_dates(data["bench"])
    lat_dates = existing_dates(data["lat"])
    legpress_dates = existing_dates(data["legpress"])
    hacksquat_dates = existing_dates(data["hacksquat"])

    changed = False
    checked = 0

    for entry in sorted(workout_log, key=lambda e: e.get("date") or ""):
        date_str = entry.get("date")
        activity_id = entry.get("activityId")
        if not date_str or not activity_id:
            continue

        need_bench = date_str not in bench_dates
        need_lat = date_str not in lat_dates
        need_legpress = date_str not in legpress_dates
        need_hacksquat = date_str not in hacksquat_dates
        if not (need_bench or need_lat or need_legpress or need_hacksquat):
            continue

        checked += 1
        try:
            sets = garmin.get_activity_exercise_sets(activity_id).get("exerciseSets", [])
        except Exception as ex:
            print(f"[warn] failed to fetch sets for {date_str} (activity {activity_id}): {ex}", file=sys.stderr)
            continue

        if need_bench:
            best = best_set_for_category(sets, "BENCH_PRESS", name_filter=is_bench_press)
            if best:
                data["bench"].append({"d": date_str, "v": best[0], "reps": str(best[1])})
                bench_dates.add(date_str)
                changed = True
                print(f"  {date_str}: bench {best[0]}kg x {best[1]}")

        if need_lat:
            best = best_set_for_category(sets, "LAT_PULLDOWN")
            if best:
                data["lat"].append({"d": date_str, "v": best[0], "reps": str(best[1])})
                lat_dates.add(date_str)
                changed = True
                print(f"  {date_str}: lat {best[0]}kg x {best[1]}")

        if need_legpress:
            best = best_set_for_category(sets, "LEG_PRESS")
            if best:
                data["legpress"].append({"d": date_str, "v": best[0], "reps": str(best[1])})
                legpress_dates.add(date_str)
                changed = True
                print(f"  {date_str}: legpress {best[0]}kg x {best[1]}")

        if need_hacksquat:
            best = best_set_for_category(sets, "HACK_SQUAT")
            if best:
                data["hacksquat"].append({"d": date_str, "v": best[0], "reps": str(best[1])})
                hacksquat_dates.add(date_str)
                changed = True
                print(f"  {date_str}: hacksquat {best[0]}kg x {best[1]}")

    print(f"\nChecked {checked} workout day(s) with a gap in at least one chart.")

    if not changed:
        print("No new chart points found (nothing to backfill).")
        return

    for key in ("bench", "lat", "legpress", "hacksquat"):
        data[key].sort(key=lambda p: p["d"])
    save_data(data)
    print("\nSaved data/dashboard-data.json. Review with `git diff data/dashboard-data.json`, then commit and push:")
    print("  git add data/dashboard-data.json")
    print('  git commit -m "fix: backfill bench/lat/legpress/hacksquat points missed by the category-matching bug"')
    print("  git push")


if __name__ == "__main__":
    main()
