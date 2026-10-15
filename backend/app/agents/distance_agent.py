"""DistanceAgent: resolves the user's location and measures distance to vendors."""
from __future__ import annotations

from .bus import MessageBus
from .geo import Coordinates, Geocoder, Router, VendorSite, haversine_miles
from .messages import DistanceQuery, DistanceResult, VendorDistance


class DistanceAgent:
    name = "distance_agent"
    action = "measure_distances"
    input_model = DistanceQuery

    def __init__(self, geocoder: Geocoder, sites: dict[str, VendorSite], router: Router | None = None):
        self.geocoder = geocoder
        self.sites = sites
        self.router = router

    def handle(self, query: DistanceQuery, bus: MessageBus) -> DistanceResult:
        place = self.geocoder.geocode(query.location)
        if place is None:
            return DistanceResult(
                location=query.location,
                recognised=False,
                known_locations=self.geocoder.known_places(),
            )
        origin = place.coordinates
        vendors = [self._measure(origin, vendor_id) for vendor_id in query.vendor_ids]
        return DistanceResult(
            location=place.name, recognised=True, coordinates=origin, vendors=vendors
        )

    def _measure(self, origin: Coordinates, vendor_id: str) -> VendorDistance:
        """Road miles and drive minutes when a router is set, straight-line miles otherwise."""
        site = self.sites.get(vendor_id)
        if site is None:
            return VendorDistance(vendor_id=vendor_id)
        points = {"": site.coordinates, **site.pickup_locations}
        miles = {key: round(haversine_miles(origin, point), 1) for key, point in points.items()}
        minutes: dict[str, float] = {}
        if self.router is not None:
            for key, drive in zip(points, self.router.drives(origin, list(points.values()))):
                if drive is not None:
                    miles[key] = drive.miles
                    minutes[key] = drive.minutes
        return VendorDistance(
            vendor_id=vendor_id,
            farm_miles=miles.pop(""),
            farm_minutes=minutes.pop("", None),
            pickup_miles=miles,
            pickup_minutes=minutes,
        )
