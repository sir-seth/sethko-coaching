"""
Pydantic models — request bodies and response shapes.

These are the contracts between the iOS app and the backend, and between
the backend and the DB layer. Keeping them here means main.py and db.py
both import from one place and can't drift apart.
"""

from datetime import date, datetime
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

# Stable mood → numeric mapping. Pinned here so T-30 pattern engine imports it.
MOOD_NUMERIC: dict[str, int] = {
    "rough": 2,
    "flat": 4,
    "good": 7,
    "great": 9,
}


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

DietaryModality = Literal[
    "high_protein_performance",
    "maintenance_active",
    "carb_cycling",
    "low_carb_keto",
    "glp1_supported",
    "flexible",
]

Goal = Literal["cut", "recomp", "bulk", "maintain", "performance", "just_feel_better"]

RecoverySource = Literal["whoop", "oura", "both"]

CoachingMode = Literal["gentle", "optimizer"]


class User(BaseModel):
    id: str
    name: str
    email: Optional[str] = None
    dietary_modality: DietaryModality
    goal: Goal
    mode: Optional[CoachingMode] = None   # None = not yet chosen (T-24 first-run)
    recovery_source: RecoverySource = "whoop"
    device: Optional[str] = None          # active wearable: "whoop" | "oura" | None (T-25)
    # Optional manual macro overrides. None means Claude sets targets dynamically.
    macro_targets: Optional[dict[str, Any]] = None
    onboarding_completed_at: Optional[datetime] = None   # None = onboarding not finished (T-41)
    habits: Optional[list[str]] = None                   # habit IDs picked in onboarding (T-41)
    created_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# HealthKit snapshot (iOS → backend)
# ---------------------------------------------------------------------------

class WorkoutEntry(BaseModel):
    """One workout session from HealthKit."""
    started_at: datetime
    ended_at: datetime
    activity_type: str          # modality enum: strength/cardio/mobility/recreational/other
    activity_label: str         # human-readable, e.g. "Strength Training"
    modality: Optional[str] = None   # same as activity_type; explicit field for clarity
    duration_min: float
    avg_hr_bpm: Optional[float] = None
    calories: Optional[float] = None
    source_app: Optional[str] = None


class HealthSnapshot(BaseModel):
    """
    Posted by the iOS app after each HealthKit read.
    Covers the current day's measurements.
    """
    date: date
    weight_lbs: Optional[float] = None
    # Workouts that ended on or after midnight of `date` (local time).
    workouts: list[WorkoutEntry] = Field(default_factory=list)
    # Apple Health HRV if available (supplements Whoop; used as fallback).
    hrv_ms: Optional[float] = None
    steps: Optional[int] = None


# ---------------------------------------------------------------------------
# Recovery signal (normalised from Whoop or Oura)
# ---------------------------------------------------------------------------

class RecoverySignal(BaseModel):
    date: date
    source: RecoverySource
    hrv_ms: Optional[float] = None
    rhr_bpm: Optional[float] = None
    sleep_hours: Optional[float] = None
    sleep_score: Optional[float] = None
    strain_score: Optional[float] = None
    recovery_score: Optional[float] = None


# ---------------------------------------------------------------------------
# Weight trend point (used internally by build_digest)
# ---------------------------------------------------------------------------

class WeightPoint(BaseModel):
    date: date
    weight_lbs: float


# ---------------------------------------------------------------------------
# Coaching response (backend → iOS)
# ---------------------------------------------------------------------------

class RecoveryRead(BaseModel):
    headline: str
    detail: str


class NutritionTarget(BaseModel):
    kcal: int
    protein_g: int
    carbs_g: int
    fat_g: int
    rationale: str


class WeightTrend(BaseModel):
    headline: str
    detail: str


class WorkoutSuggestion(BaseModel):
    type: str
    strain_target: str
    detail: str


class CoachingResponse(BaseModel):
    recovery_read: RecoveryRead
    nutrition_target_today: NutritionTarget
    weight_trend: WeightTrend
    workout_suggestion: WorkoutSuggestion
    daily_brief: str
    generated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Daily brief — new schema (T-23)
# ---------------------------------------------------------------------------

class CoachingCard(BaseModel):
    eyebrow: str
    headline: str
    body: str
    action_label: str


class BriefTodayPayload(BaseModel):
    coaching_card: Optional[CoachingCard] = None
    metric_stamps: Optional[Any] = None   # Always None until T-35


# ---------------------------------------------------------------------------
# Profile / mode (T-24)
# ---------------------------------------------------------------------------

class ModeUpdateRequest(BaseModel):
    mode: CoachingMode


class ProfileUpdateRequest(BaseModel):
    """Flexible partial update used by onboarding and profile settings (T-41)."""
    mode: Optional[CoachingMode] = None
    goal: Optional[Goal] = None
    habits: Optional[list[str]] = None
    onboarding_completed_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Subjective check-in (T-17)
# ---------------------------------------------------------------------------

MoodLabel = Literal["rough", "flat", "good", "great"]


class CheckInRequest(BaseModel):
    date: date
    mood: MoodLabel
    energy: int   # 1-10
    motivation: int   # 1-10
    clarity: int   # 1-10
    note: Optional[str] = None
    source: Optional[str] = None   # "watch" | "phone" (None → defaults to "phone" in db)


