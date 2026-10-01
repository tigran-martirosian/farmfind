import { useState } from "react";
import type {
  CartPlan,
  DeliveryCharge,
  HandlingCharge,
  OptimizeResponse,
  SelectedItem,
  VendorBreakdown,
} from "../types";

interface Props {
  result: OptimizeResponse;
  // The vendor and product actions are optional: the chat panel shows a cart read-only.
  excludedVendorIds?: string[];
  onExcludeVendor?: (vendorId: string) => void;
  onIncludeVendor?: (vendorId: string) => void;
  onRequireVendor?: (vendorId: string) => void;
  onExcludeProduct?: (productId: string) => void;
  onTryVendorMix?: (vendorIds: string[]) => void;
}

interface VendorGroup {
  vendorId: string;
  vendorName: string;
  pickupRequired: boolean;
  items: SelectedItem[];
  itemSubtotal: number;
  deliveryFee: number;
  handlingFee: number;
  fulfillmentCost: number;
  fulfillmentMethod: string;
  breakdown?: VendorBreakdown;
  notes: string[];
}

interface DisplayPlan {
  selected_items: SelectedItem[];
  delivery_charges: DeliveryCharge[];
  handling_charges: HandlingCharge[];
  item_cost_total: number;
  delivery_cost_total: number;
  handling_fee_total: number;
  vendor_breakdowns: VendorBreakdown[];
  regular_product_subtotal: number;
  product_subtotal: number;
  subscription_savings: number;
  fulfillment_cost_total: number;
  returnable_deposits: number;
  total_estimated_cost: number;
  total_due_today: number;
  warnings: string[];
  notes: string[];
}

function formatMoney(value: number): string {
  return `$${value.toFixed(2)}`;
}

