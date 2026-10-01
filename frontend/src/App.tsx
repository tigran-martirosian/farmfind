import { useEffect, useRef, useState } from "react";
import {
  checkVendorConnection,
  approveImportCandidate,
  fetchFetchMetadata,
  fetchImportCandidates,
  fetchVendorFetchJob,
  fetchVendorConnectionMetadata,
  fetchProducts,
  fetchShippingQuote,
  fetchVendorConnections,
  fetchVendors,
  openVendorLoginSession,
  optimizeCart,
  rejectImportCandidate,
  startVendorFetchJob,
} from "./api";
import BestCart from "./components/BestCart";
import ChatPanel from "./components/ChatPanel";
import ImportReview from "./components/ImportReview";
import OptimizeForm from "./components/OptimizeForm";
import ProductTable from "./components/ProductTable";
import VendorConnections from "./components/VendorConnections";
import type {
  FetchMetadata,
  FetchJobStatus,
  ImportedProductCandidate,
  LoginSessionResult,
  NormalizedProduct,
  OptimizerConstraints,
  OptimizeRequest,
  OptimizeResponse,
  ShippingQuote,
  Vendor,
  VendorConnection,
  VendorConnectionMetadata,
  VendorFetchSummary,
} from "./types";

// Honest shipping-quote message. Never presents default/estimated fees as a real
// captured quote; only quote_available reports real numbers.
// Product Comparison is hidden from normal navigation until candidate data is
// clean; opt in with VITE_SHOW_PRODUCT_COMPARISON=true for debugging.
const SHOW_PRODUCT_COMPARISON = import.meta.env.VITE_SHOW_PRODUCT_COMPARISON === "true";

function shippingQuoteMessage(summary: ShippingQuote): string {
  const money = (value: number | null) => (value == null ? "—" : `$${value.toFixed(2)}`);
  if (summary.shipping_quote_status === "quote_available") {
    const method = summary.selected_shipping_method ?? "shipping";
    return `Shipping quote available: ${method} ${money(summary.shipping_price)}, cooler fee ${money(summary.cooler_fee)}, total ${money(summary.cart_quote_total)}.`;
  }
  if (summary.shipping_quote_status === "unsafe_checkout_boundary") {
    return "Stopped before checkout confirmation for safety. No order was placed.";
  }
  if (summary.shipping_quote_status === "cart_unavailable" || summary.shipping_quote_status === "failed") {
    return "Quote unavailable because the vendor cart could not be safely loaded/modified.";
  }
  if (summary.shipping_quote_status === "location_or_order_cycle_required") {
    return "Shipping quote unavailable: location/order cycle required.";
  }
  return `Shipping quote unavailable: ${summary.action_required ?? summary.shipping_quote_status}.`;
}

// Build an honest, human-readable message from a fetch summary. It never implies
// products were imported when candidates_created_total is 0.
function fetchSummaryMessage(summary: VendorFetchSummary): string {
  if (summary.candidates_created_total > 0) {
    return `Fetched and created ${summary.candidates_created_total} import candidates. Review and approve them in Import Review.`;
  }
  if (!summary.fetch_ready) {
    const reason = summary.action_required ?? "not fetch-ready";
    if (summary.account_authenticated) {
      return `Vendor connected but not fetch-ready: ${reason}.`;
    }
    return `Vendor not fetch-ready: ${reason}.`;
  }
  const reason =
    (summary.warnings.length ? summary.warnings.join("; ") : summary.recommended_next_action) ??
    "no candidates could be created.";
  return `Fetched page artifacts, but no import candidates were created: ${reason}`;
}

const EMPTY_CONSTRAINTS: OptimizerConstraints = {
  excluded_vendor_ids: [],
  excluded_product_ids: [],
  required_vendor_ids: [],
  max_vendors: null,
  avoid_pickup_required: false,
  fulfillment_mode: "best",
  allowed_fulfillment_methods: [
    "pickup_dropoff",
    "ups_shipping",
    "farm_truck_delivery",
  ],
  enforce_minimum_order: true,
  cost_per_mile: 0.67,
  vehicle_mpg: 25,
  fuel_price_per_gallon: 4,
  use_round_trip_pickup_cost: true,
  max_pickup_distance_miles: null,
  use_subscription_pricing: false,
  catalog_mode: "imported_only",
  packaging_preference: "any",
  include_returnable_deposits_in_total: false,
  storage_preference: "any",
  salt_preference: "any",
};

