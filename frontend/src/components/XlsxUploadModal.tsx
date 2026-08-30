import React, { useEffect, useState } from "react";

interface XlsxUploadModalProps {
  isOpen: boolean;
  onClose: () => void;
  entityId: number | null;
  entityName: string;
  baseUrl: string;
  authFetch: (url: string, init?: RequestInit) => Promise<Response>;
  onSuccess: (periodStart: string, periodEnd: string) => void;
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

  useEffect(() => {
    if (!isOpen) return;
    setFile(null);
    setMapping(emptyMapping());
    setBalanceMode("single");
    setSignConvention("negative_is_credit");
    setPeriodStart(initialPeriodStart || "2026-04-01");
    setPeriodEnd(initialPeriodEnd || "2027-03-31");
    setError(null);
  }, [isOpen, initialPeriodStart, initialPeriodEnd]);

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
    if (isMock) {
      setTimeout(() => { setSubmitting(false); onSuccess(periodStart, periodEnd); onClose(); }, 300);
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
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || "XLSX trial balance ingestion failed.");
      onSuccess(periodStart, periodEnd);
      onClose();
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
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-md">
      <form onSubmit={submit} className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-3xl shadow-2xl overflow-y-auto max-h-[90vh]">
        <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between">
          <div><h3 className="text-base font-bold text-slate-100">Import Excel Trial Balance</h3><p className="text-xs text-slate-400">Entity: <span className="text-slate-200">{entityName}</span></p></div>
          <button type="button" onClick={onClose} className="text-slate-400 hover:text-slate-200 text-xl">&times;</button>
        </div>
        <div className="p-6 space-y-6">
          {error && <div className="bg-rose-950/80 border border-rose-800 text-rose-200 p-3 rounded-xl text-xs">{error}</div>}
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
