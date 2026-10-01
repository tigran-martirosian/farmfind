export type CanonicalProduct =
  | "cow_milk"
  | "sheep_milk"
  | "cream"
  | "butter"
  | "cheese"
  | "eggs";

export type NormalizedUnit = "gallon" | "pint" | "lb" | "count";
export type FulfillmentMode =
  | "best"
  | "mixed"
  | "delivery_only"
  | "pickup_only"
  | "ups_only"
  | "farm_truck_only"
  | "no_pickup";

export interface Vendor {
  id: string;
  name: string;
  pickup_address: string;
  delivery_available: boolean;
  delivery_fee: number;
  handling_fee: number;
  packing_fee: number;
  cooler_fee: number;
  service_fee: number;
  minimum_order: number;
  pickup_dropoff_available: boolean | null;
  ups_shipping_available: boolean | null;
  farm_truck_delivery_available: boolean;
  source_type: "manual" | "imported" | "mock";
  website_url: string | null;
  login_required: boolean;
  supports_authenticated_fetch: boolean;
  last_fetched_at: string | null;
  auth_state_label: string | null;
  fetch_notes: string | null;
  notes: string;
}

export type CredentialStatus = "configured" | "not_configured" | "needs_update";
export type ConnectionStatus =
  | "not_connected"
  | "connected"
  | "expired"
  | "action_required"
  | "failed";
export type ConnectionActionRequired =
  | "login_required"
  | "manual_login_required"
  | "captcha_required"
  | "two_factor_required"
  | "credential_update_required"
  | "reconnect_required"
  | "pricing_locked"
  | "store_access_required"
  | "location_or_order_cycle_required"
  | "unsupported_layout"
  | "parser_zero_candidates"
  | "unsupported_vendor_parser"
  | "fetch_failed"
  | "connected_account_only"
  | "cart_unavailable"
  | "failed";

