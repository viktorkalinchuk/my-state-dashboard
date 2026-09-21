#!/usr/bin/env python3
"""
ONE-TIME, LOCAL-ONLY repair script.

Some entries in data/workout-log.json got saved with every set merged into
a single "Вправа не розпізнана" exercise, because build_workout_log_entry()
in fetch_garmin.py only looked at Garmin's 'name' field on each exercise
set — and Garmin leaves that empty for exercises picked from its standard
list (it reports 'category'/'subCategory' instead). fetch_garmin.py has
been fixed to fall back to category/subCategory, but that fix only applies
going forward to NEW activities — it can't retroactively fix entries
already written to workout-log.json (they're keyed by activityId and never
rebuilt once saved). This script re-fetches the exercise sets for the
already-broken entries specifically and rewrites them in place using the
same corrected logic.

It reuses your existing local Garmin session at ~/.garminconnect (the same
one generate_garmin_session.py created) — you do not need to log in again
unless that session has expired, in which case run
generate_garmin_session.py first.

Usage:
    cd ~/my-state-dashboard
    python3 scripts/fix_workout_log_names.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_garmin import (
    WORKOUT_LOG_PATH,
    build_workout_log_entry,
    load_json_list,
    save_json_list,
)


def entry_is_broken(entry):
    exercises = entry.get("exercises") or []
    return len(exercises) == 1 and exercises[0].get("name") == "Вправа не розпізнана"


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

    workout_log = load_json_list(WORKOUT_LOG_PATH)
    broken = [e for e in workout_log if entry_is_broken(e)]

    if not broken:
        print("No broken entries found (nothing to fix).")
        return

    print(f"Found {len(broken)} entr{'y' if len(broken) == 1 else 'ies'} to re-fetch and fix:")
    for e in broken:
        print(f"  {e.get('date')} (activityId={e.get('activityId')})")

    by_id = {e.get("activityId"): e for e in workout_log}
    for e in broken:
        activity_id = e.get("activityId")
        try:
            sets = garmin.get_activity_exercise_sets(activity_id).get("exerciseSets", [])
        except Exception as ex:
            print(f"[warn] failed to re-fetch sets for activity {activity_id}: {ex}", file=sys.stderr)
            continue
        fixed = build_workout_log_entry(e, sets)
        if fixed["exercises"]:
            by_id[activity_id].clear()
            by_id[activity_id].update(fixed)
            print(f"  fixed {e.get('date')}: {[x['name'] for x in fixed['exercises']]}")
        else:
            print(f"  [warn] still no exercises after re-fetch for {e.get('date')} — leaving as-is")

    workout_log.sort(key=lambda x: x.get("date") or "")
    save_json_list(WORKOUT_LOG_PATH, workout_log)
    print("\nSaved data/workout-log.json. Review with `git diff data/workout-log.json`, then commit and push:")
    print("  git add data/workout-log.json")
    print('  git commit -m "fix: re-resolve exercise names for entries Garmin returned without a name field"')
    print("  git push")


if __name__ == "__main__":
    main()
