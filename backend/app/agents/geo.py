"""Finding the user's town, where the farms are, and how far the drive is.

The sample towns are looked up offline. Any other town or zip code in NJ, NY or PA goes to free
online services: zippopotam.us for zip codes, OpenStreetMap's Nominatim for town names,
and the OSRM demo server for drive times. Set FARMFIND_OFFLINE=1 to use only the sample towns."""
from __future__ import annotations

import json
import math
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import BaseModel

from ..data_loader import DATA_DIR
from ..paths import FARMS_DIR

LOCATIONS_FILE = DATA_DIR / "locations.json"
FARM_LOCATIONS_FILE = FARMS_DIR / "locations.json"
EARTH_RADIUS_MILES = 3958.8
METERS_PER_MILE = 1609.344
HTTP_HEADERS = {"User-Agent": "FarmFind/0.2 (farm price comparison)"}
ZIP_PATTERN = re.compile(r"^\d{5}$")
# The farms deliver and hold pickups only around these states.
SERVED_STATES = {"NJ", "NY", "PA"}


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


class OnlineGeocoder:
    """A town ("Princeton, NJ") or zip code ("08540") in New Jersey, New York or
    Pennsylvania. Places in other states return None."""

    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=15, headers=HTTP_HEADERS)

    @lru_cache(maxsize=256)
    def geocode(self, place: str) -> Place | None:
        text = place.strip()
        if ZIP_PATTERN.match(text):
            response = self.client.get(f"https://api.zippopotam.us/us/{text}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            found = response.json()["places"][0]
            if found["state abbreviation"] not in SERVED_STATES:
                return None
            return Place(
                name=f"{found['place name']}, {found['state abbreviation']} {text}",
                coordinates=Coordinates(lat=float(found["latitude"]), lon=float(found["longitude"])),
            )
        response = self.client.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": text, "countrycodes": "us", "format": "jsonv2", "limit": 10, "addressdetails": 1},
        )
        response.raise_for_status()
        served = [
            result
            for result in response.json()
            if result.get("address", {}).get("ISO3166-2-lvl4", "").removeprefix("US-") in SERVED_STATES
        ]
        if not served:
            return None
        found = served[0]
        address = found["address"]
        town = address.get("city") or address.get("town") or address.get("village") or found["name"]
        state = address["ISO3166-2-lvl4"].removeprefix("US-")
        return Place(
            name=f"{town}, {state}" if state else town,
            coordinates=Coordinates(lat=float(found["lat"]), lon=float(found["lon"])),
        )

    def known_places(self) -> list[str]:
        return []


class ChainGeocoder:
    """Tries each geocoder in turn; the first one that knows the place wins."""

    def __init__(self, geocoders: list[Geocoder]):
        self.geocoders = geocoders

    def geocode(self, place: str) -> Place | None:
        for geocoder in self.geocoders:
            found = geocoder.geocode(place)
            if found is not None:
                return found
        return None

    def known_places(self) -> list[str]:
        if any(isinstance(geocoder, OnlineGeocoder) for geocoder in self.geocoders):
            return []
        return [name for geocoder in self.geocoders for name in geocoder.known_places()]


def offline() -> bool:
    return os.environ.get("FARMFIND_OFFLINE") == "1"


@lru_cache(maxsize=1)
def default_geocoder() -> Geocoder:
    gazetteer = GazetteerGeocoder.from_file()
    return gazetteer if offline() else ChainGeocoder([gazetteer, OnlineGeocoder()])


class Drive(BaseModel):
    miles: float
    minutes: float


class Router(Protocol):
    """Driving distance and time from one point to several."""

    def drives(self, origin: Coordinates, destinations: list[Coordinates]) -> list[Drive | None]: ...


class OsrmRouter:
    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=20, headers=HTTP_HEADERS)

    def drives(self, origin: Coordinates, destinations: list[Coordinates]) -> list[Drive | None]:
        if not destinations:
            return []
        points = ";".join(f"{point.lon},{point.lat}" for point in [origin, *destinations])
        response = self.client.get(
            f"https://router.project-osrm.org/table/v1/driving/{points}",
            params={"sources": 0, "annotations": "duration,distance"},
        )
        response.raise_for_status()
        table = response.json()
        if table.get("code") != "Ok":
            raise RuntimeError(f"Drive-time service answered {table.get('code')}: {table.get('message', '')}")
        drives: list[Drive | None] = []
        for seconds, meters in zip(table["durations"][0][1:], table["distances"][0][1:]):
            if seconds is None or meters is None:
                drives.append(None)
            else:
                drives.append(Drive(miles=round(meters / METERS_PER_MILE, 1), minutes=round(seconds / 60)))
        return drives


@lru_cache(maxsize=1)
def default_router() -> Router | None:
    return None if offline() else OsrmRouter()


@lru_cache(maxsize=1)
def load_vendor_sites() -> dict[str, VendorSite]:
    """The sample farms' sites, plus the real farms' sites when a farms folder is in use."""
    sites = json.loads(LOCATIONS_FILE.read_text(encoding="utf-8"))["vendor_sites"]
    if FARM_LOCATIONS_FILE != LOCATIONS_FILE and FARM_LOCATIONS_FILE.exists():
        sites |= json.loads(FARM_LOCATIONS_FILE.read_text(encoding="utf-8"))["vendor_sites"]
    return {vendor_id: VendorSite(**site) for vendor_id, site in sites.items()}


def haversine_miles(a: Coordinates, b: Coordinates) -> float:
    lat_a, lat_b = math.radians(a.lat), math.radians(b.lat)
    d_lat = lat_b - lat_a
    d_lon = math.radians(b.lon - a.lon)
    h = math.sin(d_lat / 2) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin(d_lon / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(h))
