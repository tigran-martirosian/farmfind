import { useState } from "react";
import type {
  CanonicalProduct,
  OptimizerConstraints,
  OptimizeItem,
  OptimizeRequest,
} from "../types";

interface Props {
  onOptimize: (request: OptimizeRequest) => void;
  constraints: OptimizerConstraints;
  activeFilterLabels: { key: string; label: string }[];
  onConstraintsChange: (updates: Partial<OptimizerConstraints>) => void;
  onRemoveFilter: (key: string) => void;
  onResetFilters: () => void;
  loading: boolean;
}

type ItemPrefs = {
  quantity: string;
  packaging_preference: string;
  salt_preference: string;
};

// Every product the farms sell comes in glass somewhere, except eggs. Only butter and
// cheese come salted or unsalted.
const FIELDS: {
  key: CanonicalProduct;
  label: string;
  unit: string;
  packaging: boolean;
  salt: boolean;
}[] = [
  { key: "cow_milk", label: "Cow milk", unit: "gallon", packaging: true, salt: false },
  { key: "sheep_milk", label: "Sheep milk", unit: "gallon", packaging: true, salt: false },
  { key: "cream", label: "Cream", unit: "pint", packaging: true, salt: false },
  { key: "butter", label: "Butter", unit: "lb", packaging: true, salt: true },
  { key: "cheese", label: "Cheese", unit: "lb", packaging: true, salt: true },
  { key: "eggs", label: "Eggs", unit: "eggs", packaging: false, salt: false },
];

const EMPTY_PREFS: ItemPrefs = {
  quantity: "",
  packaging_preference: "any",
  salt_preference: "any",
};

const DEFAULT_ITEMS: Record<CanonicalProduct, ItemPrefs> = {
  cow_milk: { ...EMPTY_PREFS, quantity: "2" },
  sheep_milk: { ...EMPTY_PREFS },
  cream: { ...EMPTY_PREFS, quantity: "4" },
  butter: { ...EMPTY_PREFS, quantity: "5" },
  cheese: { ...EMPTY_PREFS, quantity: "2" },
  eggs: { ...EMPTY_PREFS, quantity: "36" },
};

