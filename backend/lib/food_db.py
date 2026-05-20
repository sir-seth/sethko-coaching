"""
USDA FoodData Central lookup with local Postgres cache.

Hits the USDA API at most once per food name per 24 hours (cached in recent_foods).
Falls back to None if USDA returns no match or the API key is exhausted.
"""

import logging
import os
from typing import Optional

import httpx

log = logging.getLogger(__name__)

USDA_BASE = "https://api.nal.usda.gov/fdc/v1"
_API_KEY = os.environ.get("USDA_API_KEY", "DEMO_KEY")

# Nutrient IDs in FoodData Central Foundation Foods
_KCAL_ID    = 1008
_PROTEIN_ID = 1003
_FAT_ID     = 1004
_CARBS_ID   = 1005


async def search_usda(query: str, max_results: int = 1) -> Optional[dict]:
    """
    Search USDA FoodData Central for `query`. Returns the first result's
    per-100g macros, or None if nothing found.
    """
    url = f"{USDA_BASE}/foods/search"
    params = {
        "api_key": _API_KEY,
        "query": query,
        "dataType": "Foundation,SR Legacy",
        "pageSize": max_results,
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning("USDA search failed for %r: %s", query, exc)
        return None

    foods = data.get("foods", [])
    if not foods:
        return None

    food = foods[0]
    nutrients = {n["nutrientId"]: n.get("value", 0) for n in food.get("foodNutrients", [])}
    return {
        "fdc_id": food.get("fdcId"),
        "description": food.get("description", query),
        "kcal_per_100g":    nutrients.get(_KCAL_ID, 0),
        "protein_per_100g": nutrients.get(_PROTEIN_ID, 0),
        "fat_per_100g":     nutrients.get(_FAT_ID, 0),
        "carbs_per_100g":   nutrients.get(_CARBS_ID, 0),
    }


def scale_macros(usda: dict, weight_g: float) -> dict:
    """Scale per-100g values to actual weight_g."""
    factor = weight_g / 100.0
    return {
        "kcal":      round(usda["kcal_per_100g"]    * factor),
        "protein_g": round(usda["protein_per_100g"] * factor, 1),
        "fat_g":     round(usda["fat_per_100g"]     * factor, 1),
        "carbs_g":   round(usda["carbs_per_100g"]   * factor, 1),
    }
