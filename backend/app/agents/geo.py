"""Offline geocoding of the sample towns, vendor sites, and haversine distance."""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from ..data_loader import DATA_DIR

LOCATIONS_FILE = DATA_DIR / "locations.json"
EARTH_RADIUS_MILES = 3958.8


class Coordinates(BaseModel):
    lat: float
    lon: float


class Place(BaseModel):
    name: str
    coordinates: Coordinates


class VendorSite(BaseModel):
    """Where a vendor's farm and pickup points are, and how far its truck goes."""

    lat: float
    lon: float
    pickup_locations: dict[str, Coordinates] = {}
    farm_truck_radius_miles: float | None = None

    @property
    def coordinates(self) -> Coordinates:
        return Coordinates(lat=self.lat, lon=self.lon)


class Geocoder(Protocol):
    """Turns a place name into coordinates. A real geocoding service can
    implement this; known_places() may then return an empty list."""

    def geocode(self, place: str) -> Place | None: ...

    def known_places(self) -> list[str]: ...


class GazetteerGeocoder:
    """Looks places up in a fixed list. Unknown names return None, never a guess."""

    def __init__(self, places: list[Place]):
        self._places = {_key(place.name): place for place in places}

    @classmethod
    def from_file(cls, path: Path = LOCATIONS_FILE) -> "GazetteerGeocoder":
        towns = json.loads(path.read_text(encoding="utf-8"))["towns"]
        return cls(
            [
                Place(name=town["name"], coordinates=Coordinates(lat=town["lat"], lon=town["lon"]))
                for town in towns
            ]
        )

    def geocode(self, place: str) -> Place | None:
        return self._places.get(_key(place))

    def known_places(self) -> list[str]:
        return [place.name for place in self._places.values()]


def _key(name: str) -> str:
    return " ".join(name.casefold().replace(",", " ").split())


@lru_cache(maxsize=1)
def load_vendor_sites() -> dict[str, VendorSite]:
    sites = json.loads(LOCATIONS_FILE.read_text(encoding="utf-8"))["vendor_sites"]
    return {vendor_id: VendorSite(**site) for vendor_id, site in sites.items()}


def haversine_miles(a: Coordinates, b: Coordinates) -> float:
    lat_a, lat_b = math.radians(a.lat), math.radians(b.lat)
    d_lat = lat_b - lat_a
    d_lon = math.radians(b.lon - a.lon)
    h = math.sin(d_lat / 2) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin(d_lon / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(h))