class SubjectiveLog(BaseModel):
    date: date
    mood_label: str
    mood_numeric: int
    energy: int
    motivation: int
    clarity: int
    note: Optional[str] = None
    created_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Win log (T-18)
# ---------------------------------------------------------------------------

class WinRequest(BaseModel):
    date: date
    text: str
    meta: Optional[str] = None
    category: str = "other"


class WinEntry(BaseModel):
    id: int
    date: date
    text: str
    meta: Optional[str] = None
    category: str
    created_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Log index (T-20)
# ---------------------------------------------------------------------------

class PendingWorkout(BaseModel):
    id: int
    source: str                      # "whoop" | "apple_health"
    detail: str                      # "Strength · 47 min"
    started_at: datetime
    duration_min: Optional[float] = None
    strain_score: Optional[float] = None


class LogEntry(BaseModel):
    id: str
    kind: str                        # "food" | "workout" | "checkin"
    icon: str                        # SF symbol name
    text: str
    meta: str
    logged_at: datetime


# ---------------------------------------------------------------------------
# Food log (T-21)
# ---------------------------------------------------------------------------

class FoodEntryRequest(BaseModel):
    description: str
    weight_g: Optional[float] = None
    kcal: Optional[int] = None
    protein_g: Optional[float] = None
    fat_g: Optional[float] = None
    carbs_g: Optional[float] = None
    meal_label: Optional[str] = None   # "breakfast" | "lunch" | "snack" | "dinner"
    source: str = "voice"              # "voice" | "manual"


class FoodEntry(BaseModel):
    id: int
    description: str
    weight_g: Optional[float] = None
    kcal: Optional[int] = None
    protein_g: Optional[float] = None
    fat_g: Optional[float] = None
    carbs_g: Optional[float] = None
    meal_label: Optional[str] = None
    logged_at: datetime


class ParsedFoodItem(BaseModel):
    description: str
    weight_g: Optional[float] = None
    kcal: Optional[int] = None
    protein_g: Optional[float] = None
    fat_g: Optional[float] = None
    carbs_g: Optional[float] = None


class DayMacros(BaseModel):
    date: str                          # "YYYY-MM-DD"
    kcal: int
    protein_g: float
    fat_g: float
    carbs_g: float
    entry_count: int


class WeekDayStatus(BaseModel):
    date: str                          # "YYYY-MM-DD"
    status: str                        # "logged" | "partial" | "today" | "skipped" | "future"
    kcal: int


# ---------------------------------------------------------------------------
# Vice log (T-22)
# ---------------------------------------------------------------------------

class ViceCategory(BaseModel):
    id: int
    label: str
    sort_order: int = 0


class ViceLogEntry(BaseModel):
    id: int
    logged_at: datetime
    category_id: Optional[int] = None
    # category_label is only shown in-app; never in coaching or analytics
    category_label: Optional[str] = None


class ViceLogResponse(BaseModel):
    id: int
    logged_at: datetime


class ViceCategoryRequest(BaseModel):
    label: str


# ---------------------------------------------------------------------------
# Sign in with Apple (T-26)
# ---------------------------------------------------------------------------

class SIWARequest(BaseModel):
    identity_token: str       # JWT from ASAuthorizationAppleIDCredential.identityToken
    authorization_code: str   # from credential.authorizationCode
    user_id_apple: str        # credential.user — stable Apple sub
    raw_nonce: str            # the un-hashed nonce iOS generated before the auth request
    email: Optional[str] = None      # only present on first authorization
    full_name: Optional[str] = None  # only present on first authorization


class SIWAResponse(BaseModel):
    session_token: str
    user_id: str
    mode: Optional[CoachingMode] = None


# ---------------------------------------------------------------------------
# Plan index (T-27)
# ---------------------------------------------------------------------------

class PlanPick(BaseModel):
    category: str                    # "lift"|"walk"|"meditate"|"run" etc.
    headline: str
    body: str
    meta: list[str] = []             # ["45 MIN", "RPE 7–8", "~2,300 KCAL"]
    outlook_value: Optional[int] = None
    is_new_activity: bool = False    # True when pick isn't in user's habits (T-41)
    is_learning: bool = False        # True when outlook baseline < 14 days


class DocketItem(BaseModel):
    id: int
    category: str
    name: str
    meta: str
    created_at: datetime


class CommitPlanRequest(BaseModel):
    category: str
    name: str


# ---------------------------------------------------------------------------
# Workout logger (T-29)
# ---------------------------------------------------------------------------

class WorkoutSetPayload(BaseModel):
    reps: int
    weight_lb: Optional[float] = None
    rpe: Optional[int] = None          # 1–10; null = not rated


class WorkoutExercisePayload(BaseModel):
    name: str
    skipped: bool = False
    sets: list[WorkoutSetPayload] = []


class WorkoutLogRequest(BaseModel):
    exercises: list[WorkoutExercisePayload]
    started_at: datetime
    ended_at: datetime
    notes: Optional[str] = None


class WorkoutLogResponse(BaseModel):
    workout_id: int
    win_id: Optional[int] = None
    set_count: int
    exercise_count: int