export interface VendorConnection {
  vendor_id: string;
  auth_state_label: string;
  session_mode: string;
  browser_channel: string;
  credential_status: CredentialStatus;
  credential_username_hint: string | null;
  credential_storage_provider: string | null;
  connection_status: ConnectionStatus;
  last_connected_at: string | null;
  last_checked_at: string | null;
  last_successful_fetch_at: string | null;
  action_required: ConnectionActionRequired | null;
  notes: string | null;
  error_message: string | null;
  login_url: string | null;
  account_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface LoginSessionResult {
  vendor_id: string;
  status:
    | "launched"
    | "already_open"
    | "already_connected"
    | "verification_pending"
    | "connected"
    | "action_required"
    | "failed";
  action_required: ConnectionActionRequired | null;
  recommended_next_action: string | null;
  profile_label: string | null;
  manual_command: string | null;
  warnings: string[];
}

export interface VendorConnectionMetadata {
  vendor_id: string;
  display_name: string | null;
  auth_strategy: string;
  credentials_configured: boolean;
  credential_status: CredentialStatus;
  connection_status: ConnectionStatus;
  action_required: ConnectionActionRequired | null;
  last_connected: string | null;
  last_checked: string | null;
  last_fetch: string | null;
  latest_fetch_status: string | null;
  latest_fetch_reason: string | null;
  session_verified: boolean;
  store_fetch_ready: boolean;
  parser_ready: boolean;
  import_candidates_created: number;
  fetch_ready: boolean;
  pages_successful: number;
  variant_count_total: number;
  candidates_created_total: number;
  empty_candidate_pages: string[];
  quote_configured: boolean;
  latest_quote_status: string | null;
  latest_quote_reason: string | null;
  recommended_next_action: string | null;
  profile_label: string | null;
  notes: string | null;
  error_message: string | null;
}

export interface FetchMetadata {
  vendor_id: string;
  page_id: string;
  url: string;
  fetched_at: string;
  status: string;
  action_required: ConnectionActionRequired | null;
  connection_status: ConnectionStatus | null;
  capture_method: string;
  final_url: string | null;
  html_path: string | null;
  screenshot_path: string | null;
  metadata_path: string | null;
  content_hash: string | null;
  page_title: string | null;
  variant_count: number | null;
  classification_reason: string | null;
  error_message: string | null;
  warnings: string[];
}

export interface FetchServiceResult {
  status: string;
  vendor_id: string;
  page_id: string | null;
  connection_status: ConnectionStatus | null;
  action_required: ConnectionActionRequired | null;
  html_path: string | null;
  screenshot_path: string | null;
  metadata_path: string | null;
  content_hash: string | null;
  warnings: string[];
  error_message: string | null;
}

export interface ImportedAttribute {
  label: string;
  value: string;
}

export interface ShippingOption {
  name: string;
  price: number | null;
  selected: boolean;
}

export type ShippingQuoteStatus =
  | "quote_available"
  | "login_required"
  | "captcha_required"
  | "pricing_locked"
  | "store_access_required"
  | "location_or_order_cycle_required"
  | "cart_unavailable"
  | "checkout_step_required"
  | "quote_unavailable"
  | "unsafe_checkout_boundary"
  | "failed";

export interface ShippingQuote {
  vendor_id: string;
  shipping_quote_source: string;
  shipping_quote_status: ShippingQuoteStatus;
  account_authenticated: boolean;
  fetch_ready: boolean;
  action_required: string | null;
  shipping_options: ShippingOption[];
  selected_shipping_method: string | null;
  shipping_price: number | null;
  cooler_fee: number | null;
  handling_fee: number | null;
  cart_subtotal: number | null;
  cart_tax: number | null;
  cart_quote_total: number | null;
  order_cycle_note: string | null;
  unrelated_cart_items: boolean;
  quote_captured_at: string | null;
  quote_artifact_path: string | null;
  quote_warnings: string[];
  recommended_next_action: string | null;
}

export interface VendorFetchSummary {
  vendor_id: string;
  connection_status: string | null;
  action_required: ConnectionActionRequired | string | null;
  account_authenticated: boolean;
  fetch_ready: boolean;
  pages_attempted: number;
  pages_successful: number;
  pages_action_required: number;
  pages_failed: number;
  variant_count_total: number;
  candidates_created_total: number;
  candidates_by_page: Record<string, number>;
  empty_candidate_pages: string[];
  pricing_locked_pages: string[];
  artifact_paths: Record<string, Record<string, string | null>>;
  warnings: string[];
  recommended_next_action: string | null;
}

export type FetchJobState = "queued" | "running" | "success" | "failed" | "action_required";

export interface FetchJobStatus {
  job_id: string;
  vendor_id: string;
  status: FetchJobState;
  current_step: string;
  source_pages_attempted: number;
  source_pages_done: number;
  product_cards_found: number;
  detail_links_found: number;
  detail_pages_fetched: number;
  option_groups_found: number;
  variants_extracted: number;
  candidates_written: number;
  excluded_count: number;
  incomplete_discoveries: string[];
  started_at: string | null;
  finished_at: string | null;
  message: string;
  error_code: string | null;
  summary: VendorFetchSummary | null;
}

export interface ImportedProductCandidate {
  import_id: string;
  vendor_id: string;
  source_page_id: string | null;
  review_status: "pending" | "approved" | "rejected";
  product_type: CanonicalProduct | null;
  name: string;
  detected_price: number | null;
  detected_package_size: number | null;
  detected_unit: string | null;
  packaging: "glass" | "plastic" | null;
  storage_state: string;
  stock_status: "in_stock" | "out_of_stock" | "unknown";
  stock_quantity: number | null;
  parser_confidence: number | null;
  raw_text: string;
  source_url: string | null;
  screenshot_path: string | null;
  needs_review: boolean;
  missing_fields: string[];
  warnings: string[];
  attributes: ImportedAttribute[];
  display_name: string | null;
  display_tags: string[];
  species: string | null;
  relevant_type: string | null;
  display_product_type: string | null;
  package_display: string | null;
  processing_tags: string[];
  freshness_status: string | null;
  salt_status: string | null;
  eligibility_status: "eligible" | "excluded" | "needs_review";
  eligibility_reason: string | null;
  exclusion_reason: string | null;
  review_notes: string[];
  evidence_checkpoints: string[];
  validation_reasons: string[];
}

export interface NormalizedProduct {
  id: string;
  vendor_id: string;
  product_name: string;
  canonical_product: CanonicalProduct;
  price: number;
  package_quantity: number;
  package_unit: string;
  in_stock: boolean;
  is_unsalted: boolean;
  delivery_available: boolean;
  pickup_available: boolean;
  pickup_dropoff_available: boolean | null;
  ups_shipping_available: boolean | null;
  farm_truck_delivery_available: boolean | null;
  subscription_available: boolean;
  subscription_discount_percent: number;
  subscription_price: number | null;
  packaging: string | null;
  container_deposit: number;
  container_returnable: boolean;
  storage_state: string;
  bulk_deal: boolean;
  source_type: "manual" | "imported" | "mock";
  source_vendor_page: string | null;
  source_url: string | null;
  imported_at: string | null;
  parser_confidence: number | null;
  needs_review: boolean;
  raw_text: string | null;
  missing_fields: string[];
  notes: string;
  normalized_quantity: number | null;
  normalized_unit: NormalizedUnit;
  unit_price: number | null;
  excluded: boolean;
  exclusion_reason: string | null;
  display_name: string | null;
  display_tags: string[];
}

export interface OptimizeItem {
  canonical_product?: CanonicalProduct;
  product_type: CanonicalProduct;
  quantity: number;
  unit: string;
  packaging_preference?: string;
  storage_preference?: string;
  salt_preference?: string;
}

export type CatalogMode = "imported_only" | "demo_only" | "imported_plus_demo";

export interface OptimizeRequest {
  items: OptimizeItem[];
  include_delivery: boolean;
  allow_overbuy_percent: number;
  excluded_vendor_ids: string[];
  excluded_product_ids: string[];
  required_vendor_ids: string[];
  max_vendors: number | null;
  avoid_pickup_required: boolean;
  fulfillment_mode: FulfillmentMode;
  allowed_fulfillment_methods: string[];
  enforce_minimum_order: boolean;
  cost_per_mile: number;
  vehicle_mpg: number | null;
  fuel_price_per_gallon: number | null;
  use_round_trip_pickup_cost: boolean;
  max_pickup_distance_miles: number | null;
  use_subscription_pricing: boolean;
  packaging_preference: string;
  include_returnable_deposits_in_total: boolean;
  storage_preference: string;
  salt_preference: string;
  catalog_mode: CatalogMode;
  /** @deprecated Use catalog_mode instead. */
  use_imported_products?: boolean;
}

export type OptimizerConstraints = Pick<
  OptimizeRequest,
  | "excluded_vendor_ids"
  | "excluded_product_ids"
  | "required_vendor_ids"
  | "max_vendors"
  | "avoid_pickup_required"
  | "fulfillment_mode"
  | "allowed_fulfillment_methods"
  | "enforce_minimum_order"
  | "cost_per_mile"
  | "vehicle_mpg"
  | "fuel_price_per_gallon"
  | "use_round_trip_pickup_cost"
  | "max_pickup_distance_miles"
  | "use_subscription_pricing"
  | "packaging_preference"
  | "include_returnable_deposits_in_total"
  | "storage_preference"
  | "salt_preference"
  | "catalog_mode"
>;

export interface CartLine {
  product_id: string;
  product_name: string;
  package_count: number;
  package_size: number;
  package_unit: string;
  line_quantity: number;
  bulk_deal: boolean;
  item_cost: number;
}

export interface SelectedPackage {
  product_id: string;
  product_name: string;
  package_count: number;
  package_quantity: number;
  package_unit: NormalizedUnit;
  listed_package_quantity: number | null;
  listed_package_unit: string | null;
  price_each: number;
  regular_price_each: number;
  effective_price_each: number;
  subscription_applied: boolean;
  subscription_savings: number;
  line_total: number;
  packaging: string | null;
  container_deposit: number;
  container_returnable: boolean;
  storage_state: string;
  stock_status: "in_stock" | "out_of_stock" | "unknown";
  pickup_dropoff_available: boolean;
  ups_shipping_available: boolean;
  farm_truck_delivery_available: boolean;
}

export interface SelectedItem {
  canonical_product: CanonicalProduct;
  vendor_id: string;
  vendor: string;
  vendor_name: string;
  packages: SelectedPackage[];
  requested_quantity: number;
  normalized_unit: NormalizedUnit;
  lines: CartLine[];
  purchased_quantity: number;
  total_quantity_purchased: number;
  overbuy_quantity: number;
  overbuy_amount: number;
  overbuy_percent: number;
  regular_item_total: number;
  subscription_savings: number;
  returnable_deposits: number;
  item_total: number;
  item_cost: number;
  pickup_required: boolean;
  packaging_preference: string;
  storage_preference: string;
  salt_preference: string;
  notes: string[];
}

export interface UnavailableItem {
  canonical_product: CanonicalProduct;
  requested_quantity: number;
  unit: string;
  reason: string;
}

export interface DeliveryCharge {
  vendor_id: string;
  vendor_name: string;
  fee: number;
}

export interface HandlingCharge {
  vendor_id: string;
  vendor_name: string;
  fee: number;
}

export interface VendorBreakdown {
  vendor_id: string;
  vendor_name: string;
  fulfillment_method: string;
  item_subtotal: number;
  fulfillment_cost: number;
  vendor_total: number;
  package_count: number;
  cold_item_count: number;
  quote_source:
    | "rule_based_estimate"
    | "default_vendor_estimate"
    | "captured_checkout_quote"
    | "unavailable"
    | "not_configured";
  quote_status:
    | "estimated"
    | "captured"
    | "unavailable"
    | "action_required"
    | "stale";
  minimum_order: number;
  minimum_met: boolean;
  amount_short: number;
  returnable_deposits: number;
  warnings: string[];
  pickup_location_id: string | null;
  pickup_location_name: string | null;
  pickup_address: string | null;
  pickup_distance_miles: number | null;
  pickup_travel_cost: number;
  pickup_fee: number;
  pickup_handling_fee: number;
  pickup_vendor_fees: number;
  ups_shipping_fee: number;
  ups_packing_fee: number;
  ups_cooler_fee: number;
  per_package_fee: number;
  per_cold_item_fee: number;
  farm_truck_delivery_fee: number;
}

export interface CatalogDiagnostics {
  approved_loaded_count: number;
  optimizer_ready_count: number;
  products_by_vendor: Record<string, number>;
  products_by_type: Record<string, number>;
  excluded_by_reason: Record<string, number>;
}

export interface CartPlan {
  rank: number;
  selected_items: SelectedItem[];
  delivery_charges: DeliveryCharge[];
  handling_charges: HandlingCharge[];
  vendor_breakdowns: VendorBreakdown[];
  item_cost_total: number;
  delivery_cost_total: number;
  handling_fee_total: number;
  regular_product_subtotal: number;
  product_subtotal: number;
  subscription_savings: number;
  fulfillment_cost_total: number;
  returnable_deposits: number;
  total_estimated_cost: number;
  total_due_today: number;
  vendor_ids: string[];
  reason_lost: string | null;
  warnings: string[];
  pickup_required: boolean;
  notes: string[];
}

export interface OptimizeResponse {
  selected_items: SelectedItem[];
  unavailable_items: UnavailableItem[];
  delivery_charges: DeliveryCharge[];
  handling_charges: HandlingCharge[];
  vendor_breakdowns: VendorBreakdown[];
  item_cost_total: number;
  delivery_cost_total: number;
  handling_fee_total: number;
  regular_product_subtotal: number;
  product_subtotal: number;
  subscription_savings: number;
  fulfillment_cost_total: number;
  returnable_deposits: number;
  total_estimated_cost: number;
  total_due_today: number;
  alternative_carts: CartPlan[];
  warnings: string[];
  notes: string[];
  catalog_diagnostics: CatalogDiagnostics;
}

export type FulfillmentMethod = "pickup_dropoff" | "ups_shipping" | "farm_truck_delivery";

export interface RequestedItem {
  product: CanonicalProduct;
  quantity: number;
  unit: string;
}

export interface ShoppingRequest {
  items: RequestedItem[];
  location: string | null;
  fulfillment: "any" | "pickup" | "delivery";
  max_pickup_miles: number | null;
  max_vendors: number | null;
}

export interface FulfillmentChoice {
  vendor_id: string;
  vendor_name: string;
  method: FulfillmentMethod;
  cost: number;
  pickup_location_name: string | null;
  distance_miles: number | null;
}

export interface ChatRecommendation {
  feasible: boolean;
  total: number;
  fulfillment: FulfillmentChoice[];
  cart: OptimizeResponse | null;
  problem: string | null;
}

export interface AgentTraceStep {
  timestamp: string;
  run_id: string;
  step: number;
  sender: string;
  target: string;
  action: string;
  input_value: string | null;
  result_status: string;
  result_summary: string;
  error_message: string | null;
}

export interface ChatResponse {
  conversation_id: string;
  reply: string;
  follow_up_question: string | null;
  recommendation: ChatRecommendation | null;
  request: ShoppingRequest;
  trace: AgentTraceStep[];
  coordinator: string;
}
