import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { ImportedProductCandidate, Vendor } from "../types";

interface Props {
  vendors: Vendor[];
  candidates: ImportedProductCandidate[];
  busyImportId: string | null;
  onApprove: (importId: string) => void;
  onReject: (importId: string) => void;
  onRefresh?: () => void;
  showExcluded?: boolean;
  onToggleExcluded?: (next: boolean) => void;
}

function vendorName(vendors: Vendor[], vendorId: string) {
  return vendors.find((vendor) => vendor.id === vendorId)?.name ?? vendorId.replace(/_/g, " ");
}

function isNeedsReview(candidate: ImportedProductCandidate): boolean {
  return candidate.needs_review || candidate.eligibility_status === "needs_review";
}

// The category label comes from the product itself, not from the source page slug.
const CATEGORY_LABELS: Record<string, string> = {
  cow_milk: "Milk",
  sheep_milk: "Sheep milk",
  cream: "Cream",
  butter: "Butter",
  cheese: "Cheese",
  eggs: "Eggs",
};

function categoryOf(candidate: ImportedProductCandidate): string {
  if (candidate.product_type) return CATEGORY_LABELS[candidate.product_type] ?? candidate.product_type;
  // Optimizer-unsupported but meaningful families (colostrum, whey) get their own
  // group instead of a generic "Other".
  if (candidate.relevant_type) {
    return candidate.relevant_type.charAt(0).toUpperCase() + candidate.relevant_type.slice(1);
  }
  if (candidate.species && candidate.species !== "unknown") return `Other (${candidate.species})`;
  return "Other / needs review";
}

type Section = "ready" | "needs_review" | "out_of_stock" | "rejected";

function sectionOf(candidate: ImportedProductCandidate): Section {
  if (candidate.review_status === "rejected") return "rejected";
  if (candidate.stock_status === "out_of_stock") return "out_of_stock";
  if (isNeedsReview(candidate)) return "needs_review";
  return "ready";
}

function packageDisplay(candidate: ImportedProductCandidate): string {
  if (candidate.package_display) return candidate.package_display;
  if (candidate.detected_package_size == null && !candidate.detected_unit) return "-";
  return `${candidate.detected_package_size ?? "?"} ${candidate.detected_unit ?? ""}`.trim();
}

function detailTags(candidate: ImportedProductCandidate): string[] {
  const tags = [...candidate.display_tags, ...candidate.processing_tags];
  return tags.filter((tag, index) => tag && tag.toLowerCase() !== "unknown" && tags.indexOf(tag) === index);
}

function reviewLabel(candidate: ImportedProductCandidate) {
  if (candidate.review_status === "approved") return <span className="tag tag-ok">Approved</span>;
  if (candidate.review_status === "rejected") return <span className="tag tag-excluded">Rejected</span>;
  if (isNeedsReview(candidate)) {
    const reason =
      candidate.eligibility_reason ||
      (candidate.missing_fields.length ? `missing ${candidate.missing_fields.join(", ")}` : "review");
    return <span className="tag tag-excluded">Needs review — {reason}</span>;
  }
  return <span className="tag tag-ok">Ready to approve</span>;
}

