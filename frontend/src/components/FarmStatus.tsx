import type { Vendor } from "../types";

const DAY_MS = 24 * 60 * 60 * 1000;
const STALE_DAYS = 7;
const OLD_DAYS = 14;

interface Props {
  vendors: Vendor[];
}

function age(lastFetchedAt: string | null): { label: string; tone: "fresh" | "stale" | "old" } {
  if (!lastFetchedAt) return { label: "never pulled", tone: "old" };
  const days = Math.floor((Date.now() - new Date(lastFetchedAt).getTime()) / DAY_MS);
  const date = new Date(lastFetchedAt).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const label = days <= 0 ? `today (${date})` : days === 1 ? `yesterday (${date})` : `${days} days ago (${date})`;
  return { label, tone: days > OLD_DAYS ? "old" : days > STALE_DAYS ? "stale" : "fresh" };
}

const money = (value: number) => `$${value % 1 === 0 ? value.toFixed(0) : value.toFixed(2)}`;

function delivery(vendor: Vendor): string {
  const parts: string[] = [];
  if (vendor.pickup_dropoff_available) {
    let pickup = vendor.pickup_rate_per_lb > 0 ? `pickup ${money(vendor.pickup_rate_per_lb)}/lb` : "pickup";
    if (vendor.free_pickup_threshold != null) pickup += `, free over ${money(vendor.free_pickup_threshold)}`;
    parts.push(pickup);
  }
  if (vendor.ups_shipping_available) {
    const flat = vendor.ups_shipping_fee + vendor.ups_packing_fee + vendor.ups_cooler_fee;
    const pieces = [flat > 0 ? money(flat) : "", vendor.ups_rate_per_lb > 0 ? `${money(vendor.ups_rate_per_lb)}/lb` : ""];
    let ups = `UPS ${pieces.filter(Boolean).join(" + ")}`.trim();
    if (vendor.free_shipping_threshold != null) ups += `, free over ${money(vendor.free_shipping_threshold)}`;
    parts.push(ups);
  }
  if (vendor.farm_truck_delivery_available) {
    parts.push(vendor.farm_truck_delivery_fee > 0 ? `door delivery ${money(vendor.farm_truck_delivery_fee)}` : "door delivery");
  }
  return parts.join(" · ") || "no delivery listed";
}

/** One line per farm: when its prices were last pulled and how it ships. */
export default function FarmStatus({ vendors }: Props) {
  const farms = vendors.filter((vendor) => vendor.source_type !== "mock");
  if (farms.length === 0) return null;
  const anyOld = farms.some((vendor) => age(vendor.last_fetched_at).tone === "old");
  return (
    <section className="card farm-status">
      <h2>Farms</h2>
      {anyOld && <p className="warning-text">Some prices are more than two weeks old; check the farm's shop before ordering.</p>}
      <ul>
        {farms.map((vendor) => {
          const pulled = age(vendor.last_fetched_at);
          return (
            <li key={vendor.id}>
              {vendor.website_url ? (
                <a href={vendor.website_url} target="_blank" rel="noreferrer">
                  {vendor.name}
                </a>
              ) : (
                vendor.name
              )}
              <span className={`pulled pulled-${pulled.tone}`}>prices {pulled.label}</span>
              <span className="helper-text">{delivery(vendor)}</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
