import type {
  CatalogMode,
  ChatResponse,
  NormalizedProduct,
  FetchMetadata,
  FetchJobStatus,
  ImportedProductCandidate,
  LoginSessionResult,
  OptimizeRequest,
  OptimizeResponse,
  ShippingQuote,
  Vendor,
  VendorConnection,
  VendorConnectionMetadata,
} from "./types";

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const REQUEST_TIMEOUT_MS = 15000;

async function readErrorText(res: Response): Promise<string> {
  const text = await res.text();
  return text || `${res.status} ${res.statusText}`;
}

async function getJSON<T>(path: string): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(`${BASE_URL}${path}`, { signal: controller.signal });
    if (!res.ok) throw new Error(`GET ${path} failed: ${await readErrorText(res)}`);
    return res.json();
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error(`GET ${path} timed out after ${REQUEST_TIMEOUT_MS / 1000}s`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

async function postJSON<T>(path: string): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      method: "POST",
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`POST ${path} failed: ${await readErrorText(res)}`);
    return res.json();
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error(`POST ${path} timed out after ${REQUEST_TIMEOUT_MS / 1000}s`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

export function fetchVendors(
  catalogMode: CatalogMode = "imported_only"
): Promise<Vendor[]> {
  return getJSON<Vendor[]>(`/vendors?catalog_mode=${catalogMode}`);
}

export function fetchProducts(
  catalogMode: CatalogMode = "imported_only"
): Promise<NormalizedProduct[]> {
  return getJSON<NormalizedProduct[]>(`/products?catalog_mode=${catalogMode}`);
}

export function fetchVendorConnections(): Promise<VendorConnection[]> {
  return getJSON<VendorConnection[]>("/vendor-connections");
}

export function fetchFetchMetadata(vendorId?: string): Promise<FetchMetadata[]> {
  return getJSON<FetchMetadata[]>(vendorId ? `/fetches/${vendorId}` : "/fetches");
}

export function checkVendorConnection(vendorId: string): Promise<VendorConnection> {
  return postJSON<VendorConnection>(`/vendor-connections/${vendorId}/check`);
}

export function fetchVendorConnection(vendorId: string): Promise<VendorConnection> {
  return getJSON<VendorConnection>(`/vendor-connections/${vendorId}`);
}

export function fetchVendorConnectionMetadata(vendorId: string): Promise<VendorConnectionMetadata> {
  return getJSON<VendorConnectionMetadata>(`/vendor-connections/${vendorId}/metadata`);
}

export function openVendorLoginSession(vendorId: string): Promise<LoginSessionResult> {
  return postJSON<LoginSessionResult>(`/vendor-connections/${vendorId}/login-session?force_open=true`);
}

export function fetchVendorNow(vendorId: string): Promise<FetchJobStatus> {
  return postJSON<FetchJobStatus>(`/vendor-connections/${vendorId}/fetch?force=true`);
}

export function startVendorFetchJob(vendorId: string): Promise<FetchJobStatus> {
  return postJSON<FetchJobStatus>(`/vendor-connections/${vendorId}/fetch?force=true`);
}

export function fetchVendorFetchJob(vendorId: string, jobId: string): Promise<FetchJobStatus> {
  return getJSON<FetchJobStatus>(`/vendor-connections/${vendorId}/fetch-jobs/${jobId}`);
}

export function fetchLatestVendorFetchJob(vendorId: string): Promise<FetchJobStatus> {
  return getJSON<FetchJobStatus>(`/vendor-connections/${vendorId}/fetch-jobs/latest`);
}

export function fetchShippingQuote(vendorId: string): Promise<ShippingQuote> {
  return postJSON<ShippingQuote>(`/vendor-connections/${vendorId}/shipping-quote`);
}

export function fetchImportCandidates(
  includeExcluded = false,
): Promise<ImportedProductCandidate[]> {
  const query = includeExcluded ? "?include_excluded=true" : "";
  return getJSON<ImportedProductCandidate[]>(`/import-candidates${query}`);
}

export function approveImportCandidate(importId: string): Promise<unknown> {
  return postJSON<unknown>(`/import-candidates/${importId}/approve`);
}

export function rejectImportCandidate(importId: string): Promise<ImportedProductCandidate> {
  return postJSON<ImportedProductCandidate>(`/import-candidates/${importId}/reject`);
}

export async function optimizeCart(
  request: OptimizeRequest
): Promise<OptimizeResponse> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(`${BASE_URL}/optimize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`POST /optimize failed: ${await readErrorText(res)}`);
    return res.json();
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error(`POST /optimize timed out after ${REQUEST_TIMEOUT_MS / 1000}s`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

// A chat turn may wait on a model call, so it gets a longer timeout.
const CHAT_TIMEOUT_MS = 60000;

export async function sendChatMessage(
  message: string,
  conversationId: string | null,
  catalogMode: CatalogMode
): Promise<ChatResponse> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), CHAT_TIMEOUT_MS);
  try {
    const res = await fetch(`${BASE_URL}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // With no approved imports the backend falls back to the demo samples,
      // so the default catalog mode is left for it to resolve.
      body: JSON.stringify({
        message,
        conversation_id: conversationId,
        catalog_mode: catalogMode === "imported_only" ? null : catalogMode,
      }),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`POST /chat failed: ${await readErrorText(res)}`);
    return res.json();
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error(`POST /chat timed out after ${CHAT_TIMEOUT_MS / 1000}s`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}
