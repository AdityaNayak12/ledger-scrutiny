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
}

const emptyMapping = () => ({
  ledger_name: "",
  group_name: "",
  opening_balance: "",
  closing_balance: "",
  opening_debit: "",
  opening_credit: "",
  closing_debit: "",
  closing_credit: "",
});

export default function XlsxUploadModal({ isOpen, onClose, entityId, entityName, baseUrl, authFetch, onSuccess, isMock, initialPeriodStart, initialPeriodEnd }: XlsxUploadModalProps) {
  const [file, setFile] = useState<File | null>(null);
  const [mapping, setMapping] = useState(emptyMapping);
  const [balanceMode, setBalanceMode] = useState<"single" | "drcr">("single");
  const [signConvention, setSignConvention] = useState("negative_is_credit");
  const [periodStart, setPeriodStart] = useState(initialPeriodStart || "2026-04-01");
  const [periodEnd, setPeriodEnd] = useState(initialPeriodEnd || "2027-03-31");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<IngestionResult | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    setFile(null);
    setMapping(emptyMapping());
    setBalanceMode("single");
    setSignConvention("negative_is_credit");
    setPeriodStart(initialPeriodStart || "2026-04-01");
    setPeriodEnd(initialPeriodEnd || "2027-03-31");
    setError(null);
    setResult(null);
  }, [isOpen, initialPeriodStart, initialPeriodEnd]);

  useEffect(() => {
    if (!isOpen) return;
    closeButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !submitting) onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isOpen, onClose, submitting]);

  if (!isOpen) return null;

  const updateMapping = (field: keyof ReturnType<typeof emptyMapping>, value: string) => setMapping({ ...mapping, [field]: value });
  const fields = balanceMode === "single"
    ? [["ledger_name", "Ledger Account Name *"], ["group_name", "Parent Account Group *"], ["opening_balance", "Opening Balance"], ["closing_balance", "Closing Balance"]]
    : [["ledger_name", "Ledger Account Name *"], ["group_name", "Parent Account Group *"], ["opening_debit", "Opening Debit"], ["opening_credit", "Opening Credit"], ["closing_debit", "Closing Debit"], ["closing_credit", "Closing Credit"]];

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file || entityId === null) return;
    if (!mapping.ledger_name || !mapping.group_name) return setError("Ledger Account Name and Parent Account Group headers are required.");
    setSubmitting(true);
    setError(null);
    setResult(null);
    if (isMock) {
      setTimeout(() => {
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
        setResult(mockResult);
        onSuccess(periodStart, periodEnd, mockResult);
        onClose();
      }, 300);
      return;
    }
    const columnMapping = Object.fromEntries(Object.entries(mapping).filter(([, header]) => header.trim()));
    const formData = new FormData();
    formData.append("file", file);
    formData.append("column_mapping", JSON.stringify(columnMapping));
    formData.append("sign_convention", signConvention);
    formData.append("target_period_start", periodStart);
    formData.append("target_period_end", periodEnd);
    formData.append("clear_only_period", "true");
    try {
      const response = await authFetch(`${baseUrl}/entities/${entityId}/upload-xlsx/confirm`, { method: "POST", body: formData });
      const body = await response.json().catch(() => ({})) as IngestionResult & { detail?: unknown };
      if (!response.ok) {
        const detail = typeof body.detail === "string" ? body.detail : "XLSX trial balance ingestion failed.";
        throw new Error(detail);
      }
      setResult(body);
      onSuccess(periodStart, periodEnd, body);
      if (body.readiness === "READY" || body.readiness === "READY_WITH_WARNINGS") onClose();
    } catch (err: unknown) {
      setError(
        err instanceof TypeError && err.message === "Failed to fetch"
          ? `Cannot reach the CApex API at ${baseUrl}. Verify that the backend is running.`
          : err instanceof Error ? err.message : "XLSX trial balance ingestion failed."
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-md" role="dialog" aria-modal="true" aria-labelledby="xlsx-upload-title">
      <form onSubmit={submit} className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-3xl shadow-2xl overflow-y-auto max-h-[90vh]">
        <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between">
          <div><h3 id="xlsx-upload-title" className="text-base font-bold text-slate-100">Import Excel Trial Balance</h3><p className="text-xs text-slate-400">Entity: <span className="text-slate-200">{entityName}</span></p></div>
          <button ref={closeButtonRef} type="button" onClick={onClose} aria-label="Close Excel import" className="text-slate-400 hover:text-slate-200 text-xl">&times;</button>
        </div>
        <div className="p-6 space-y-6">
          <div className="bg-indigo-950/40 border border-indigo-800/60 text-indigo-200 p-3 rounded-xl text-xs" role="note">
            Canonical scrutiny requires a complete account-level opening baseline. Imports with missing coverage or validation issues stay open for correction.
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
            <label className="block text-xs font-semibold text-slate-300 mb-1">Excel file (.xlsx or .xls)</label>
            <input type="file" accept=".xlsx,.xls,.XLSX,.XLS" required onChange={(event) => { const selected = event.target.files?.[0]; if (selected && !/\.xlsx?$/i.test(selected.name)) setError("Please select an Excel spreadsheet (.xlsx or .xls)."); else { setFile(selected || null); setError(null); } }} className="block w-full text-xs text-slate-300" />
            {file && <p className="mt-2 text-xs text-slate-400">{file.name}</p>}
          </div>
          <div className="bg-slate-950/50 border border-slate-800 rounded-2xl p-4 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3"><div><h4 className="text-xs font-bold text-slate-200">Exact column headers</h4><p className="text-xxs text-slate-400">Type headers exactly as they appear in the spreadsheet.</p></div><div className="flex gap-2"><button type="button" onClick={() => { setBalanceMode("single"); setSignConvention("negative_is_credit"); }} className={`px-3 py-1.5 rounded-lg text-xxs ${balanceMode === "single" ? "bg-indigo-600 text-white" : "bg-slate-800 text-slate-300"}`}>Net balances</button><button type="button" onClick={() => { setBalanceMode("drcr"); setSignConvention("separate_dr_cr_columns"); }} className={`px-3 py-1.5 rounded-lg text-xxs ${balanceMode === "drcr" ? "bg-indigo-600 text-white" : "bg-slate-800 text-slate-300"}`}>Separate Dr / Cr</button></div></div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">{fields.map(([field, label]) => <label key={field} className="text-xxs font-bold uppercase tracking-wider text-slate-400">{label}<input value={mapping[field as keyof typeof mapping]} onChange={(event) => updateMapping(field as keyof typeof mapping, event.target.value)} placeholder="Exact spreadsheet header" className="mt-1 w-full bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded-xl px-3 py-2 focus:outline-none focus:border-indigo-500 normal-case font-normal" /></label>)}</div>
          </div>
          {balanceMode === "single" && <fieldset className="bg-slate-950/50 border border-slate-800 rounded-2xl p-4"><legend className="px-1 text-xs font-bold text-slate-200">Balance sign convention</legend><label className="mr-5 text-xs text-slate-300"><input type="radio" checked={signConvention === "negative_is_credit"} onChange={() => setSignConvention("negative_is_credit")} /> Negative is credit</label><label className="text-xs text-slate-300"><input type="radio" checked={signConvention === "positive_is_credit"} onChange={() => setSignConvention("positive_is_credit")} /> Positive is credit</label></fieldset>}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4"><label className="text-xxs font-bold uppercase tracking-wider text-slate-400">Period start<input type="date" value={periodStart} onChange={(event) => setPeriodStart(event.target.value)} disabled={!!initialPeriodStart} required className="mt-1 w-full bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded-xl px-3 py-2" /></label><label className="text-xxs font-bold uppercase tracking-wider text-slate-400">Period end<input type="date" value={periodEnd} onChange={(event) => setPeriodEnd(event.target.value)} disabled={!!initialPeriodEnd} required className="mt-1 w-full bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded-xl px-3 py-2" /></label></div>
          <div className="flex justify-end gap-3"><button type="button" onClick={onClose} className="px-4 py-2 text-xs text-slate-300 bg-slate-800 rounded-xl">Cancel</button><button type="submit" disabled={!file || submitting} className="px-4 py-2 text-xs font-bold text-white bg-emerald-600 disabled:bg-emerald-900 rounded-xl">{submitting ? "Importing…" : "Import Trial Balance"}</button></div>
        </div>
      </form>
    </div>
  );
}
