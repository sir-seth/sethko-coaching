"""
Pydantic models — request bodies and response shapes.

These are the contracts between the iOS app and the backend, and between
the backend and the DB layer. Keeping them here means main.py and db.py
both import from one place and can't drift apart.
"""

from datetime import date, datetime
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


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

Goal = Literal["cut", "recomp", "bulk", "maintain", "performance"]

RecoverySource = Literal["whoop", "oura", "both"]


class User(BaseModel):
    id: str
    name: str
    email: Optional[str] = None
    dietary_modality: DietaryModality
    goal: Goal
    recovery_source: RecoverySource = "whoop"
    # Optional manual macro overrides. None means Claude sets targets dynamically.
    macro_targets: Optional[dict[str, Any]] = None
    created_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# HealthKit snapshot (iOS → backend)
# ---------------------------------------------------------------------------

class WorkoutEntry(BaseModel):
    """One workout session from HealthKit."""
    started_at: datetime
    ended_at: datetime
    activity_type: str          # e.g. "HKWorkoutActivityTypeTraditionalStrengthTraining"
    activity_label: str         # human-readable, e.g. "Strength Training"
    duration_min: float
    avg_hr_bpm: Optional[float] = None
    calories: Optional[float] = None
    source_app: Optional[str] = None  # e.g. "com.fitnessapp.macrofactor"


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