function CandidateRow({
  candidate,
  busyImportId,
  onApprove,
  onReject,
}: {
  candidate: ImportedProductCandidate;
  busyImportId: string | null;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
}) {
  const busy = busyImportId === candidate.import_id;
  const approveDisabled =
    busy ||
    isNeedsReview(candidate) ||
    candidate.review_status === "approved" ||
    candidate.review_status === "rejected";
  const notes = candidate.review_notes.length ? candidate.review_notes : candidate.warnings;
  const displayType = candidate.display_product_type ?? candidate.product_type ?? candidate.relevant_type;
  return (
    <tr>
      <td title={candidate.raw_text || undefined}>{candidate.display_name ?? candidate.name}</td>
      <td>{displayType ?? <span className="tag tag-excluded">missing</span>}</td>
      <td>{candidate.species ?? "-"}</td>
      <td>{packageDisplay(candidate)}</td>
      <td>{candidate.detected_price == null ? "-" : `$${candidate.detected_price.toFixed(2)}`}</td>
      <td>{candidate.stock_status.replace(/_/g, " ")}</td>
      <td>
        {/* Tags are joined with a comma and a space. */}
        {detailTags(candidate).length > 0 && (
          <span className="detail-tags">{detailTags(candidate).join(", ")}</span>
        )}
        {notes.map((note) => (
          <div key={note} className="warning-text">
            {note}
          </div>
        ))}
      </td>
      <td>{reviewLabel(candidate)}</td>
      <td>
        {/* Vendor is the enclosing group; no per-row Vendor column needed. */}
        <div className="connection-actions">
          <button
            type="button"
            className="table-action-button"
            disabled={approveDisabled}
            onClick={() => onApprove(candidate.import_id)}
          >
            {candidate.review_status === "approved" ? "Approved" : "Approve"}
          </button>
          <button
            type="button"
            className="table-action-button secondary-button"
            disabled={busy || candidate.review_status === "rejected"}
            onClick={() => onReject(candidate.import_id)}
          >
            Reject
          </button>
        </div>
      </td>
    </tr>
  );
}

