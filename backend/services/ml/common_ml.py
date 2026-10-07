"""Constants and wording for the repair-pressure context (kept apart so tests and the UI contract can share them)."""
BOROUGHS = ["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"]

HIGH_PCT, MODERATE_PCT = 67.0, 33.0

CATEGORY_LABELS = {
    "High": "Repair pressure is high relative to the borough's 2024-2025 history.",
    "Moderate": "Repair pressure is in the middle of the borough's 2024-2025 history.",
    "Low": "Repair pressure is low relative to the borough's 2024-2025 history.",
}

EXPLANATION = (
    "Repair pressure describes how slowly 311 street-light complaints have recently been resolved in a borough, "
    "compared with that borough's own 2024-2025 history. A frozen model trained on NYC 311 repair behaviour turns "
    "recent complaint and closure patterns into a score; the score is ranked against past 28-day windows. "
    "High does not mean high crime, a dangerous borough or a predicted crime. It is context for dispatchers and does "
    "not change the priority score or the dispatch plan."
)

LIMITATIONS = [
    "Operational context only: it says nothing about crime and does not rank individual outages.",
    "Most of the model's skill comes from the borough's recent repair pace, not from features of a single complaint.",
    "Repair pace has drifted upward since 2024, so a borough can rank above its entire 2024-2025 range.",
    "Validated on NYC data and on a simulated dataset only; the simulation does not show real-world generalisation.",
]


def categorize(percentile: float) -> str:
    if percentile >= HIGH_PCT:
        return "High"
    if percentile >= MODERATE_PCT:
        return "Moderate"
    return "Low"
