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
  storage_preference: string;
};

const FIELDS: {
  key: CanonicalProduct;
  label: string;
  unit: string;
  packaging: boolean;
  statePrefs: boolean;
}[] = [
  { key: "cow_milk", label: "Cow milk", unit: "gallon", packaging: true, statePrefs: true },
  { key: "sheep_milk", label: "Sheep milk", unit: "gallon", packaging: true, statePrefs: true },
  { key: "cream", label: "Cream", unit: "pint", packaging: true, statePrefs: true },
  { key: "butter", label: "Butter", unit: "lb", packaging: true, statePrefs: true },
  { key: "cheese", label: "Cheese", unit: "lb", packaging: false, statePrefs: true },
  { key: "eggs", label: "Eggs", unit: "count", packaging: false, statePrefs: false },
];

const EMPTY_PREFS: ItemPrefs = {
  quantity: "",
  packaging_preference: "any",
  salt_preference: "any",
  storage_preference: "any",
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
          unit: f.unit,
          packaging_preference: row.packaging_preference,
          salt_preference: row.salt_preference,
          storage_preference: row.storage_preference,
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
        <h2>Requested Cart</h2>
        <div className="cart-builder-grid">
          <div className="cart-builder-head">Product</div>
          <div className="cart-builder-head">Quantity</div>
          <div className="cart-builder-head">Unit</div>
          <div className="cart-builder-head">Packaging preference</div>
          <div className="cart-builder-head">Salt preference</div>
          <div className="cart-builder-head">Storage preference</div>
          {FIELDS.map((f) => (
            <div
              key={f.key}
              className={`cart-builder-row ${
                parseFloat(items[f.key].quantity) > 0 ? "" : "cart-builder-row-empty"
              }`}
            >
              <strong>{f.label}</strong>
              <input
                type="number"
                min="0"
                step="any"
                value={items[f.key].quantity}
                onChange={(e) => updateItem(f.key, { quantity: e.target.value })}
              />
              <span>{f.unit}</span>
              {f.packaging ? (
                <select
                  value={items[f.key].packaging_preference}
                  onChange={(e) =>
                    updateItem(f.key, { packaging_preference: e.target.value })
                  }
                >
                  <option value="any">Any</option>
                  <option value="prefer_glass">Prefer glass</option>
                  <option value="glass_only">Glass only</option>
                  <option value="avoid_plastic">Avoid plastic</option>
                </select>
              ) : (
                <span className="helper-text">Any</span>
              )}
              {f.key === "butter" ? (
                <select
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
                <span className="helper-text">Any</span>
              )}
              {f.statePrefs ? (
                <select
                  value={items[f.key].storage_preference}
                  onChange={(e) =>
                    updateItem(f.key, { storage_preference: e.target.value })
                  }
                >
                  <option value="any">Any</option>
                  <option value="fresh_only">Fresh only</option>
                  <option value="prefer_fresh">Prefer fresh</option>
                  <option value="allow_frozen">Allow frozen</option>
                </select>
              ) : (
                <span className="helper-text">Any</span>
              )}
            </div>
          ))}
        </div>
        <label className="field overbuy-field">
          <span>Allow overbuy %</span>
          <input
            type="number"
            min="0"
            step="any"
            value={overbuy}
            onChange={(e) => setOverbuy(e.target.value)}
          />
        </label>
      </div>

      <div className="card">
        <h2>Fulfillment</h2>
        <div className="fulfillment-grid">
          <label className="field">
            <span>Fulfillment mode</span>
            <select
              value={constraints.fulfillment_mode}
              onChange={(e) =>
                onConstraintsChange({
                  fulfillment_mode: e.target
                    .value as OptimizerConstraints["fulfillment_mode"],
                })
              }
            >
              <option value="best">Best overall</option>
              <option value="mixed">Mixed pickup and delivery</option>
              <option value="delivery_only">Delivery only</option>
              <option value="pickup_only">Pickup/drop-off only</option>
              <option value="ups_only">UPS only</option>
              <option value="farm_truck_only">Farm truck only</option>
              <option value="no_pickup">No pickup</option>
            </select>
          </label>
          <label className="field">
            <span>Max pickup distance</span>
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
            <span>Vehicle MPG</span>
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
            <span>Max vendors</span>
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
            Round-trip pickup cost
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
            Avoid pickup-required vendors
          </label>
        </div>
        {avoidPickupDisabled && (
          <p className="helper-text">
            Avoid pickup is only relevant when pickup and delivery can both be ranked.
          </p>
        )}
      </div>

      <div className="card">
        <h2>Catalog Source</h2>
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
            <option value="imported_only">Real website imports only</option>
            <option value="demo_only">Demo samples only</option>
            <option value="imported_plus_demo">Real + demo debugging</option>
          </select>
        </label>
      </div>

      <div className="card">
        <h2>Pricing</h2>
        <div className="form-grid form-grid-compact">
          <label className="checkbox checkbox-field">
            <input
              type="checkbox"
              checked={constraints.enforce_minimum_order}
              onChange={(e) =>
                onConstraintsChange({ enforce_minimum_order: e.target.checked })
              }
            />
            Enforce minimum order
          </label>
          <label className="checkbox checkbox-field">
            <input
              type="checkbox"
              checked={constraints.use_subscription_pricing}
              onChange={(e) =>
                onConstraintsChange({ use_subscription_pricing: e.target.checked })
              }
            />
            Use subscription pricing
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
            Include returnable deposits in total
          </label>
        </div>
      </div>

      <div className="card">
        <h2>Active Constraints</h2>
        {activeFilterLabels.length > 0 ? (
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
        ) : (
          <p className="helper-text">No active constraints.</p>
        )}
        <div className="form-footer">
          <button type="button" className="secondary-button" onClick={onResetFilters}>
            Reset filters
          </button>
          <button onClick={submit} disabled={loading}>
            {loading ? "Optimizing..." : "Optimize Cart"}
          </button>
        </div>
      </div>
    </>
  );
}