function SectionTable({
  title,
  rows,
  busyImportId,
  onApprove,
  onReject,
  defaultCollapsed = false,
  extraAction,
}: {
  title: string;
  rows: ImportedProductCandidate[];
  busyImportId: string | null;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  defaultCollapsed?: boolean;
  extraAction?: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  if (rows.length === 0) return null;
  return (
    <div className="review-section">
      <div className="card-head-row">
        <button type="button" className="inline-link-button" onClick={() => setCollapsed((c) => !c)}>
          {collapsed ? "▶" : "▼"} {title} ({rows.length})
        </button>
        {!collapsed && extraAction}
      </div>
      {!collapsed && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Product</th>
                <th>Type</th>
                <th>Species</th>
                <th>Package</th>
                <th>Price</th>
                <th>Stock</th>
                <th>Details</th>
                <th>Review</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((candidate) => (
                <CandidateRow
                  key={candidate.import_id}
                  candidate={candidate}
                  busyImportId={busyImportId}
                  onApprove={onApprove}
                  onReject={onReject}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function ImportReview({
  vendors,
  candidates,
  busyImportId,
  onApprove,
  onReject,
  onRefresh,
  showExcluded = false,
  onToggleExcluded,
}: Props) {
  const [search, setSearch] = useState("");
  const [inStockOnly, setInStockOnly] = useState(false);
  const [typeFilter, setTypeFilter] = useState("");

  const productTypes = useMemo(
    () => Array.from(new Set(candidates.map((c) => c.display_product_type ?? c.product_type ?? c.relevant_type).filter(Boolean))) as string[],
    [candidates],
  );

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return candidates.filter((candidate) => {
      if (inStockOnly && candidate.stock_status === "out_of_stock") return false;
      if (typeFilter && (candidate.display_product_type ?? candidate.product_type ?? candidate.relevant_type) !== typeFilter) return false;
      if (query) {
        const haystack = `${candidate.display_name ?? candidate.name} ${candidate.species ?? ""}`.toLowerCase();
        if (!haystack.includes(query)) return false;
      }
      return true;
    });
  }, [candidates, search, inStockOnly, typeFilter]);

  // Group by vendor -> source page.
  const grouped = useMemo(() => {
    const byVendor = new Map<string, Map<string, ImportedProductCandidate[]>>();
    for (const candidate of filtered) {
      const vendor = candidate.vendor_id;
      const category = categoryOf(candidate);
      if (!byVendor.has(vendor)) byVendor.set(vendor, new Map());
      const categories = byVendor.get(vendor)!;
      if (!categories.has(category)) categories.set(category, []);
      categories.get(category)!.push(candidate);
    }
    return byVendor;
  }, [filtered]);

  const counts = useMemo(() => {
    const c = { ready: 0, needs_review: 0, out_of_stock: 0, rejected: 0 };
    for (const candidate of filtered) c[sectionOf(candidate)] += 1;
    return c;
  }, [filtered]);

  return (
    <section className="card">
      <div className="card-head-row">
        <h2>Import Review</h2>
        <span className="helper-text">
          Ready {counts.ready} · Needs review {counts.needs_review} · Out of stock {counts.out_of_stock} ·
          Rejected {counts.rejected} · Total {filtered.length}
        </span>
      </div>

      <div className="review-filters">
        <input
          type="search"
          placeholder="Search product / species"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
          <option value="">All types</option>
          {productTypes.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
        <label className="helper-text">
          <input type="checkbox" checked={inStockOnly} onChange={(e) => setInStockOnly(e.target.checked)} /> In stock
          only
        </label>
        {onToggleExcluded && (
          <label className="helper-text">
            <input
              type="checkbox"
              checked={showExcluded}
              onChange={(e) => onToggleExcluded(e.target.checked)}
            />{" "}
            Show excluded diagnostics
          </label>
        )}
        {onRefresh && (
          <button type="button" className="table-action-button secondary-button" onClick={onRefresh}>
            Refresh
          </button>
        )}
      </div>

      {grouped.size === 0 && <p className="helper-text">No import candidates to review.</p>}

      {Array.from(grouped.entries()).map(([vendorId, categories]) => {
        const vendorTotal = Array.from(categories.values()).reduce((sum, rows) => sum + rows.length, 0);
        return (
          <VendorGroup
            key={vendorId}
            title={`${vendorName(vendors, vendorId)} (${vendorTotal})`}
          >
            {Array.from(categories.entries()).map(([category, rows]) => {
              const ready = rows.filter((r) => sectionOf(r) === "ready");
              const needs = rows.filter((r) => sectionOf(r) === "needs_review");
              const oos = rows.filter((r) => sectionOf(r) === "out_of_stock");
              const rejected = rows.filter((r) => sectionOf(r) === "rejected");
              const readyApprovable = ready.filter(
                (r) => r.review_status === "pending" && !isNeedsReview(r),
              );
              return (
                <div key={category} className="source-group">
                  <h4>{category}</h4>
                  <SectionTable
                    title="Ready to approve"
                    rows={ready}
                    busyImportId={busyImportId}
                    onApprove={onApprove}
                    onReject={onReject}
                    extraAction={
                      readyApprovable.length > 1 ? (
                        <button
                          type="button"
                          className="table-action-button"
                          disabled={busyImportId != null}
                          onClick={() => readyApprovable.forEach((r) => onApprove(r.import_id))}
                        >
                          Approve all ready ({readyApprovable.length})
                        </button>
                      ) : undefined
                    }
                  />
                  <SectionTable
                    title="Needs review"
                    rows={needs}
                    busyImportId={busyImportId}
                    onApprove={onApprove}
                    onReject={onReject}
                  />
                  <SectionTable
                    title="Out of stock"
                    rows={oos}
                    busyImportId={busyImportId}
                    onApprove={onApprove}
                    onReject={onReject}
                    defaultCollapsed
                  />
                  <SectionTable
                    title="Rejected"
                    rows={rejected}
                    busyImportId={busyImportId}
                    onApprove={onApprove}
                    onReject={onReject}
                    defaultCollapsed
                  />
                </div>
              );
            })}
          </VendorGroup>
        );
      })}
    </section>
  );
}

function VendorGroup({ title, children }: { title: string; children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  return (
    <div className="vendor-group">
      <h3>
        <button type="button" className="inline-link-button" onClick={() => setCollapsed((c) => !c)}>
          {collapsed ? "▶" : "▼"} {title}
        </button>
      </h3>
      {!collapsed && children}
    </div>
  );
}
