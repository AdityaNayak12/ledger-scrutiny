import React, { useEffect, useRef, useState } from "react";

interface IngestionResult {
  status?: string;
  readiness?: string;
  validation_report?: Record<string, unknown>;
  baseline_coverage?: { present?: boolean; complete?: boolean; reason?: string; account_count?: number };
  gaps?: Array<{ start?: string; end?: string }>;
  warnings?: unknown[];
  errors?: unknown[];
  dataset_fingerprint?: string;
  active_batch_ids?: number[];
  source_batch_ids?: number[];
  source_lineage?: { source_family?: string; selected_batch_ids?: number[] };
}

interface XlsxUploadModalProps {
  isOpen: boolean;
  onClose: () => void;
  entityId: number | null;
  entityName: string;
  baseUrl: string;
  authFetch: (url: string, init?: RequestInit) => Promise<Response>;
  onSuccess: (periodStart: string, periodEnd: string, result: IngestionResult) => void;
  isMock: boolean;
  initialPeriodStart?: string;
  initialPeriodEnd?: string;
  opener?: HTMLElement | null;
}

export default function XlsxUploadModal({ isOpen, onClose, entityId, entityName, baseUrl, authFetch, onSuccess, isMock, initialPeriodStart, initialPeriodEnd, opener }: XlsxUploadModalProps) {
  const [file, setFile] = useState<File | null>(null);
  const [baselineFile, setBaselineFile] = useState<File | null>(null);
  const [signedPdfFile, setSignedPdfFile] = useState<File | null>(null);
  const [periodStart, setPeriodStart] = useState(initialPeriodStart || "2026-04-01");
  const [periodEnd, setPeriodEnd] = useState(initialPeriodEnd || "2027-03-31");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<IngestionResult | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const isOpenRef = useRef(isOpen);
  const modalGenerationRef = useRef(0);
  const requestIdRef = useRef(0);
  const onCloseRef = useRef(onClose);
  const submittingRef = useRef(submitting);

  if (isOpenRef.current !== isOpen) {
    isOpenRef.current = isOpen;
    modalGenerationRef.current += 1;
    if (isOpen) submittingRef.current = false;
  }

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    submittingRef.current = submitting;
  }, [submitting]);

  useEffect(() => {
    if (!isOpen) return;
    setFile(null);
    setBaselineFile(null);
    setSignedPdfFile(null);
    setPeriodStart(initialPeriodStart || "2026-04-01");
    setPeriodEnd(initialPeriodEnd || "2027-03-31");
    setError(null);
    setResult(null);
    setSubmitting(false);
  }, [isOpen, initialPeriodStart, initialPeriodEnd]);

  useEffect(() => {
    if (!isOpen) {
      const opener = openerRef.current;
      if (opener?.isConnected) opener.focus();
      openerRef.current = null;
      return;
    }
    const activeElement = document.activeElement;
    openerRef.current = opener || (activeElement instanceof HTMLElement ? activeElement : null);
    closeButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !submittingRef.current) {
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
      ) || []);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isOpen, opener]);

  if (!isOpen) return null;

  const closeModal = () => {
    if (submittingRef.current) return;
    onCloseRef.current();
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file || entityId === null) return;
    const requestId = ++requestIdRef.current;
    const modalGeneration = modalGenerationRef.current;
    setSubmitting(true);
    submittingRef.current = true;
    setError(null);
    setResult(null);
    const isCurrentRequest = () => requestIdRef.current === requestId && modalGenerationRef.current === modalGeneration && isOpenRef.current;
    if (isMock) {
      setTimeout(() => {
        if (!isCurrentRequest()) return;
        const mockResult: IngestionResult = {
          status: "ACTIVE",
          readiness: "READY",
          validation_report: { accepted_rows: 12, document_count: 4, warnings: [] },
          baseline_coverage: { present: true, complete: true, account_count: 12 },
          active_batch_ids: [1],
          source_batch_ids: [1],
          dataset_fingerprint: "demo-dataset",
        };
        setSubmitting(false);
        submittingRef.current = false;
        setResult(mockResult);
        onSuccess(periodStart, periodEnd, mockResult);
        onCloseRef.current();
      }, 300);
      return;
    }
    try {
      const journalFormData = new FormData();
      journalFormData.append("file", file);
      journalFormData.append("column_mapping", "{}");
      journalFormData.append("sign_convention", "negative_is_credit");
      journalFormData.append("target_period_start", periodStart);
      journalFormData.append("target_period_end", periodEnd);
      journalFormData.append("clear_only_period", "true");
      let response = await authFetch(`${baseUrl}/entities/${entityId}/upload-xlsx/confirm`, { method: "POST", body: journalFormData });
      let body = await response.json().catch(() => ({})) as IngestionResult & { detail?: unknown };
      if (!isCurrentRequest()) return;
      if (!response.ok) {
        const detail = body.detail;
        if (detail && typeof detail === "object") {
          const failure = detail as { message?: string; failed_batch_status?: string; readiness?: string; errors?: unknown[] };
          const parts = [failure.message, failure.failed_batch_status && `Batch: ${failure.failed_batch_status}`, failure.readiness && `Readiness: ${failure.readiness}`];
          if (Array.isArray(failure.errors) && failure.errors.length) parts.push(failure.errors.slice(0, 2).map(String).join("; "));
          throw new Error(parts.filter(Boolean).join(" ") || "XLSX general-ledger ingestion failed.");
        }
        throw new Error(typeof detail === "string" ? detail : "XLSX general-ledger ingestion failed.");
      }
      if (baselineFile) {
        const baselineFormData = new FormData();
        baselineFormData.append("file", baselineFile);
        baselineFormData.append("target_period_start", periodStart);
        baselineFormData.append("target_period_end", periodEnd);
        if (signedPdfFile) baselineFormData.append("signed_pdf", signedPdfFile);
        response = await authFetch(`${baseUrl}/entities/${entityId}/upload-xlsx/baseline`, { method: "POST", body: baselineFormData });
        body = await response.json().catch(() => ({})) as IngestionResult & { detail?: unknown };
        if (!isCurrentRequest()) return;
        if (!response.ok) {
          const detail = body.detail;
          if (detail && typeof detail === "object") {
            const failure = detail as { message?: string; failed_batch_status?: string; readiness?: string; errors?: unknown[] };
            const parts = [failure.message, failure.failed_batch_status && `Batch: ${failure.failed_batch_status}`, failure.readiness && `Readiness: ${failure.readiness}`];
            if (Array.isArray(failure.errors) && failure.errors.length) parts.push(failure.errors.slice(0, 2).map(String).join("; "));
            throw new Error(parts.filter(Boolean).join(" ") || "Opening baseline ingestion failed.");
          }
          throw new Error(typeof detail === "string" ? detail : "Opening baseline ingestion failed.");
        }
      }
      setResult(body);
      onSuccess(periodStart, periodEnd, body);
      if ((body.readiness === "READY" || body.readiness === "READY_WITH_WARNINGS") && isCurrentRequest()) {
        submittingRef.current = false;
        setSubmitting(false);
        onCloseRef.current();
      }
    } catch (err: unknown) {
      if (!isCurrentRequest()) return;
      setError(
        err instanceof TypeError && err.message === "Failed to fetch"
          ? `Cannot reach the CApex API at ${baseUrl}. Verify that the backend is running.`
          : err instanceof Error ? err.message : "XLSX general-ledger ingestion failed."
      );
    } finally {
      if (isCurrentRequest()) {
        submittingRef.current = false;
        setSubmitting(false);
      }
    }
  };

  return (
    <div ref={dialogRef} className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-md" role="dialog" aria-modal="true" aria-labelledby="xlsx-upload-title">
      <form onSubmit={submit} className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-3xl shadow-2xl overflow-y-auto max-h-[90vh]">
        <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between">
          <div><h3 id="xlsx-upload-title" className="text-base font-bold text-slate-100">Import Excel General Ledger</h3><p className="text-xs text-slate-400">Entity: <span className="text-slate-200">{entityName}</span></p></div>
          <button ref={closeButtonRef} type="button" onClick={closeModal} disabled={submitting} aria-label="Close Excel import" className="text-slate-400 hover:text-slate-200 disabled:opacity-50 text-xl">&times;</button>
        </div>
        <div className="p-6 space-y-6">
          <div className="bg-indigo-950/40 border border-indigo-800/60 text-indigo-200 p-3 rounded-xl text-xs" role="note">
            Upload the fixed canonical profile with these exact headers: <span className="font-semibold">Document Number, G/L Account, Posting Date, Amount in local currency</span>. Optional source columns are preserved when present.
          </div>
          {error && <div className="bg-rose-950/80 border border-rose-800 text-rose-200 p-3 rounded-xl text-xs" role="alert" aria-live="assertive">{error}</div>}
          {result && (() => {
            const report = result.validation_report || {};
            const baseline = result.baseline_coverage;
            const readiness = result.readiness || "UNKNOWN";
            const ready = readiness === "READY" || readiness === "READY_WITH_WARNINGS";
            return (
              <div className={`border p-4 rounded-xl text-xs space-y-2 ${ready ? "bg-emerald-950/40 border-emerald-800/60 text-emerald-200" : "bg-amber-950/40 border-amber-800/60 text-amber-200"}`} role="status" aria-live="polite">
                <div className="flex flex-wrap justify-between gap-2 font-bold uppercase tracking-wider">
                  <span>Import {result.status || "reported"}</span><span>Readiness: {readiness}</span>
                </div>
                <div className="text-slate-300 normal-case tracking-normal">
                  Report: {String(report.accepted_rows ?? report.accepted ?? 0)} accepted rows · {String(report.document_count ?? 0)} documents · {Array.isArray(result.warnings) ? result.warnings.length : 0} warnings · {Array.isArray(result.errors) ? result.errors.length : 0} errors
                </div>
                <div className="text-slate-300 normal-case tracking-normal">
                  Baseline: {baseline?.complete ? "complete" : baseline?.present ? "present but incomplete" : "required and not present"}
                  {baseline?.account_count !== undefined ? ` · ${baseline.account_count} accounts` : ""}
                </div>
                {result.gaps && result.gaps.length > 0 && <div className="text-amber-200 normal-case tracking-normal">Coverage gaps: {result.gaps.map((gap) => `${gap.start || "?"}–${gap.end || "?"}`).join(", ")}</div>}
                {result.errors && result.errors.length > 0 && <div className="text-rose-200 normal-case tracking-normal">Validation errors: {result.errors.slice(0, 2).map(String).join("; ")}</div>}
                {result.dataset_fingerprint && <div className="font-mono text-xxs text-slate-400 break-all">Fingerprint: {result.dataset_fingerprint}</div>}
                {result.source_batch_ids && result.source_batch_ids.length > 0 && <div className="text-slate-400 normal-case tracking-normal">Source lineage: {result.source_lineage?.source_family || "canonical"} · batches {result.source_batch_ids.join(", ")}</div>}
                {!ready && <div className="font-semibold normal-case tracking-normal">Resolve the baseline, coverage gaps, or validation errors before running scrutiny.</div>}
              </div>
            );
          })()}
          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1">Excel file (.xlsx)</label>
            <input type="file" accept=".xlsx,.XLSX" required onChange={(event) => { const selected = event.target.files?.[0]; if (selected && !/\.xlsx$/i.test(selected.name)) { setFile(null); setError("Please select an XLSX spreadsheet (.xlsx)."); event.currentTarget.value = ""; } else { setFile(selected || null); setError(null); } }} className="block w-full text-xs text-slate-300" />
            {file && <p className="mt-2 text-xs text-slate-400">{file.name}</p>}
          </div>
          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1">Opening baseline Excel (.xlsx, optional)</label>
            <input type="file" accept=".xlsx,.XLSX" onChange={(event) => { const selected = event.target.files?.[0]; if (selected && !/\.xlsx$/i.test(selected.name)) { setBaselineFile(null); setError("Please select an opening baseline XLSX spreadsheet (.xlsx)."); event.currentTarget.value = ""; } else { setBaselineFile(selected || null); setError(null); } }} className="block w-full text-xs text-slate-300" />
            {baselineFile && <p className="mt-2 text-xs text-slate-400">{baselineFile.name}</p>}
            <p className="mt-1 text-xxs text-slate-500">Account-level signed prior-year closing balances are required before scrutiny.</p>
          </div>
          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1">Signed PDF evidence (optional)</label>
            <input type="file" accept=".pdf,application/pdf" onChange={(event) => { const selected = event.target.files?.[0]; if (selected && !/\.pdf$/i.test(selected.name)) { setSignedPdfFile(null); setError("Please select signed PDF evidence (.pdf)."); event.currentTarget.value = ""; } else { setSignedPdfFile(selected || null); setError(null); } }} className="block w-full text-xs text-slate-300" />
            {signedPdfFile && <p className="mt-2 text-xs text-slate-400">{signedPdfFile.name}</p>}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4"><label className="text-xxs font-bold uppercase tracking-wider text-slate-400">Period start<input type="date" value={periodStart} onChange={(event) => setPeriodStart(event.target.value)} disabled={!!initialPeriodStart} required className="mt-1 w-full bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded-xl px-3 py-2" /></label><label className="text-xxs font-bold uppercase tracking-wider text-slate-400">Period end<input type="date" value={periodEnd} onChange={(event) => setPeriodEnd(event.target.value)} disabled={!!initialPeriodEnd} required className="mt-1 w-full bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded-xl px-3 py-2" /></label></div>
          <div className="flex justify-end gap-3"><button type="button" onClick={closeModal} disabled={submitting} className="px-4 py-2 text-xs text-slate-300 bg-slate-800 disabled:opacity-50 rounded-xl">Cancel</button><button type="submit" disabled={!file || submitting} className="px-4 py-2 text-xs font-bold text-white bg-emerald-600 disabled:bg-emerald-900 rounded-xl">{submitting ? "Importing…" : "Import General Ledger"}</button></div>
        </div>
      </form>
    </div>
  );
}
