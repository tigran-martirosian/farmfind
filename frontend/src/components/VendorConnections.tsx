import { Fragment } from "react";
import type {
  FetchMetadata,
  FetchJobStatus,
  LoginSessionResult,
  ShippingQuote,
  Vendor,
  VendorConnection,
  VendorConnectionMetadata,
} from "../types";

interface Props {
  vendors: Vendor[];
  connections: VendorConnection[];
  fetches: FetchMetadata[];
  busyVendorId: string | null;
  metadataByVendorId: Record<string, VendorConnectionMetadata>;
  shippingQuotesByVendorId: Record<string, ShippingQuote>;
  loginSessionsByVendorId: Record<string, LoginSessionResult>;
  fetchJobsByVendorId: Record<string, FetchJobStatus>;
  onCheck: (vendorId: string) => void;
  onFetch: (vendorId: string) => void;
  onViewMetadata: (vendorId: string) => void;
  onShippingQuote: (vendorId: string) => void;
  onOpenLoginSession: (vendorId: string) => void;
}

function formatDate(value: string | null) {
  if (!value) return "Never";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function vendorName(vendors: Vendor[], vendorId: string) {
  return vendors.find((vendor) => vendor.id === vendorId)?.name ?? vendorId.replace(/_/g, " ");
}

function latestFetch(fetches: FetchMetadata[], vendorId: string) {
  return fetches.find((fetch) => fetch.vendor_id === vendorId) ?? null;
}

function isLoginAction(action: string | null) {
  return (
    action === "login_required" ||
    action === "manual_login_required" ||
    action === "reconnect_required" ||
    action === "captcha_required" ||
    action === "two_factor_required"
  );
}

function formatStatus(value: string | null | undefined) {
  return value ? value.replace(/_/g, " ") : "-";
}

function recommendedAction(
  connection: VendorConnection,
  latest: FetchMetadata | null,
  metadata: VendorConnectionMetadata | undefined
) {
  const action = metadata?.action_required ?? connection.action_required ?? latest?.action_required ?? null;
  if (action === "parser_zero_candidates" || action === "unsupported_vendor_parser") return "Parser/import needs work";
  if (action === "store_access_required" || action === "location_or_order_cycle_required" || action === "pricing_locked") return "Store setup required";
  if (metadata?.fetch_ready && (metadata.import_candidates_created > 0 || metadata.parser_ready)) return "Ready";
  if (connection.action_required === "credential_update_required") return "Update credentials";
  if (connection.credential_status !== "configured") return "Set credentials";
  if (canOpenLoginBrowser(connection) && (isLoginAction(action) || connection.connection_status !== "connected")) return "Open login browser";
  if (action === "captcha_required") return "Complete browser challenge";
  if (action === "two_factor_required") return "Complete 2FA in browser";
  if (connection.connection_status !== "connected") return "Verify session";
  if (latest?.status === "success" && (latest.variant_count ?? 0) === 0) return "Parser/import needs work";
  if (!latest || latest.status !== "success") return "Fetch now";
  return "Ready";
}

function quoteSummary(quote: ShippingQuote) {
  if (quote.shipping_quote_status === "quote_available") {
    const price = quote.shipping_price == null ? "price unknown" : `$${quote.shipping_price.toFixed(2)}`;
    return `${formatStatus(quote.shipping_quote_status)}: ${quote.selected_shipping_method ?? "shipping"} ${price}`;
  }
  if (isLoginAction(quote.action_required)) return "Login/session refresh required";
  return formatStatus(quote.action_required ?? quote.shipping_quote_status);
}

function canOpenLoginBrowser(connection: VendorConnection) {
  if (connection.action_required === "credential_update_required") return false;
  return connection.credential_status === "configured" && Boolean(connection.login_url || connection.account_url);
}

export default function VendorConnections({
  vendors,
  connections,
  fetches,
  busyVendorId,
  metadataByVendorId,
  shippingQuotesByVendorId,
  loginSessionsByVendorId,
  fetchJobsByVendorId,
  onCheck,
  onFetch,
  onViewMetadata,
  onShippingQuote,
  onOpenLoginSession,
}: Props) {
  return (
    <section className="card">
      <div className="card-head-row">
        <h2>Vendor Connections</h2>
        <span className="helper-text">Use login refresh for vendor sessions that need a human browser step.</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Vendor</th>
              <th>Credentials</th>
              <th>Connection</th>
              <th>Action</th>
              <th>Last connected</th>
              <th>Last checked</th>
              <th>Last fetch</th>
              <th>Latest fetch</th>
              <th>Next</th>
              <th>Controls</th>
            </tr>
          </thead>
          <tbody>
            {connections.map((connection) => {
              const latest = latestFetch(fetches, connection.vendor_id);
              const fetchJob = fetchJobsByVendorId[connection.vendor_id];
              const fetchRunning = fetchJob?.status === "queued" || fetchJob?.status === "running";
              const busy = busyVendorId === connection.vendor_id || fetchRunning;
              const metadata = metadataByVendorId[connection.vendor_id];
              const quote = shippingQuotesByVendorId[connection.vendor_id];
              const loginSession = loginSessionsByVendorId[connection.vendor_id];
              const showDetails = metadata || quote || loginSession || fetchJob;
              return (
                <Fragment key={connection.vendor_id}>
                  <tr>
                    <td>{vendorName(vendors, connection.vendor_id)}</td>
                    <td>{formatStatus(connection.credential_status)}</td>
                    <td>{formatStatus(connection.action_required ?? connection.connection_status)}</td>
                    <td>{formatStatus(connection.action_required)}</td>
                    <td>{formatDate(connection.last_connected_at)}</td>
                    <td>{formatDate(connection.last_checked_at)}</td>
                    <td>{formatDate(connection.last_successful_fetch_at)}</td>
                    <td>
                      {latest ? (
                        <>
                          <span>{formatStatus(latest.status)}</span>
                          <span className="helper-text"> {formatDate(latest.fetched_at)}</span>
                        </>
                      ) : "-"}
                    </td>
                    <td>{recommendedAction(connection, latest, metadata)}</td>
                    <td>
                      <div className="connection-actions">
                        {canOpenLoginBrowser(connection) && (
                          <button
                            type="button"
                            className="table-action-button"
                            disabled={busy}
                            onClick={() => onOpenLoginSession(connection.vendor_id)}
                          >
                            Open login browser
                          </button>
                        )}
                        <button
                          type="button"
                          className="table-action-button secondary-button"
                          disabled={busy}
                          onClick={() => onCheck(connection.vendor_id)}
                        >
                          Verify session
                        </button>
                        <button
                          type="button"
                          className="table-action-button secondary-button"
                          disabled={busy}
                          onClick={() => onFetch(connection.vendor_id)}
                        >
                          {fetchRunning ? "Fetching..." : "Fetch"}
                        </button>
                        <button
                          type="button"
                          className="table-action-button secondary-button"
                          disabled={busy}
                          title="Safely read the vendor cart shipping/cooler totals. Never places an order."
                          onClick={() => onShippingQuote(connection.vendor_id)}
                        >
                          Shipping quote
                        </button>
                        <button
                          type="button"
                          className="inline-link-button"
                          onClick={() => onViewMetadata(connection.vendor_id)}
                        >
                          Metadata
                        </button>
                      </div>
                    </td>
                  </tr>
                  {showDetails && (
                    <tr className="connection-detail-row">
                      <td colSpan={10}>
                        {fetchJob && (
                          <div className="inline-detail detail-grid">
                            <span><strong>Fetch job:</strong> {formatStatus(fetchJob.status)}</span>
                            <span>{fetchJob.current_step}</span>
                            <span>Pages: {fetchJob.source_pages_done}/{fetchJob.source_pages_attempted}</span>
                            <span>Cards: {fetchJob.product_cards_found}</span>
                            <span>Detail links: {fetchJob.detail_links_found}</span>
                            <span>Details fetched: {fetchJob.detail_pages_fetched}</span>
                            <span>Option groups: {fetchJob.option_groups_found}</span>
                            <span>Variants: {fetchJob.variants_extracted}</span>
                            <span>Candidates: {fetchJob.candidates_written}</span>
                            <span>Excluded: {fetchJob.excluded_count}</span>
                            <span>{fetchJob.message}</span>
                          </div>
                        )}
                        {loginSession && (
                          <div className="inline-detail">
                            <strong>Login session:</strong> {formatStatus(loginSession.status)}
                            {loginSession.profile_label && <> | {loginSession.profile_label}</>}
                            {loginSession.recommended_next_action && <> | {loginSession.recommended_next_action}</>}
                            {loginSession.manual_command && <code>{loginSession.manual_command}</code>}
                          </div>
                        )}
                        {quote && (
                          <div className="inline-detail">
                            <strong>Shipping quote:</strong> {quoteSummary(quote)}
                            {quote.recommended_next_action && <> | {quote.recommended_next_action}</>}
                          </div>
                        )}
                        {metadata && (
                          <div className="inline-detail detail-grid">
                            <span><strong>Metadata:</strong> {metadata.display_name ?? metadata.vendor_id}</span>
                            <span>Auth: {formatStatus(metadata.auth_strategy)}</span>
                            <span>Credentials: {metadata.credentials_configured ? "configured" : formatStatus(metadata.credential_status)}</span>
                            <span>Connection: {formatStatus(metadata.connection_status)}</span>
                            <span>Action: {formatStatus(metadata.action_required)}</span>
                            <span>Session verified: {metadata.session_verified ? "yes" : "no"}</span>
                            <span>Store ready: {metadata.store_fetch_ready ? "yes" : "no"}</span>
                            <span>Parser ready: {metadata.parser_ready ? "yes" : "no"}</span>
                            <span>Fetch ready: {metadata.fetch_ready ? "yes" : "no"}</span>
                            <span>Candidates: {metadata.import_candidates_created}</span>
                            <span>Latest fetch: {formatStatus(metadata.latest_fetch_status)} at {formatDate(metadata.last_fetch)}</span>
                            <span>Pages successful: {metadata.pages_successful}</span>
                            <span>Variants: {metadata.variant_count_total}</span>
                            <span>Quote configured: {metadata.quote_configured ? "yes" : "no"}</span>
                            <span>Latest quote: {formatStatus(metadata.latest_quote_status)}</span>
                            <span>Profile: {metadata.profile_label ?? "-"}</span>
                            <span>{metadata.latest_fetch_reason ?? metadata.latest_quote_reason ?? metadata.error_message ?? metadata.recommended_next_action ?? metadata.notes ?? "No metadata available yet."}</span>
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