export default function OptimizeForm({
  onOptimize,
  constraints,
  activeFilterLabels,
  onConstraintsChange,
  onRemoveFilter,
  onResetFilters,
  loading,
}: Props) {
  const [items, setItems] = useState<Record<CanonicalProduct, ItemPrefs>>(DEFAULT_ITEMS);
  const [overbuy, setOverbuy] = useState("20");

  function updateItem(key: CanonicalProduct, updates: Partial<ItemPrefs>) {
    setItems({ ...items, [key]: { ...items[key], ...updates } });
  }

  function numericOrNull(value: string): number | null {
    return value ? parseFloat(value) : null;
  }

  function positiveNumericOrNull(value: string): number | null {
    const parsed = numericOrNull(value);
    return parsed && parsed > 0 ? parsed : null;
  }

  function submit() {
    const requestItems: OptimizeItem[] = FIELDS.flatMap((f) => {
      const row = items[f.key];
      const qty = parseFloat(row.quantity);
      if (!qty || qty <= 0) return [];
      return [
        {
          product_type: f.key,
          quantity: qty,
          unit: f.key === "eggs" ? "count" : f.unit,
          packaging_preference: row.packaging_preference,
          salt_preference: row.salt_preference,
          storage_preference: "any",
        },
      ];
    });
    onOptimize({
      items: requestItems,
      include_delivery: true,
      allow_overbuy_percent: parseFloat(overbuy) || 0,
      ...constraints,
    });
  }

  const avoidPickupDisabled =
    constraints.fulfillment_mode !== "best" &&
    constraints.fulfillment_mode !== "mixed";

  return (
    <>
      <div className="card">
        <h2>What you need</h2>
        <div className="cart-builder-grid">
          <div className="cart-builder-head">Product</div>
          <div className="cart-builder-head">How much</div>
          <div className="cart-builder-head">Glass jars</div>
          <div className="cart-builder-head">Salt</div>
          {FIELDS.map((f) => (
            <div
              key={f.key}
              className={`cart-builder-row ${
                parseFloat(items[f.key].quantity) > 0 ? "" : "cart-builder-row-empty"
              }`}
            >
              <strong>{f.label}</strong>
              <div className="qty-field">
                <input
                  type="number"
                  min="0"
                  step="any"
                  placeholder="0"
                  aria-label={`${f.label} quantity`}
                  value={items[f.key].quantity}
                  onChange={(e) => updateItem(f.key, { quantity: e.target.value })}
                />
                <span>{f.unit}</span>
              </div>
              {f.packaging ? (
                <select
                  aria-label={`${f.label} glass`}
                  value={items[f.key].packaging_preference}
                  onChange={(e) =>
                    updateItem(f.key, { packaging_preference: e.target.value })
                  }
                >
                  <option value="any">Any</option>
                  <option value="prefer_glass">Prefer glass</option>
                  <option value="glass_only">Glass only</option>
                </select>
              ) : (
                <span />
              )}
              {f.salt ? (
                <select
                  aria-label={`${f.label} salt`}
                  value={items[f.key].salt_preference}
                  onChange={(e) =>
                    updateItem(f.key, { salt_preference: e.target.value })
                  }
                >
                  <option value="any">Any</option>
                  <option value="salted">Salted</option>
                  <option value="unsalted">Unsalted</option>
                </select>
              ) : (
                <span />
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <h2>Where you are</h2>
        <div className="fulfillment-grid">
          <label className="field">
            <span>Town or zip code</span>
            <input
              type="text"
              placeholder="e.g. Princeton, NJ or 08540"
              value={constraints.location ?? ""}
              onChange={(e) => onConstraintsChange({ location: e.target.value || null })}
            />
          </label>
          <label className="field">
            <span>Longest pickup drive (min)</span>
            <input
              type="number"
              min="0"
              step="5"
              value={constraints.max_pickup_minutes ?? ""}
              onChange={(e) =>
                onConstraintsChange({ max_pickup_minutes: numericOrNull(e.target.value) })
              }
            />
          </label>
          <label className="field">
            <span>How to get it</span>
            <select
              value={constraints.fulfillment_mode}
              onChange={(e) =>
                onConstraintsChange({
                  fulfillment_mode: e.target
                    .value as OptimizerConstraints["fulfillment_mode"],
                })
              }
            >
              <option value="best">Cheapest any way</option>
              <option value="mixed">Mix pickup and delivery</option>
              <option value="delivery_only">Delivery only</option>
              <option value="pickup_only">Pickup only</option>
              <option value="ups_only">UPS only</option>
              <option value="farm_truck_only">Farm's own delivery only</option>
              <option value="no_pickup">No pickup</option>
            </select>
          </label>
          <label className="field">
            <span>Most farms in one order</span>
            <input
              type="number"
              min="1"
              step="1"
              value={constraints.max_vendors ?? ""}
              onChange={(e) =>
                onConstraintsChange({
                  max_vendors: e.target.value ? parseInt(e.target.value, 10) : null,
                })
              }
            />
          </label>
        </div>

        <details className="more-options">
          <summary>More options</summary>
          <div className="fulfillment-grid">
            <label className="field">
              <span>Max pickup distance (mi)</span>
              <input
                type="number"
                min="0"
                step="0.1"
                value={constraints.max_pickup_distance_miles ?? ""}
                onChange={(e) =>
                  onConstraintsChange({
                    max_pickup_distance_miles: e.target.value
                      ? parseFloat(e.target.value)
                      : null,
                  })
                }
              />
            </label>
            <label className="field">
              <span>Car MPG</span>
              <input
                type="number"
                min="0.1"
                step="0.1"
                value={constraints.vehicle_mpg ?? ""}
                onChange={(e) =>
                  onConstraintsChange({
                    vehicle_mpg: positiveNumericOrNull(e.target.value),
                  })
                }
              />
            </label>
            <label className="field">
              <span>Gas price / gallon</span>
              <input
                type="number"
                min="0"
                step="0.01"
                value={constraints.fuel_price_per_gallon ?? ""}
                onChange={(e) =>
                  onConstraintsChange({
                    fuel_price_per_gallon: numericOrNull(e.target.value),
                  })
                }
              />
            </label>
            <label className="field">
              <span>Allow overbuy %</span>
              <input
                type="number"
                min="0"
                step="any"
                value={overbuy}
                onChange={(e) => setOverbuy(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Product catalog</span>
              <select
                value={constraints.catalog_mode}
                onChange={(e) =>
                  onConstraintsChange({
                    catalog_mode: e.target.value as OptimizerConstraints["catalog_mode"],
                  })
                }
              >
                <option value="imported_only">Real farm prices</option>
                <option value="demo_only">Sample data</option>
                <option value="imported_plus_demo">Real + sample</option>
              </select>
            </label>
          </div>
          <div className="form-grid form-grid-compact">
            <label className="checkbox checkbox-field">
              <input
                type="checkbox"
                checked={constraints.use_round_trip_pickup_cost}
                onChange={(e) =>
                  onConstraintsChange({
                    use_round_trip_pickup_cost: e.target.checked,
                  })
                }
              />
              Count the drive both ways
            </label>
            <label className="checkbox checkbox-field">
              <input
                type="checkbox"
                checked={
                  constraints.avoid_pickup_required &&
                  (constraints.fulfillment_mode === "best" ||
                    constraints.fulfillment_mode === "mixed")
                }
                disabled={avoidPickupDisabled}
                onChange={(e) =>
                  onConstraintsChange({ avoid_pickup_required: e.target.checked })
                }
              />
              Avoid farms that are pickup only
            </label>
            <label className="checkbox checkbox-field">
              <input
                type="checkbox"
                checked={constraints.enforce_minimum_order}
                onChange={(e) =>
                  onConstraintsChange({ enforce_minimum_order: e.target.checked })
                }
              />
              Respect order minimums
            </label>
            <label className="checkbox checkbox-field">
              <input
                type="checkbox"
                checked={constraints.use_subscription_pricing}
                onChange={(e) =>
                  onConstraintsChange({ use_subscription_pricing: e.target.checked })
                }
              />
              Use subscription prices
            </label>
            <label className="checkbox checkbox-field">
              <input
                type="checkbox"
                checked={constraints.include_returnable_deposits_in_total}
                onChange={(e) =>
                  onConstraintsChange({
                    include_returnable_deposits_in_total: e.target.checked,
                  })
                }
              />
              Include jar deposits in the total
            </label>
          </div>
        </details>

        {activeFilterLabels.length > 0 && (
          <div className="filter-chip-row">
            {activeFilterLabels.map((filter) => (
              <button
                key={filter.key}
                type="button"
                className="filter-chip"
                onClick={() => onRemoveFilter(filter.key)}
              >
                {filter.label} <span aria-hidden="true">x</span>
              </button>
            ))}
          </div>
        )}
        <div className="form-footer">
          <button type="button" className="secondary-button" onClick={onResetFilters}>
            Reset filters
          </button>
          <button onClick={submit} disabled={loading}>
            {loading ? "Finding the cheapest cart..." : "Find the cheapest cart"}
          </button>
        </div>
      </div>
    </>
  );
}
