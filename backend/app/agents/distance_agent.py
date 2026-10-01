"""DistanceAgent: resolves the user's location and measures distance to vendors."""
from __future__ import annotations

from .bus import MessageBus
from .geo import Geocoder, VendorSite, haversine_miles
from .messages import DistanceQuery, DistanceResult, VendorDistance


class DistanceAgent:
    name = "distance_agent"
    action = "measure_distances"
    input_model = DistanceQuery

    def __init__(self, geocoder: Geocoder, sites: dict[str, VendorSite]):
        self.geocoder = geocoder
        self.sites = sites

    def handle(self, query: DistanceQuery, bus: MessageBus) -> DistanceResult:
        place = self.geocoder.geocode(query.location)
        if place is None:
            return DistanceResult(
                location=query.location,
                recognised=False,
                known_locations=self.geocoder.known_places(),
            )
        origin = place.coordinates
        vendors = []
        for vendor_id in query.vendor_ids:
            site = self.sites.get(vendor_id)
            if site is None:
                vendors.append(VendorDistance(vendor_id=vendor_id))
                continue
            vendors.append(
                VendorDistance(
                    vendor_id=vendor_id,
                    farm_miles=round(haversine_miles(origin, site.coordinates), 1),
                    pickup_miles={
                        location_id: round(haversine_miles(origin, coordinates), 1)
                        for location_id, coordinates in site.pickup_locations.items()
                    },
                )
            )
        return DistanceResult(
            location=place.name, recognised=True, coordinates=origin, vendors=vendors
        )