function normalizeConstraints(
  constraints: OptimizerConstraints
): OptimizerConstraints {
  if (constraints.fulfillment_mode === "pickup_only") {
    return { ...constraints, avoid_pickup_required: false };
  }
  if (constraints.fulfillment_mode !== "best" && constraints.fulfillment_mode !== "mixed") {
    return { ...constraints, avoid_pickup_required: false };
  }
  return constraints;
}

export default function App() {
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [products, setProducts] = useState<NormalizedProduct[]>([]);
  const [connections, setConnections] = useState<VendorConnection[]>([]);
  const [fetches, setFetches] = useState<FetchMetadata[]>([]);
  const [importCandidates, setImportCandidates] = useState<ImportedProductCandidate[]>([]);
  const [result, setResult] = useState<OptimizeResponse | null>(null);
  const [lastRequest, setLastRequest] = useState<OptimizeRequest | null>(null);
  const [constraints, setConstraints] =
    useState<OptimizerConstraints>(EMPTY_CONSTRAINTS);
  const constraintsRef = useRef<OptimizerConstraints>(EMPTY_CONSTRAINTS);
  const [loading, setLoading] = useState(false);
  const [connectionBusyVendorId, setConnectionBusyVendorId] = useState<string | null>(null);
  const [fetchJobs, setFetchJobs] = useState<Record<string, FetchJobStatus>>({});
  const [busyImportId, setBusyImportId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fetchNotice, setFetchNotice] = useState<string | null>(null);
  const [showExcludedDiagnostics, setShowExcludedDiagnostics] = useState(false);
  const [connectionMetadata, setConnectionMetadata] = useState<Record<string, VendorConnectionMetadata>>({});
  const [shippingQuotes, setShippingQuotes] = useState<Record<string, ShippingQuote>>({});
  const [loginSessions, setLoginSessions] = useState<Record<string, LoginSessionResult>>({});
  const [undoNotice, setUndoNotice] = useState<{
    message: string;
    undo: () => void;
  } | null>(null);

  useEffect(() => {
    Promise.all([
      fetchVendors(constraints.catalog_mode),
      fetchProducts(constraints.catalog_mode),
      fetchVendorConnections(),
      fetchFetchMetadata(),
      fetchImportCandidates(),
    ])
      .then(([v, p, c, f, imports]) => {
        setVendors(v);
        setProducts(p);
        setConnections(c);
        setFetches(f);
        setImportCandidates(imports);
      })
      .catch((e) => setError(String(e)));
  }, [constraints.catalog_mode]);

  async function refreshConnections() {
    const [nextConnections, nextFetches] = await Promise.all([
      fetchVendorConnections(),
      fetchFetchMetadata(),
    ]);
    setConnections(nextConnections);
    setFetches(nextFetches);
  }

  async function refreshImportCandidates() {
    setImportCandidates(await fetchImportCandidates(showExcludedDiagnostics));
  }

  async function handleToggleExcluded(next: boolean) {
    setShowExcludedDiagnostics(next);
    setImportCandidates(await fetchImportCandidates(next));
  }

  async function handleOptimize(request: OptimizeRequest) {
    setLastRequest(request);
    setLoading(true);
    setError(null);
    try {
      setResult(await optimizeCart(request));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  function requestWithConstraints(nextConstraints: OptimizerConstraints) {
    if (!lastRequest) return null;
    return { ...lastRequest, ...nextConstraints };
  }

  function updateConstraints(
    updates:
      | Partial<OptimizerConstraints>
      | ((current: OptimizerConstraints) => Partial<OptimizerConstraints>)
  ) {
    const current = constraintsRef.current;
    const patch = typeof updates === "function" ? updates(current) : updates;
    const merged = { ...current, ...patch };
    const next = normalizeConstraints(
      patch.avoid_pickup_required && current.fulfillment_mode === "pickup_only"
        ? { ...merged, fulfillment_mode: "best" }
        : merged
    );
    constraintsRef.current = next;
    setConstraints(next);
    const nextRequest = requestWithConstraints(next);
    if (nextRequest) {
      void handleOptimize(nextRequest);
    }
  }

  function resetFilters() {
    setUndoNotice(null);
    constraintsRef.current = EMPTY_CONSTRAINTS;
    setConstraints(EMPTY_CONSTRAINTS);
    const nextRequest = requestWithConstraints(EMPTY_CONSTRAINTS);
    if (nextRequest) {
      void handleOptimize(nextRequest);
    }
  }

  function excludeVendor(vendorId: string) {
    updateConstraints((current) => ({
      excluded_vendor_ids: Array.from(
        new Set([...current.excluded_vendor_ids, vendorId])
      ),
      required_vendor_ids: current.required_vendor_ids.filter((id) => id !== vendorId),
    }));
    setUndoNotice({
      message: `Excluded ${vendorName(vendorId)}`,
      undo: () => includeVendor(vendorId),
    });
  }

  function includeVendor(vendorId: string) {
    updateConstraints((current) => ({
      excluded_vendor_ids: current.excluded_vendor_ids.filter((id) => id !== vendorId),
    }));
    setUndoNotice(null);
  }

  function excludeProduct(productId: string) {
    updateConstraints((current) => ({
      excluded_product_ids: Array.from(
        new Set([...current.excluded_product_ids, productId])
      ),
    }));
    setUndoNotice({
      message: `Excluded ${productName(productId)}`,
      undo: () => includeProduct(productId),
    });
  }

  function includeProduct(productId: string) {
    updateConstraints((current) => ({
      excluded_product_ids: current.excluded_product_ids.filter((id) => id !== productId),
    }));
    setUndoNotice(null);
  }

  function removeFilter(key: string) {
    if (key.startsWith("excluded_vendor:")) {
      const id = key.replace("excluded_vendor:", "");
      updateConstraints({
        excluded_vendor_ids: constraints.excluded_vendor_ids.filter((v) => v !== id),
      });
    } else if (key.startsWith("excluded_product:")) {
      const id = key.replace("excluded_product:", "");
      updateConstraints({
        excluded_product_ids: constraints.excluded_product_ids.filter((p) => p !== id),
      });
    } else if (key.startsWith("required_vendor:")) {
      const id = key.replace("required_vendor:", "");
      updateConstraints({
        required_vendor_ids: constraints.required_vendor_ids.filter((v) => v !== id),
      });
    } else if (key === "max_vendors") {
      updateConstraints({ max_vendors: null });
    } else if (key === "avoid_pickup_required") {
      updateConstraints({ avoid_pickup_required: false });
    } else if (key === "fulfillment_mode") {
      updateConstraints({ fulfillment_mode: "best" });
    } else if (key === "use_subscription_pricing") {
      updateConstraints({ use_subscription_pricing: false });
    } else if (key === "catalog_mode") {
      updateConstraints({ catalog_mode: "imported_only" });
    } else if (key === "packaging_preference") {
      updateConstraints({ packaging_preference: "any" });
    } else if (key === "include_returnable_deposits_in_total") {
      updateConstraints({ include_returnable_deposits_in_total: false });
    } else if (key === "storage_preference") {
      updateConstraints({ storage_preference: "any" });
    } else if (key === "salt_preference") {
      updateConstraints({ salt_preference: "any" });
    }
  }

  function vendorName(id: string) {
    return vendors.find((vendor) => vendor.id === id)?.name ?? id;
  }

  function productName(id: string) {
    return products.find((product) => product.id === id)?.product_name ?? id;
  }

  async function handleCheckConnection(vendorId: string) {
    setConnectionBusyVendorId(vendorId);
    setError(null);
    try {
      await checkVendorConnection(vendorId);
      await refreshConnections();
    } catch (e) {
      setError(String(e));
    } finally {
      setConnectionBusyVendorId(null);
    }
  }

  async function handleFetchVendor(vendorId: string) {
    setConnectionBusyVendorId(vendorId);
    setError(null);
    setFetchNotice(null);
    try {
      const started = await startVendorFetchJob(vendorId);
      setFetchJobs((current) => ({ ...current, [vendorId]: started }));
      setFetchNotice(`${vendorName(vendorId)}: ${started.message}`);
      let latest = started;
      while (latest.status === "queued" || latest.status === "running") {
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
        latest = await fetchVendorFetchJob(vendorId, started.job_id);
        setFetchJobs((current) => ({ ...current, [vendorId]: latest }));
      }
      await Promise.all([refreshConnections(), refreshImportCandidates()]);
      if (latest.summary) {
        setFetchNotice(`${vendorName(vendorId)}: ${fetchSummaryMessage(latest.summary)}`);
      } else {
        setFetchNotice(`${vendorName(vendorId)}: ${latest.message}`);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setConnectionBusyVendorId(null);
    }
  }

  async function handleShippingQuote(vendorId: string) {
    setConnectionBusyVendorId(vendorId);
    setError(null);
    setFetchNotice(null);
    try {
      const quote = await fetchShippingQuote(vendorId);
      setShippingQuotes((current) => ({ ...current, [vendorId]: quote }));
      setFetchNotice(`${vendorName(vendorId)}: ${shippingQuoteMessage(quote)}`);
    } catch (e) {
      setError(String(e));
    } finally {
      setConnectionBusyVendorId(null);
    }
  }

  async function handleViewLatestFetchMetadata(vendorId: string) {
    setConnectionBusyVendorId(vendorId);
    setError(null);
    try {
      const [metadata, vendorFetches] = await Promise.all([
        fetchVendorConnectionMetadata(vendorId),
        fetchFetchMetadata(vendorId),
      ]);
      setConnectionMetadata((current) => ({ ...current, [vendorId]: metadata }));
      setFetches((current) => [
        ...vendorFetches,
        ...current.filter((item) => item.vendor_id !== vendorId),
      ]);
    } catch (e) {
      setError(String(e));
    } finally {
      setConnectionBusyVendorId(null);
    }
  }

  async function handleOpenLoginSession(vendorId: string) {
    setConnectionBusyVendorId(vendorId);
    setError(null);
    setFetchNotice(null);
    try {
      const session = await openVendorLoginSession(vendorId);
      setLoginSessions((current) => ({ ...current, [vendorId]: session }));
      await refreshConnections();
      setFetchNotice(
        `${vendorName(vendorId)}: ${session.recommended_next_action ?? session.status.replace(/_/g, " ")}`
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setConnectionBusyVendorId(null);
    }
  }

  async function handleApproveImport(importId: string) {
    setBusyImportId(importId);
    setError(null);
    try {
      await approveImportCandidate(importId);
      // Approved candidate is now staged: refresh candidates AND the product
      // comparison so the new imported_only product appears.
      await Promise.all([
        refreshImportCandidates(),
        fetchProducts(constraints.catalog_mode).then(setProducts),
      ]);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyImportId(null);
    }
  }

  async function handleRejectImport(importId: string) {
    setBusyImportId(importId);
    setError(null);
    try {
      await rejectImportCandidate(importId);
      await refreshImportCandidates();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyImportId(null);
    }
  }

  const activeFilterLabels = [
    ...constraints.excluded_vendor_ids.map((id) => ({
      key: `excluded_vendor:${id}`,
      label: `Excluded farm: ${vendorName(id)}`,
    })),
    ...constraints.excluded_product_ids.map((id) => ({
      key: `excluded_product:${id}`,
      label: `Excluded product: ${productName(id)}`,
    })),
    ...constraints.required_vendor_ids.map((id) => ({
      key: `required_vendor:${id}`,
      label: `Forced farm: ${vendorName(id)}`,
    })),
    ...(constraints.max_vendors
      ? [{ key: "max_vendors", label: `Max vendors: ${constraints.max_vendors}` }]
      : []),
    ...(constraints.avoid_pickup_required &&
    (constraints.fulfillment_mode === "best" ||
      constraints.fulfillment_mode === "mixed")
      ? [{ key: "avoid_pickup_required", label: "Avoid pickup required" }]
      : []),
    ...(constraints.fulfillment_mode !== "best"
      ? [
          {
            key: "fulfillment_mode",
            label: `Fulfillment: ${constraints.fulfillment_mode.replace(/_/g, " ")}`,
          },
        ]
      : []),
    ...(constraints.use_subscription_pricing
      ? [{ key: "use_subscription_pricing", label: "Subscription pricing" }]
      : []),
    ...(constraints.catalog_mode !== "imported_only"
      ? [
          {
            key: "catalog_mode",
            label:
              constraints.catalog_mode === "demo_only"
                ? "Catalog: demo samples only"
                : "Catalog: real + demo debugging",
          },
        ]
      : []),
    ...(constraints.packaging_preference !== "any"
      ? [{ key: "packaging_preference", label: `Packaging: ${constraints.packaging_preference.replace(/_/g, " ")}` }]
      : []),
    ...(constraints.include_returnable_deposits_in_total
      ? [{ key: "include_returnable_deposits_in_total", label: "Deposits in total" }]
      : []),
    ...(constraints.storage_preference !== "any"
      ? [{ key: "storage_preference", label: `Storage: ${constraints.storage_preference.replace(/_/g, " ")}` }]
      : []),
    ...(constraints.salt_preference !== "any"
      ? [{ key: "salt_preference", label: `Salt: ${constraints.salt_preference}` }]
      : []),
  ];

  return (
    <div className="app">
      <header>
        <h1>FarmFind</h1>
        <p className="subtitle">Dairy &amp; Eggs Procurement Optimizer</p>
      </header>

      {error && <div className="error">{error}</div>}
      {fetchNotice && (
        <div className="notice">
          <span>{fetchNotice}</span>
          <button type="button" className="inline-link-button" onClick={() => setFetchNotice(null)}>
            Dismiss
          </button>
        </div>
      )}
      {undoNotice && (
        <div className="notice">
          <span>{undoNotice.message}</span>
          <button type="button" className="inline-link-button" onClick={undoNotice.undo}>
            Undo
          </button>
        </div>
      )}

      <ChatPanel catalogMode={constraints.catalog_mode} />
      <OptimizeForm
        onOptimize={handleOptimize}
        constraints={constraints}
        activeFilterLabels={activeFilterLabels}
        onConstraintsChange={updateConstraints}
        onRemoveFilter={removeFilter}
        onResetFilters={resetFilters}
        loading={loading}
      />
      <VendorConnections
        vendors={vendors}
        connections={connections}
        fetches={fetches}
        busyVendorId={connectionBusyVendorId}
        metadataByVendorId={connectionMetadata}
        fetchJobsByVendorId={fetchJobs}
        shippingQuotesByVendorId={shippingQuotes}
        loginSessionsByVendorId={loginSessions}
        onCheck={handleCheckConnection}
        onFetch={handleFetchVendor}
        onViewMetadata={handleViewLatestFetchMetadata}
        onShippingQuote={handleShippingQuote}
        onOpenLoginSession={handleOpenLoginSession}
      />
      <ImportReview
        vendors={vendors}
        candidates={importCandidates}
        busyImportId={busyImportId}
        onApprove={handleApproveImport}
        onReject={handleRejectImport}
        onRefresh={refreshImportCandidates}
        showExcluded={showExcludedDiagnostics}
        onToggleExcluded={handleToggleExcluded}
      />
      {result && (
        <BestCart
          result={result}
          excludedVendorIds={constraints.excluded_vendor_ids}
          onExcludeVendor={excludeVendor}
          onIncludeVendor={includeVendor}
          onRequireVendor={(vendorId) =>
            updateConstraints({
              required_vendor_ids: Array.from(
                new Set([...constraints.required_vendor_ids, vendorId])
              ),
            })
          }
          onExcludeProduct={excludeProduct}
          onTryVendorMix={(vendorIds) =>
            updateConstraints({ required_vendor_ids: vendorIds })
          }
        />
      )}
      {SHOW_PRODUCT_COMPARISON ? (
        <ProductTable
          products={products}
          vendors={vendors}
          excludedVendorIds={constraints.excluded_vendor_ids}
          excludedProductIds={constraints.excluded_product_ids}
          onExcludeVendor={excludeVendor}
          onIncludeVendor={includeVendor}
          onExcludeProduct={excludeProduct}
          onIncludeProduct={includeProduct}
        />
      ) : (
        <section className="card">
          <h2>Product Comparison</h2>
          <p className="helper-text">
            Product comparison is disabled until candidate data is clean. (Set
            VITE_SHOW_PRODUCT_COMPARISON=true to re-enable for debugging.)
          </p>
        </section>
      )}
    </div>
  );
}
