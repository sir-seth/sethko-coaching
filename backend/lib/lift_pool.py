"""Curated lift exercise pool for v1 plan generation."""

# Each entry: name, tag (display), sets, reps, weight_lb (None = bodyweight/superset)
_POOL = [
    # BACK
    {"name": "Bent-over row",               "tag": "BACK",      "sets": 4, "reps": 8,  "weight_lb": 135},
    {"name": "Lat pulldown",                "tag": "BACK",      "sets": 3, "reps": 10, "weight_lb": 120},
    {"name": "Seated cable row",            "tag": "BACK",      "sets": 3, "reps": 10, "weight_lb": 110},
    {"name": "Pull-up",                     "tag": "BACK",      "sets": 3, "reps": 8,  "weight_lb": None},
    # CHEST
    {"name": "Bench press",                 "tag": "CHEST",     "sets": 4, "reps": 8,  "weight_lb": 155},
    {"name": "Incline bench press",         "tag": "CHEST",     "sets": 4, "reps": 8,  "weight_lb": 135},
    {"name": "Cable fly",                   "tag": "CHEST",     "sets": 3, "reps": 12, "weight_lb": 40},
    {"name": "Chest dip",                   "tag": "CHEST",     "sets": 3, "reps": 10, "weight_lb": None},
    # ARMS
    {"name": "Bicep curl",                  "tag": "ARMS",      "sets": 3, "reps": 10, "weight_lb": 35},
    {"name": "Tricep pushdown",             "tag": "ARMS",      "sets": 3, "reps": 12, "weight_lb": 50},
    {"name": "Hammer curl",                 "tag": "ARMS",      "sets": 3, "reps": 10, "weight_lb": 35},
    {"name": "Cable curl + tricep press",   "tag": "ARMS",      "sets": 3, "reps": 12, "weight_lb": None},
    # LEGS
    {"name": "Squat",                       "tag": "LEGS",      "sets": 4, "reps": 8,  "weight_lb": 185},
    {"name": "Romanian deadlift",           "tag": "LEGS",      "sets": 3, "reps": 10, "weight_lb": 155},
    {"name": "Leg press",                   "tag": "LEGS",      "sets": 3, "reps": 12, "weight_lb": 200},
    {"name": "Walking lunge",               "tag": "LEGS",      "sets": 3, "reps": 12, "weight_lb": 45},
    # SHOULDERS
    {"name": "Overhead press",              "tag": "SHOULDERS", "sets": 4, "reps": 8,  "weight_lb": 95},
    {"name": "Lateral raise",              "tag": "SHOULDERS", "sets": 3, "reps": 12, "weight_lb": 15},
    {"name": "Face pull",                   "tag": "SHOULDERS", "sets": 3, "reps": 15, "weight_lb": 30},
]

# 4-day split rotation: list of (tag, exercise_count) pairs per day
_SPLITS = [
    [("BACK", 2), ("CHEST", 1), ("ARMS", 1)],        # Pull + chest + arms
    [("LEGS", 3), ("SHOULDERS", 1)],                  # Leg day
    [("CHEST", 2), ("SHOULDERS", 1), ("ARMS", 1)],    # Push day
    [("BACK", 2), ("ARMS", 2)],                       # Pull day
]


def _format(e: dict) -> dict:
    sets, reps, w = e["sets"], e["reps"], e.get("weight_lb")
    if w:
        detail = f"{sets} × {reps} · {int(w)} lb"
    elif "superset" in e["name"].lower() or "+" in e["name"]:
        detail = f"{sets} × {reps} · superset"
    else:
        detail = f"{sets} × {reps} · bodyweight"
    return {
        "name": e["name"],
        "sets": sets,
        "reps": reps,
        "weight_lb": w,
        "detail": detail,
        "muscle_tag": e["tag"],
    }


def get_lift_plan(day_index: int, gentle: bool = False) -> dict:
    """
    Return a lift plan for the given day index (rotates through the 4-day split).

    gentle=True caps RPE at 6, reduces sets to 3, and overrides the headline.
    """
    split = _SPLITS[day_index % len(_SPLITS)]
    exercises = []
    tags_seen: list[str] = []

    for tag, count in split:
        pool = [e for e in _POOL if e["tag"] == tag]
        for e in pool[:count]:
            ex = dict(e)
            if gentle:
                ex["sets"] = min(ex["sets"], 3)
            exercises.append(ex)
        if tag not in tags_seen:
            tags_seen.append(tag)

    # Headline construction
    if gentle:
        # headline empty so the whole phrase renders italic in the view
        headline = ""
        italic_fragment = "A few quiet lifts."
    else:
        names = [t.capitalize() for t in tags_seen]
        if len(names) == 1:
            headline = names[0]
            italic_fragment = "day."
        elif len(names) == 2:
            headline = names[0]
            italic_fragment = f"& {names[1].lower()}."
        else:
            headline = ", ".join(n.lower() for n in names[:-1]).capitalize()
            italic_fragment = f"& {names[-1].lower()}."

    return {
        "headline": headline,
        "italic_fragment": italic_fragment,
        "duration_min": 35 if gentle else 45,
        "rpe_min": 5 if gentle else 7,
        "rpe_max": 6 if gentle else 8,
        "exercises": [_format(e) for e in exercises],
    }
