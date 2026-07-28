"""Client for the NESO Carbon Intensity API for Great Britain - free,
public, no API key needed.

Trimmed for the web app: only the /regional endpoint is needed here (no
24h forecast, that's a CLI/demo-script-only chart in the main package).

Fallback chain (see main README for the full design rationale):
    1. live API call
    2. last-known-good cache (written to a temp dir - Vercel's deployed
       source directory is read-only, so this can't live under data/ the
       way it does in the main package; it's a nice-to-have warm-instance
       optimisation here, not load-bearing)
    3. bundled sample snapshot shipped in data/samples/ (read-only, fine)
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.carbonintensity.org.uk"
DEFAULT_TIMEOUT = 10  # seconds
DEFAULT_CACHE_MAX_AGE = 1800  # 30 min - matches the API's own refresh cadence

# lib/carbon_api.py -> parent = lib/, parents[1] = green-dijkstra-web/
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLES_DIR = _PROJECT_ROOT / "data" / "samples"
# Serverless functions get a read-only source tree but a writable /tmp -
# use that instead of a repo-relative path.
DEFAULT_CACHE_DIR = Path(tempfile.gettempdir()) / "green_dijkstra_cache"


class CarbonDataUnavailableError(RuntimeError):
    """Raised only if live API, cache, AND bundled sample all fail."""


@dataclass
class DataSnapshot:
    payload: Any
    source: str  # "live" | "cache" | "bundled_sample"
    fetched_at: str
    is_stale: bool = False


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _first(data):
    if isinstance(data, list):
        return data[0]
    return data


class CarbonIntensityClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        samples_dir: Path = DEFAULT_SAMPLES_DIR,
        cache_max_age: float = DEFAULT_CACHE_MAX_AGE,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.cache_dir = Path(cache_dir)
        self.samples_dir = Path(samples_dir)
        self.cache_max_age = cache_max_age
        self.session = session or requests.Session()

    def _cache_path(self, name: str) -> Path:
        return self.cache_dir / f"{name}.json"

    def _sample_path(self, name: str) -> Path:
        return self.samples_dir / f"{name}.json"

    def _read_json(self, path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read %s: %s", path, exc)
            return None

    def _write_cache(self, name: str, payload: Any) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            envelope = {"fetched_at": _utcnow_iso(), "payload": payload}
            self._cache_path(name).write_text(json.dumps(envelope), encoding="utf-8")
        except OSError as exc:
            # Best-effort only - a cold/read-only /tmp shouldn't break a live request.
            logger.warning("Could not write cache for %s: %s", name, exc)

    def _load_with_fallback(self, name: str, fetch_raw_fn, extract_fn) -> DataSnapshot:
        try:
            raw = fetch_raw_fn()
            self._write_cache(name, raw)
            return DataSnapshot(payload=extract_fn(raw), source="live", fetched_at=_utcnow_iso())
        except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
            logger.warning("Live fetch of %s failed (%s); falling back to cache.", name, exc)

        cached = self._read_json(self._cache_path(name))
        if cached is not None:
            try:
                payload = extract_fn(cached["payload"])
                age = (
                    datetime.now(timezone.utc)
                    - datetime.strptime(cached["fetched_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                        tzinfo=timezone.utc
                    )
                ).total_seconds()
                return DataSnapshot(
                    payload=payload,
                    source="cache",
                    fetched_at=cached["fetched_at"],
                    is_stale=age > self.cache_max_age,
                )
            except (KeyError, IndexError) as exc:
                logger.warning("Cached %s payload malformed (%s); trying bundled sample.", name, exc)

        sample_raw = self._read_json(self._sample_path(name))
        if sample_raw is not None:
            try:
                payload = extract_fn(sample_raw)
                return DataSnapshot(
                    payload=payload, source="bundled_sample", fetched_at="unknown", is_stale=True
                )
            except (KeyError, IndexError) as exc:
                logger.warning("Bundled sample %s payload malformed: %s", name, exc)

        raise CarbonDataUnavailableError(
            f"No live data, cache, or bundled sample available for '{name}'."
        )

    def get_regional_snapshot(self) -> DataSnapshot:
        """All GB regions' current carbon intensity."""

        def fetch_raw():
            r = self.session.get(f"{self.base_url}/regional", timeout=self.timeout)
            r.raise_for_status()
            return r.json()

        def extract(raw):
            return _first(raw["data"])["regions"]

        return self._load_with_fallback("regional_intensity", fetch_raw, extract)

    def get_regional_intensity_by_id(self) -> tuple[dict[int, dict], str]:
        """Convenience wrapper: {region_id: {shortname, gco2_per_kwh, index}}."""
        snapshot = self.get_regional_snapshot()
        by_id = {
            region["regionid"]: {
                "shortname": region["shortname"],
                "gco2_per_kwh": region["intensity"]["forecast"],
                "index": region["intensity"]["index"],
            }
            for region in snapshot.payload
        }
        return by_id, snapshot.source