function labelProduct(value: string): string {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function labelUnavailableReason(reason: string): string {
  const labels: Record<string, string> = {
    no_matching_product_type: "no matching product type",
    all_matches_out_of_stock: "out of stock",
    no_unit_mapping: "needs unit mapping",
    no_valid_product_after_filters: "filtered out by current constraints",
    no_valid_product_within_overbuy_limit: "overbuy limit blocks available packages",
    requested_packaging_unavailable: "requested packaging unavailable",
    no_valid_cart_plan_with_requested_constraints: "no valid cart with current constraints",
  };
  return labels[reason] ?? reason.replace(/_/g, " ");
}

function feeForVendor(
  vendorId: string,
  charges: Array<DeliveryCharge | HandlingCharge>
): number {
  return charges.find((charge) => charge.vendor_id === vendorId)?.fee ?? 0;
}

function groupItemsByVendor(cartPlan: DisplayPlan): VendorGroup[] {
  const groups = new Map<string, VendorGroup>();

  for (const item of cartPlan.selected_items) {
    if (!groups.has(item.vendor_id)) {
      const breakdown = cartPlan.vendor_breakdowns.find(
        (vendor) => vendor.vendor_id === item.vendor_id
      );
      groups.set(item.vendor_id, {
        vendorId: item.vendor_id,
        vendorName: item.vendor,
        pickupRequired: item.pickup_required,
        items: [],
        itemSubtotal: 0,
        deliveryFee: feeForVendor(item.vendor_id, cartPlan.delivery_charges),
        handlingFee: feeForVendor(item.vendor_id, cartPlan.handling_charges),
        fulfillmentCost: breakdown?.fulfillment_cost ?? 0,
        fulfillmentMethod: breakdown?.fulfillment_method ?? "pickup_dropoff",
        breakdown,
        notes: [],
      });
    }

    const group = groups.get(item.vendor_id)!;
    group.items.push(item);
    group.itemSubtotal += item.item_cost;
    group.pickupRequired = group.pickupRequired || item.pickup_required;
    group.notes.push(...item.notes);
  }

  for (const note of cartPlan.notes) {
    for (const group of groups.values()) {
      if (note.includes(group.vendorName)) {
        group.notes.push(note);
      }
    }
  }

  return Array.from(groups.values()).map((group) => ({
    ...group,
    itemSubtotal: Number(group.itemSubtotal.toFixed(2)),
    notes: Array.from(new Set(group.notes)),
  }));
}

function getVendorTotal(group: VendorGroup): number {
  return group.itemSubtotal + group.fulfillmentCost;
}

function labelFulfillment(value: string): string {
  if (value === "pickup_dropoff") return "Pickup/drop-off";
  if (value === "ups_shipping") return "UPS shipping";
  if (value === "farm_truck_delivery") return "Farm truck delivery";
  return value;
}

function displayTag(value: string | null | undefined): string | null {
  if (!value) return null;
  const clean = value.replace(/_/g, " ").trim();
  if (!clean || clean.toLowerCase() === "unknown") return null;
  return clean;
}

function packageTags(pkg: { packaging: string | null; storage_state: string }) {
  return [displayTag(pkg.packaging), displayTag(pkg.storage_state)].filter(Boolean);
}

function VendorBreakdown({
  plan,
  onExcludeVendor,
  onIncludeVendor,
  onRequireVendor,
  onExcludeProduct,
  excludedVendorIds = [],
}: {
  plan: DisplayPlan;
  excludedVendorIds?: string[];
  onExcludeVendor?: (vendorId: string) => void;
  onIncludeVendor?: (vendorId: string) => void;
  onRequireVendor?: (vendorId: string) => void;
  onExcludeProduct?: (productId: string) => void;
}) {
  const groups = groupItemsByVendor(plan);

  if (groups.length === 0) {
    return <p>No items selected.</p>;
  }

  return (
    <div className="vendor-cart-grid">
      {groups.map((group) => (
        <div key={group.vendorId} className="vendor-card">
          <div className="vendor-card-head">
            <div>
              <h3>{group.vendorName}</h3>
              <span className={`tag ${group.pickupRequired ? "tag-pickup" : "tag-ok"}`}>
                {labelFulfillment(group.fulfillmentMethod)}
              </span>
            </div>
            {(onExcludeVendor || onIncludeVendor || onRequireVendor) && (
              <div className="cart-actions">
                {onIncludeVendor && excludedVendorIds.includes(group.vendorId) ? (
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => onIncludeVendor(group.vendorId)}
                  >
                    Re-include farm
                  </button>
                ) : onExcludeVendor ? (
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => onExcludeVendor(group.vendorId)}
                  >
                    Exclude this farm
                  </button>
                ) : null}
                {onRequireVendor && (
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => onRequireVendor(group.vendorId)}
                  >
                    Force this farm
                  </button>
                )}
              </div>
            )}
          </div>

          <div className="vendor-items">
            {group.items.map((item) => (
              <div key={item.canonical_product} className="vendor-item">
                <div className="vendor-item-title">
                  <strong>{labelProduct(item.canonical_product)}</strong>
                  <span>
                    Requested {item.requested_quantity} {item.normalized_unit},
                    bought {item.purchased_quantity} {item.normalized_unit},
                    overbuy {item.overbuy_percent}%
                  </span>
                </div>
                {item.packages.map((pkg) => (
                  <div
                    key={`${item.canonical_product}-${pkg.product_name}`}
                    className="package-row"
                  >
                    <div>
                      <div>{pkg.product_name}</div>
                      <small>
                        {pkg.package_count} package
                        {pkg.package_count === 1 ? "" : "s"} x{" "}
                        {pkg.package_quantity} {pkg.package_unit}
                      </small>
                    </div>
                    <div>
                      <div>{formatMoney(pkg.regular_price_each)} regular</div>
                      {pkg.subscription_applied && (
                        <small>{formatMoney(pkg.effective_price_each)} subscription</small>
                      )}
                      {packageTags(pkg).length > 0 && <small>{packageTags(pkg).join(" · ")}</small>}
                      {pkg.container_returnable && (
                        <small>
                          {formatMoney(pkg.container_deposit)} refundable deposit each
                        </small>
                      )}
                    </div>
                    <strong>{formatMoney(pkg.line_total)}</strong>
                    {onExcludeProduct && (
                      <button
                        type="button"
                        className="inline-link-button"
                        onClick={() => onExcludeProduct(pkg.product_id)}
                      >
                        Exclude this product
                      </button>
                    )}
                  </div>
                ))}
              </div>
            ))}
          </div>

          {group.notes.length > 0 && (
            <ul className="vendor-notes">
              {group.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
          {group.breakdown && (
            <div className="fulfillment-detail">
              {group.breakdown.fulfillment_method === "pickup_dropoff" && (
                <>
                  <div>Pickup/drop-off: {group.breakdown.pickup_location_name}</div>
                  <div>
                    Distance: {group.breakdown.pickup_distance_miles ?? 0} mi one-way
                  </div>
                  <div>
                    Travel cost: {formatMoney(group.breakdown.pickup_travel_cost)}
                  </div>
                  <div>
                    Pickup/vendor fees:{" "}
                    {formatMoney(
                      group.breakdown.pickup_fee +
                        group.breakdown.pickup_handling_fee +
                        group.breakdown.pickup_vendor_fees
                    )}
                  </div>
                </>
              )}
              {group.breakdown.fulfillment_method === "ups_shipping" && (
                <div>
                  UPS shipping selected: shipping {formatMoney(group.breakdown.ups_shipping_fee)},
                  packing/cooler{" "}
                  {formatMoney(
                    group.breakdown.ups_packing_fee + group.breakdown.ups_cooler_fee
                  )}{" "}
                  <span className="helper-text">(estimated/default, not a captured checkout quote)</span>
                </div>
              )}
              {group.breakdown.fulfillment_method === "farm_truck_delivery" && (
                <div>
                  Farm truck delivery:{" "}
                  {formatMoney(group.breakdown.farm_truck_delivery_fee)}
                </div>
              )}
              <div>
                Minimum: {formatMoney(group.breakdown.minimum_order)}{" "}
                {group.breakdown.minimum_met
                  ? "(met)"
                  : `(short ${formatMoney(group.breakdown.amount_short)})`}
              </div>
              {group.breakdown.warnings.map((warning) => (
                <div key={warning} className="warning-text">
                  {warning}
                </div>
              ))}
            </div>
          )}

          <div className="vendor-totals">
            <div>
              <span>Vendor item subtotal</span>
              <strong>{formatMoney(group.itemSubtotal)}</strong>
            </div>
            <div>
              <span>Fulfillment cost</span>
              <strong>{formatMoney(group.fulfillmentCost)}</strong>
            </div>
            {group.breakdown?.returnable_deposits ? (
              <div>
                <span>Returnable deposits</span>
                <strong>{formatMoney(group.breakdown.returnable_deposits)}</strong>
              </div>
            ) : null}
            <div>
              <span>Due today incl. deposits</span>
              <strong>
                {formatMoney(getVendorTotal(group) + (group.breakdown?.returnable_deposits ?? 0))}
              </strong>
            </div>
            <div className="vendor-total">
              <span>Estimated vendor cost</span>
              <strong>{formatMoney(getVendorTotal(group))}</strong>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function responseAsPlan(result: OptimizeResponse): DisplayPlan {
  return {
    selected_items: result.selected_items,
    delivery_charges: result.delivery_charges,
    handling_charges: result.handling_charges,
    vendor_breakdowns: result.vendor_breakdowns,
    item_cost_total: result.item_cost_total,
    delivery_cost_total: result.delivery_cost_total,
    handling_fee_total: result.handling_fee_total,
    regular_product_subtotal: result.regular_product_subtotal,
    product_subtotal: result.product_subtotal,
    subscription_savings: result.subscription_savings,
    fulfillment_cost_total: result.fulfillment_cost_total,
    returnable_deposits: result.returnable_deposits,
    total_estimated_cost: result.total_estimated_cost,
    total_due_today: result.total_due_today,
    warnings: result.warnings,
    notes: result.notes,
  };
}

export default function BestCart({
  result,
  excludedVendorIds,
  onExcludeVendor,
  onIncludeVendor,
  onRequireVendor,
  onExcludeProduct,
  onTryVendorMix,
}: Props) {
  const [expandedRank, setExpandedRank] = useState<number | null>(null);

  return (
    <div className="card">
      <h2>Best Cart</h2>

      <VendorBreakdown
        plan={responseAsPlan(result)}
        excludedVendorIds={excludedVendorIds}
        onExcludeVendor={onExcludeVendor}
        onIncludeVendor={onIncludeVendor}
        onRequireVendor={onRequireVendor}
        onExcludeProduct={onExcludeProduct}
      />

      {result.warnings.length > 0 && (
        <ul className="vendor-notes">
          {result.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}

      {result.unavailable_items.length > 0 && (
        <div className="section">
          <h3>Unavailable</h3>
          <ul>
            {result.unavailable_items.map((u, i) => (
              <li key={i}>
                {u.canonical_product} - {u.requested_quantity} {u.unit} ({labelUnavailableReason(u.reason)})
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.alternative_carts.length > 0 && (
        <div className="section">
          <h3>Alternative Carts</h3>
          <div className="alternative-list">
            {result.alternative_carts.map((cart: CartPlan) => {
              const expanded = expandedRank === cart.rank;
              return (
                <div key={cart.rank} className="alternative-card">
                  <button
                    type="button"
                    className="alternative-toggle"
                    onClick={() => setExpandedRank(expanded ? null : cart.rank)}
                    aria-expanded={expanded}
                  >
                    <span>
                      <strong>Alternative Cart #{cart.rank}</strong> -{" "}
                      {formatMoney(cart.total_estimated_cost)}
                    </span>
                    <span>{expanded ? "Hide preview" : "Preview alternative cart"}</span>
                  </button>
                  <div className="alternative-summary">
                    <span>Vendors: {cart.vendor_ids.join(", ")}</span>
                    <span>Items: {formatMoney(cart.item_cost_total)}</span>
                    <span>Fulfillment: {formatMoney(cart.fulfillment_cost_total)}</span>
                    <span>Deposits: {formatMoney(cart.returnable_deposits)}</span>
                  </div>
                  {cart.reason_lost && (
                    <div className="alternative-reason">
                      Reason: {cart.reason_lost}
                    </div>
                  )}
                  {expanded && (
                    <div className="alternative-preview">
                      {onTryVendorMix && (
                        <button
                          type="button"
                          className="secondary-button"
                          onClick={() => onTryVendorMix(cart.vendor_ids)}
                        >
                          Try this vendor mix
                        </button>
                      )}
                      <VendorBreakdown plan={cart} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="totals">
        <div>Regular product subtotal: {formatMoney(result.regular_product_subtotal)}</div>
        <div>Subscription savings: {formatMoney(result.subscription_savings)}</div>
        <div>Product subtotal: {formatMoney(result.product_subtotal)}</div>
        <div>Fulfillment cost: {formatMoney(result.fulfillment_cost_total)}</div>
        <div>Estimated cost excl. deposits: {formatMoney(result.total_estimated_cost)}</div>
        <div>Returnable deposits due today: {formatMoney(result.returnable_deposits)}</div>
        <div className="grand-total">
          Due today incl. deposits: {formatMoney(result.total_due_today)}
        </div>
      </div>
    </div>
  );
}
