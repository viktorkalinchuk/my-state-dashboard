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
  - for strength_training activities, the FULL exercise/set breakdown
    (every exercise, every set — weight + reps), appended to
    data/workout-log.json. This is the same data the "Журнал тренувань"
    section on the dashboard reads.
  - a best-effort daily recovery snapshot (steps, sleep hours, resting HR,
    HRV, Body Battery, stress, training readiness — whichever of these the
    account/device actually has data for) appended to data/recovery.json.
    Recovery is intentionally never displayed on the dashboard yet; it's
    collected so a future "generate my next session" request has it on
    hand without needing a fresh Garmin pull.

It only ever *adds* to data/dashboard-data.json, data/workout-log.json and
data/recovery.json — existing points are never edited or removed by this
script (workout-log entries are keyed by activityId, recovery entries by
date, so reruns don't duplicate).

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
WORKOUT_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "workout-log.json")
RECOVERY_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "recovery.json")
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


def load_json_list(path):
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        return json.loads(content) if content else []


def save_json_list(path, items):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
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


def build_workout_log_entry(activity, sets):
    """Turns one strength_training activity + its exercise sets into the
    {date, activityId, activityName, exercises:[{name, sets:[{weight,reps}]}]}
    shape used by data/workout-log.json and rendered by index.html's
    "Журнал тренувань" section."""
    exercises = []
    by_name = {}
    for s in sets:
        name = s.get("name") or "Вправа не розпізнана"
        weight_g = s.get("weight")
        reps = s.get("repetitionCount") or s.get("reps")
        if weight_g is None or not reps:
            continue
        weight_kg = round(weight_g / 1000.0, 1)
        if name not in by_name:
            entry = {"name": name, "sets": []}
            by_name[name] = entry
            exercises.append(entry)
        by_name[name]["sets"].append({"weight": weight_kg, "reps": reps})

    return {
        "date": activity.get("startTimeLocal", "")[:10] or activity.get("date"),
        "activityId": activity.get("activityId"),
        "activityName": activity.get("activityName") or "Тренування",
        "exercises": exercises,
    }


def fetch_recovery_day(garmin, date_str):
    """Best-effort daily recovery snapshot. Every metric is fetched
    independently and just omitted if the call fails or the account/device
    doesn't have that data (some of these — training readiness, HRV — are
    known to come back empty for some accounts; see README "Known data
    gaps"). Never raises."""
    snapshot = {"d": date_str}

    try:
        steps_result = garmin.get_steps_data(date_str)
        total_steps = sum(x.get("steps") or 0 for x in steps_result) if isinstance(steps_result, list) else None
        if total_steps:
            snapshot["steps"] = total_steps
    except Exception as e:
        print(f"[warn] recovery steps for {date_str} failed: {e}", file=sys.stderr)

    try:
        sleep_result = garmin.get_sleep_data(date_str)
        seconds = (sleep_result.get("dailySleepDTO") or {}).get("sleepTimeSeconds")
        if seconds:
            snapshot["sleep_hours"] = round(seconds / 3600.0, 2)
        sleep_score = ((sleep_result.get("dailySleepDTO") or {}).get("sleepScores") or {}).get("overall", {}).get("value")
        if sleep_score:
            snapshot["sleep_score"] = sleep_score
    except Exception as e:
        print(f"[warn] recovery sleep for {date_str} failed: {e}", file=sys.stderr)

    try:
        rhr = garmin.get_rhr_day(date_str)
        rhr_value = ((rhr.get("allMetrics") or {}).get("metricsMap") or {}).get("WELLNESS_RESTING_HEART_RATE")
        if isinstance(rhr_value, list) and rhr_value:
            snapshot["resting_hr"] = rhr_value[0].get("value")
    except Exception as e:
        print(f"[warn] recovery resting HR for {date_str} failed: {e}", file=sys.stderr)

    try:
        bb = garmin.get_body_battery(date_str, date_str)
        if isinstance(bb, list) and bb:
            values = [p[1] for p in (bb[0].get("bodyBatteryValuesArray") or []) if p and p[1] is not None]
            if values:
                snapshot["body_battery_high"] = max(values)
                snapshot["body_battery_low"] = min(values)
    except Exception as e:
        print(f"[warn] recovery body battery for {date_str} failed: {e}", file=sys.stderr)

    try:
        stress = garmin.get_stress_data(date_str)
        avg_stress = stress.get("avgStressLevel")
        if avg_stress is not None and avg_stress >= 0:
            snapshot["stress_avg"] = avg_stress
    except Exception as e:
        print(f"[warn] recovery stress for {date_str} failed: {e}", file=sys.stderr)

    try:
        hrv = garmin.get_hrv_data(date_str)
        hrv_summary = (hrv or {}).get("hrvSummary") or {}
        if hrv_summary.get("lastNightAvg"):
            snapshot["hrv_ms"] = hrv_summary["lastNightAvg"]
        if hrv_summary.get("status"):
            snapshot["hrv_status"] = hrv_summary["status"]
    except Exception as e:
        print(f"[warn] recovery HRV for {date_str} failed: {e}", file=sys.stderr)

    try:
        readiness = garmin.get_training_readiness(date_str)
        if isinstance(readiness, list) and readiness:
            score = readiness[0].get("score")
            if score is not None:
                snapshot["training_readiness"] = score
    except Exception as e:
        print(f"[warn] recovery training readiness for {date_str} failed: {e}", file=sys.stderr)

    return snapshot


def fetch_day(garmin, d, data, workout_log, known_activity_ids):
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

        if activity_id not in known_activity_ids:
            entry = build_workout_log_entry(act, sets)
            if entry["exercises"]:
                workout_log.append(entry)
                known_activity_ids.add(activity_id)

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
    workout_log = load_json_list(WORKOUT_LOG_PATH)
    recovery = load_json_list(RECOVERY_PATH)
    known_activity_ids = {e.get("activityId") for e in workout_log}
    known_recovery_dates = {e.get("d") for e in recovery}

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
        fetch_day(garmin, d, data, workout_log, known_activity_ids)
        date_str = d.isoformat()
        if date_str not in known_recovery_dates:
            recovery.append(fetch_recovery_day(garmin, date_str))
            known_recovery_dates.add(date_str)
        last_ok = date_str

    data["meta"]["last_processed_date"] = last_ok
    data["meta"]["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    save_data(data)
    workout_log.sort(key=lambda e: e.get("date") or "")
    save_json_list(WORKOUT_LOG_PATH, workout_log)
    recovery.sort(key=lambda e: e.get("d") or "")
    save_json_list(RECOVERY_PATH, recovery)
    print(f"Updated through {last_ok}.")


if __name__ == "__main__":
    main()
