"""GPS / location grounding for assets (PD 2026-11-25).

Root cause of the "첫 바닷가 수영" fabrication (cvufJY0xteQ, 2026-11-24): the pipeline
sourced a clip's LOCATION only from the VLM's pixel guess — which hallucinated "바닷가"
from water + white foam — while the authoritative GPS sat UNUSED in every source file.
Apple Photos ALREADY reverse-geocodes each capture offline (no network, no API key),
giving (a) the real administrative place and (b) a STRUCTURED water classification
(ocean / inland_water / body_of_water). This module extracts that ground truth so the
Writer can ground location on the map instead of a guess, and so an "ocean" claim on a
stream/inland clip can be caught — or, when genuinely ambiguous, asked of PD.

Signals, in order of authority:
  1. Apple reverse-geocode (`PhotoInfo.place`) — administrative name + water kind. Offline.
  2. GPS lat/lon (`PhotoInfo.location`) — the raw point.
  3. Altitude (video ISO6709 via ffprobe) — a cheap *physical* backstop: an 84 m / 202 m
     capture cannot be the ocean. Secondary; the map is primary (PD: "꼭 고도가 아니더라도
     맵이랑 확인").

Columns written (see `ensure_geo_cols`): gps_lat, gps_lon (existing) + gps_alt, geo_place,
geo_water (new). `geo_water` ∈ {'ocean', 'inland_water', 'none'} or NULL when no GPS.
NULL geo_water = UNKNOWN (no coordinates) → the ask-when-unknown path, not "not water".
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

# Apple PlaceNames fields that name a body of water near the capture point.
_OCEAN_FIELDS = ("ocean",)
_INLAND_WATER_FIELDS = ("inland_water", "body_of_water")


def _nonempty(names, field) -> bool:
    try:
        return bool(list(getattr(names, field, None) or []))
    except Exception:
        return False


def classify_water(names) -> str:
    """Map an osxphotos ``PlaceNames`` to 'ocean' | 'inland_water' | 'none'.

    Apple populates the ``ocean`` field only near a genuine coastline, so a non-empty
    ``ocean`` is strong evidence the clip IS coastal; empty ``ocean`` + any inland-water
    field → inland; nothing → 'none' (no NAMED water feature nearby — note this does NOT
    prove dry land, a small unnamed stream may still be present, which is why the caller
    asks PD rather than hard-denying all water)."""
    if names is None:
        return "none"
    if any(_nonempty(names, f) for f in _OCEAN_FIELDS):
        return "ocean"
    if any(_nonempty(names, f) for f in _INLAND_WATER_FIELDS):
        return "inland_water"
    return "none"


def geo_from_photoinfo(p) -> dict | None:
    """Extract {lat, lon, place, water} from an osxphotos PhotoInfo, or None if no GPS."""
    loc = getattr(p, "location", None)
    if not loc or loc[0] is None or loc[1] is None:
        return None
    lat, lon = float(loc[0]), float(loc[1])
    place = None
    water = "none"
    pl = getattr(p, "place", None)
    if pl is not None:
        place = getattr(pl, "address_str", None) or getattr(pl, "name", None)
        water = classify_water(getattr(pl, "names", None))
    return {"lat": lat, "lon": lon, "place": place, "water": water}


def altitude_from_file(path: str | Path) -> float | None:
    """Parse the capture altitude (metres) from a video's ISO6709 location tag via ffprobe.

    QuickTime stores GPS as e.g. '+36.6913+128.0179+201.998/' (lat, lon, altitude). We only
    want the altitude here (lat/lon come from Apple's richer reverse-geocode); it is a cheap
    physical backstop — a capture high above sea level cannot be the ocean."""
    path = Path(path)
    if not path.exists() or shutil.which("ffprobe") is None:
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_entries",
             "format_tags", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        tags = (json.loads(out.stdout or "{}").get("format", {}) or {}).get("tags", {}) or {}
    except Exception:
        return None
    iso = (tags.get("location")
           or tags.get("com.apple.quicktime.location.ISO6709")
           or tags.get("location-eng"))
    if not iso:
        return None
    # ISO6709: +DD.DDDD+DDD.DDDD+AAA.AAA/ — the 3rd signed number is altitude.
    import re
    nums = re.findall(r"[+-]\d+(?:\.\d+)?", iso)
    if len(nums) >= 3:
        try:
            return float(nums[2])
        except ValueError:
            return None
    return None


def ensure_geo_cols(con: sqlite3.Connection) -> None:
    """Idempotently add the GPS-grounding columns (runs on both Mac ingest and VM import,
    so the ingest_register export/import carries them across)."""
    for col, typ in (("gps_alt", "REAL"), ("geo_place", "TEXT"), ("geo_water", "TEXT")):
        try:
            con.execute(f"ALTER TABLE assets ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass  # already added


# ── Grounding label for prompts / gates (consumed by the Writer + Giri, next steps) ──
_WATER_KO = {"ocean": "바다·해안", "inland_water": "내륙 수역(강/호수/개울)", "none": "바다 아님"}


def geo_label(place: str | None, water: str | None, alt: float | None = None) -> str | None:
    """Short, human, grounded location string for prompt injection, e.g.
    '경상북도 문경시 가은읍 원북리 · 내륙 수역(강/호수/개울)'. None when there is no GPS."""
    if not place and not water:
        return None
    bits = []
    if place:
        bits.append(str(place))
    if water and water in _WATER_KO:
        tag = _WATER_KO[water]
        if alt is not None and water != "ocean" and alt >= 20:
            tag += f"·고도 {alt:.0f}m"
        bits.append(tag)
    return " · ".join(bits) if bits else None
