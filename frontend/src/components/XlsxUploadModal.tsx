import React, { useState, useEffect } from "react";

interface XlsxUploadModalProps {
  isOpen: boolean;
  onClose: () => void;
  entityId: number | null;
  entityName: string;
  baseUrl: string;
  authFetch: (url: string, init?: RequestInit) => Promise<Response>;
  onSuccess: (periodStart: string, periodEnd: string) => void;
  isMock: boolean;
}

interface ColumnMatchInfo {
  column: string;
  col_idx: number;
  confidence: number;
}

interface XlsxPreviewData {
  header_row_number: number | null;
  column_mapping: Record<string, ColumnMatchInfo>;
  missing_fields: string[];
  sample_rows: Array<{ row_number: number; raw_data: Record<string, string> }>;
  parse_errors: Array<{ row_number: number; error: string }>;
  total_data_rows: number;
}

export default function XlsxUploadModal({
  isOpen,
  onClose,
  entityId,
  entityName,
  baseUrl,
  authFetch,
  onSuccess,
  isMock,
}: XlsxUploadModalProps) {
  const [step, setStep] = useState<"upload" | "confirm">("upload");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState<boolean>(false);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Preview detection results
  const [previewData, setPreviewData] = useState<XlsxPreviewData | null>(null);
  const [allDetectedHeaders, setAllDetectedHeaders] = useState<string[]>([]);

  // Configurable mapping state
  const [balanceMode, setBalanceMode] = useState<"single_column" | "separate_drcr">("single_column");
  const [userMapping, setUserMapping] = useState<Record<string, string>>({
    ledger_name: "",
    group_name: "",
    opening_balance: "",
    closing_balance: "",
    opening_debit: "",
    opening_credit: "",
    closing_debit: "",
    closing_credit: "",
  });

  const [signConvention, setSignConvention] = useState<string>("negative_is_credit");
  const [periodStart, setPeriodStart] = useState<string>("2025-04-01");
  const [periodEnd, setPeriodEnd] = useState<string>("2026-03-31");

  useEffect(() => {
    if (!isOpen) {
      // Reset modal state
      setStep("upload");
      setSelectedFile(null);
      setIsAnalyzing(false);
      setIsSubmitting(false);
      setErrorMessage(null);
      setPreviewData(null);
      setAllDetectedHeaders([]);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleFileSelect = async (file: File) => {
    const fname = file.name.toLowerCase();
    if (!fname.endsWith(".xlsx") && !fname.endsWith(".xls")) {
      setErrorMessage("Please select a valid Excel spreadsheet (.xlsx or .xls)");
      return;
    }

    setSelectedFile(file);
    setErrorMessage(null);
    setIsAnalyzing(true);

    if (isMock) {
      setTimeout(() => {
        const mockPreview: XlsxPreviewData = {
          header_row_number: 3,
          column_mapping: {
            ledger_name: { column: "Particulars", col_idx: 0, confidence: 1.0 },
            group_name: { column: "Grp", col_idx: 1, confidence: 0.95 },
            opening_balance: { column: "Op Bal", col_idx: 2, confidence: 0.9 },
            closing_balance: { column: "Cl Bal", col_idx: 3, confidence: 0.9 },
          },
          missing_fields: [],
          sample_rows: [
            { row_number: 4, raw_data: { ledger_name: "Share Capital", group_name: "Capital Account", opening_balance: "-327850.00", closing_balance: "-327850.00" } },
            { row_number: 5, raw_data: { ledger_name: "Furniture and Fixtures", group_name: "Fixed Assets", opening_balance: "120000.00", closing_balance: "108000.00" } },
            { row_number: 6, raw_data: { ledger_name: "Rahul Enterprises", group_name: "Sundry Debtors", opening_balance: "85000.00", closing_balance: "-12000.00" } },
            { row_number: 7, raw_data: { ledger_name: "Verma Traders", group_name: "Sundry Creditors", opening_balance: "-95000.00", closing_balance: "8000.00" } },
            { row_number: 8, raw_data: { ledger_name: "HDFC Bank Current Account", group_name: "Bank Accounts", opening_balance: "250000.00", closing_balance: "410000.00" } },
          ],
          parse_errors: [],
          total_data_rows: 9,
        };

        setPreviewData(mockPreview);
        setAllDetectedHeaders(["Particulars", "Grp", "Op Bal", "Cl Bal"]);
        setUserMapping({
          ledger_name: "Particulars",
          group_name: "Grp",
          opening_balance: "Op Bal",
          closing_balance: "Cl Bal",
          opening_debit: "",
          opening_credit: "",
          closing_debit: "",
          closing_credit: "",
        });
        setIsAnalyzing(false);
        setStep("confirm");
      }, 1000);
      return;
    }

    try {
      const formData = new FormData();
      formData.append("file", file);

      const res = await authFetch(`${baseUrl}/entities/${entityId}/upload-xlsx/preview`, {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || "Failed to analyze Excel header structure");
      }

      const data: XlsxPreviewData = await res.json();
      setPreviewData(data);

      // Extract unique list of detected header column names
      const headersSet = new Set<string>();
      Object.values(data.column_mapping).forEach((m) => {
        if (m.column) headersSet.add(m.column);
      });
      // Extract sample row keys if available
      if (data.sample_rows.length > 0) {
        Object.keys(data.sample_rows[0].raw_data).forEach((k) => {
          const val = data.column_mapping[k]?.column;
          if (val) headersSet.add(val);
        });
      }

      const detectedList = Array.from(headersSet);
      setAllDetectedHeaders(detectedList);

      // Check if separate Dr/Cr columns were detected
      const hasSepDrCr = "opening_debit" in data.column_mapping || "closing_debit" in data.column_mapping;
      const initialMode = hasSepDrCr ? "separate_drcr" : "single_column";
      setBalanceMode(initialMode);
      if (hasSepDrCr) setSignConvention("separate_dr_cr_columns");

      // Set initial user mappings from auto-detected result
      setUserMapping({
        ledger_name: data.column_mapping.ledger_name?.column || detectedList[0] || "",
        group_name: data.column_mapping.group_name?.column || detectedList[1] || "",
        opening_balance: data.column_mapping.opening_balance?.column || detectedList[2] || "",
        closing_balance: data.column_mapping.closing_balance?.column || detectedList[3] || "",
        opening_debit: data.column_mapping.opening_debit?.column || "",
        opening_credit: data.column_mapping.opening_credit?.column || "",
        closing_debit: data.column_mapping.closing_debit?.column || "",
        closing_credit: data.column_mapping.closing_credit?.column || "",
      });

      setStep("confirm");
    } catch (err: any) {
      setErrorMessage(err.message || "Spreadsheet analysis failed.");
    } finally {
      setIsAnalyzing(false);
    }
  };

  const handleConfirmSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedFile || entityId === null) return;

    // Validate essential mappings
    if (!userMapping.ledger_name) {
      setErrorMessage("Please select a spreadsheet column for Ledger Account Name.");
      return;
    }
    if (!userMapping.group_name) {
      setErrorMessage("Please select a spreadsheet column for Account Group Name.");
      return;
    }

    setErrorMessage(null);
    setIsSubmitting(true);

    if (isMock) {
      setTimeout(() => {
        setIsSubmitting(false);
        onSuccess(periodStart, periodEnd);
        onClose();
      }, 1200);
      return;
    }

    try {
      // Build final column mapping dictionary based on active balance mode
      const finalMapping: Record<string, string> = {
        ledger_name: userMapping.ledger_name,
        group_name: userMapping.group_name,
      };

      if (balanceMode === "single_column") {
        if (userMapping.opening_balance) finalMapping.opening_balance = userMapping.opening_balance;
        if (userMapping.closing_balance) finalMapping.closing_balance = userMapping.closing_balance;
      } else {
        if (userMapping.opening_debit) finalMapping.opening_debit = userMapping.opening_debit;
        if (userMapping.opening_credit) finalMapping.opening_credit = userMapping.opening_credit;
        if (userMapping.closing_debit) finalMapping.closing_debit = userMapping.closing_debit;
        if (userMapping.closing_credit) finalMapping.closing_credit = userMapping.closing_credit;
      }

      const formData = new FormData();
      formData.append("file", selectedFile);
      formData.append("column_mapping", JSON.stringify(finalMapping));
      formData.append("sign_convention", signConvention);
      formData.append("target_period_start", periodStart);
      formData.append("target_period_end", periodEnd);
      formData.append("clear_only_period", "true");

      const res = await authFetch(`${baseUrl}/entities/${entityId}/upload-xlsx/confirm`, {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || "XLSX trial balance ingestion failed.");
      }

      onSuccess(periodStart, periodEnd);
      onClose();
    } catch (err: any) {
      setErrorMessage(err.message || "Failed to confirm XLSX ingestion.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-md animate-fade-in">
      <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-3xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
        
        {/* MODAL HEADER */}
        <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between bg-slate-950/50">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-emerald-950/80 border border-emerald-800/80 flex items-center justify-center text-emerald-400 font-bold">
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            </div>
            <div>
              <h3 className="text-base font-bold text-slate-100 flex items-center gap-2">
                Import Excel Trial Balance
                <span className="text-xxs uppercase tracking-wider font-semibold px-2 py-0.5 bg-emerald-950 text-emerald-300 border border-emerald-800/60 rounded-full">
                  Flexible Parser
                </span>
              </h3>
              <p className="text-xs text-slate-400">
                Entity: <span className="font-semibold text-slate-200">{entityName}</span>
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-200 p-1.5 rounded-lg hover:bg-slate-800 transition-all cursor-pointer"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* MODAL BODY */}
        <div className="p-6 overflow-y-auto flex-1 space-y-6">
          
          {/* ERROR ALERT */}
          {errorMessage && (
            <div className="bg-rose-950/80 border border-rose-800/80 text-rose-200 p-4 rounded-xl text-xs flex items-start gap-3">
              <svg className="w-5 h-5 text-rose-400 flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
              <div>
                <span className="font-bold block mb-0.5">Ingestion Error</span>
                <span>{errorMessage}</span>
              </div>
            </div>
          )}

          {/* STEP 1: UPLOAD FILE */}
          {step === "upload" && (
            <div className="space-y-6">
              <div className="border-2 border-dashed border-slate-700 hover:border-emerald-500/60 transition-all rounded-2xl p-8 text-center bg-slate-950/30 flex flex-col items-center justify-center gap-4">
                <div className="w-16 h-16 rounded-2xl bg-slate-800/80 border border-slate-700 flex items-center justify-center text-emerald-400">
                  <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 0115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                  </svg>
                </div>
                <div>
                  <h4 className="text-sm font-bold text-slate-200 mb-1">
                    Select your Excel Trial Balance (.xlsx, .xls)
                  </h4>
                  <p className="text-xs text-slate-400 max-w-md">
                    Our intelligent parser scans the first 15 rows to automatically detect column headers, field names, and numerical balance formats.
                  </p>
                </div>

                <label className="bg-emerald-600 hover:bg-emerald-500 text-white px-5 py-2.5 rounded-xl text-xs font-bold transition-all cursor-pointer shadow-lg shadow-emerald-950/50 flex items-center gap-2">
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                  </svg>
                  Browse Excel File
                  <input
                    type="file"
                    accept=".xlsx,.xls,.XLSX,.XLS"
                    disabled={isAnalyzing}
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) handleFileSelect(f);
                    }}
                    className="hidden"
                  />
                </label>
              </div>

              {isAnalyzing && (
                <div className="bg-slate-950 border border-slate-800 p-5 rounded-2xl flex items-center gap-4">
                  <div className="w-6 h-6 border-2 border-emerald-400 border-t-transparent rounded-full animate-spin"></div>
                  <div>
                    <h5 className="text-xs font-bold text-slate-200">Analyzing Spreadsheet Header Structure...</h5>
                    <p className="text-xxs text-slate-400">Scanning rows 1-15 for ledger names, parent groups, and balances.</p>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* STEP 2: CONFIRMATION & MAPPING */}
          {step === "confirm" && previewData && (
            <form onSubmit={handleConfirmSubmit} className="space-y-6">
              
              {/* HEADER DETECTION SUMMARY CARD */}
              <div className="bg-slate-950/80 border border-slate-800 rounded-2xl p-4 flex flex-wrap items-center justify-between gap-4">
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-emerald-950 text-emerald-400 border border-emerald-800 flex items-center justify-center font-bold text-xs">
                    ✓
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-bold text-slate-200">
                        Header Row #{previewData.header_row_number || "Detected"}
                      </span>
                      <span className="text-xxs font-bold text-emerald-400 bg-emerald-950/60 border border-emerald-800 px-2 py-0.5 rounded-full">
                        {previewData.total_data_rows} Accounts Found
                      </span>
                    </div>
                    <p className="text-xxs text-slate-400">
                      File: <span className="text-slate-300 font-mono">{selectedFile?.name}</span>
                    </p>
                  </div>
                </div>

                <button
                  type="button"
                  onClick={() => setStep("upload")}
                  className="text-xs text-slate-400 hover:text-slate-200 bg-slate-900 border border-slate-800 px-3 py-1.5 rounded-lg transition-all"
                >
                  Change File
                </button>
              </div>

              {/* WARNING: MISSING FIELDS */}
              {previewData.missing_fields.length > 0 && (
                <div className="bg-amber-950/60 border border-amber-800/80 text-amber-300 p-3.5 rounded-xl text-xs flex items-center gap-3">
                  <svg className="w-4 h-4 flex-shrink-0 text-amber-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                  </svg>
                  <span>
                    Could not auto-match: <strong className="text-white">{previewData.missing_fields.join(", ")}</strong>. Please select the correct column below.
                  </span>
                </div>
              )}

              {/* COLUMN MAPPING CUSTOMIZER */}
              <div className="bg-slate-950/50 border border-slate-800/80 rounded-2xl p-4 space-y-4">
                <div className="flex items-center justify-between border-b border-slate-800/80 pb-3">
                  <div>
                    <h4 className="text-xs font-bold text-slate-200">1. Confirm Column Mappings</h4>
                    <p className="text-xxs text-slate-400">Map internal standard fields to spreadsheet headers.</p>
                  </div>

                  {/* BALANCE MODE TOGGLE */}
                  <div className="flex items-center bg-slate-900 border border-slate-800 p-1 rounded-xl gap-1">
                    <button
                      type="button"
                      onClick={() => setBalanceMode("single_column")}
                      className={`px-2.5 py-1 rounded-lg text-xxs font-bold transition-all cursor-pointer ${
                        balanceMode === "single_column"
                          ? "bg-indigo-600 text-white"
                          : "text-slate-400 hover:text-slate-200"
                      }`}
                    >
                      Net Balance Column
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setBalanceMode("separate_drcr");
                        setSignConvention("separate_dr_cr_columns");
                      }}
                      className={`px-2.5 py-1 rounded-lg text-xxs font-bold transition-all cursor-pointer ${
                        balanceMode === "separate_drcr"
                          ? "bg-indigo-600 text-white"
                          : "text-slate-400 hover:text-slate-200"
                      }`}
                    >
                      Separate Dr / Cr Columns
                    </button>
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {/* Ledger Name */}
                  <div>
                    <label className="block text-xxs font-bold uppercase tracking-wider text-slate-400 mb-1">
                      Ledger Account Name <span className="text-rose-400">*</span>
                    </label>
                    <select
                      value={userMapping.ledger_name}
                      onChange={(e) => setUserMapping({ ...userMapping, ledger_name: e.target.value })}
                      className="w-full bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded-xl px-3 py-2 focus:outline-none focus:border-indigo-500 font-medium"
                    >
                      <option value="">-- Select Column --</option>
                      {allDetectedHeaders.map((h, i) => (
                        <option key={i} value={h}>{h}</option>
                      ))}
                    </select>
                  </div>

                  {/* Group Name */}
                  <div>
                    <label className="block text-xxs font-bold uppercase tracking-wider text-slate-400 mb-1">
                      Parent Account Group <span className="text-rose-400">*</span>
                    </label>
                    <select
                      value={userMapping.group_name}
                      onChange={(e) => setUserMapping({ ...userMapping, group_name: e.target.value })}
                      className="w-full bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded-xl px-3 py-2 focus:outline-none focus:border-indigo-500 font-medium"
                    >
                      <option value="">-- Select Column --</option>
                      {allDetectedHeaders.map((h, i) => (
                        <option key={i} value={h}>{h}</option>
                      ))}
                    </select>
                  </div>

                  {/* Single Column Balances */}
                  {balanceMode === "single_column" ? (
                    <>
                      <div>
                        <label className="block text-xxs font-bold uppercase tracking-wider text-slate-400 mb-1">
                          Opening Balance Column
                        </label>
                        <select
                          value={userMapping.opening_balance}
                          onChange={(e) => setUserMapping({ ...userMapping, opening_balance: e.target.value })}
                          className="w-full bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded-xl px-3 py-2 focus:outline-none focus:border-indigo-500 font-medium"
                        >
                          <option value="">-- None / Zero --</option>
                          {allDetectedHeaders.map((h, i) => (
                            <option key={i} value={h}>{h}</option>
                          ))}
                        </select>
                      </div>

                      <div>
                        <label className="block text-xxs font-bold uppercase tracking-wider text-slate-400 mb-1">
                          Closing Balance Column
                        </label>
                        <select
                          value={userMapping.closing_balance}
                          onChange={(e) => setUserMapping({ ...userMapping, closing_balance: e.target.value })}
                          className="w-full bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded-xl px-3 py-2 focus:outline-none focus:border-indigo-500 font-medium"
                        >
                          <option value="">-- Select Column --</option>
                          {allDetectedHeaders.map((h, i) => (
                            <option key={i} value={h}>{h}</option>
                          ))}
                        </select>
                      </div>
                    </>
                  ) : (
                    /* Separate Dr / Cr Columns */
                    <>
                      <div>
                        <label className="block text-xxs font-bold uppercase tracking-wider text-slate-400 mb-1">
                          Opening Debit Column
                        </label>
                        <select
                          value={userMapping.opening_debit}
                          onChange={(e) => setUserMapping({ ...userMapping, opening_debit: e.target.value })}
                          className="w-full bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded-xl px-3 py-2 focus:outline-none focus:border-indigo-500 font-medium"
                        >
                          <option value="">-- None --</option>
                          {allDetectedHeaders.map((h, i) => (
                            <option key={i} value={h}>{h}</option>
                          ))}
                        </select>
                      </div>

                      <div>
                        <label className="block text-xxs font-bold uppercase tracking-wider text-slate-400 mb-1">
                          Opening Credit Column
                        </label>
                        <select
                          value={userMapping.opening_credit}
                          onChange={(e) => setUserMapping({ ...userMapping, opening_credit: e.target.value })}
                          className="w-full bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded-xl px-3 py-2 focus:outline-none focus:border-indigo-500 font-medium"
                        >
                          <option value="">-- None --</option>
                          {allDetectedHeaders.map((h, i) => (
                            <option key={i} value={h}>{h}</option>
                          ))}
                        </select>
                      </div>

                      <div>
                        <label className="block text-xxs font-bold uppercase tracking-wider text-slate-400 mb-1">
                          Closing Debit Column
                        </label>
                        <select
                          value={userMapping.closing_debit}
                          onChange={(e) => setUserMapping({ ...userMapping, closing_debit: e.target.value })}
                          className="w-full bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded-xl px-3 py-2 focus:outline-none focus:border-indigo-500 font-medium"
                        >
                          <option value="">-- None --</option>
                          {allDetectedHeaders.map((h, i) => (
                            <option key={i} value={h}>{h}</option>
                          ))}
                        </select>
                      </div>

                      <div>
                        <label className="block text-xxs font-bold uppercase tracking-wider text-slate-400 mb-1">
                          Closing Credit Column
                        </label>
                        <select
                          value={userMapping.closing_credit}
                          onChange={(e) => setUserMapping({ ...userMapping, closing_credit: e.target.value })}
                          className="w-full bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded-xl px-3 py-2 focus:outline-none focus:border-indigo-500 font-medium"
                        >
                          <option value="">-- None --</option>
                          {allDetectedHeaders.map((h, i) => (
                            <option key={i} value={h}>{h}</option>
                          ))}
                        </select>
                      </div>
                    </>
                  )}
                </div>
              </div>

              {/* SIGN CONVENTION SELECTOR */}
              <div className="bg-slate-950/50 border border-slate-800/80 rounded-2xl p-4 space-y-3">
                <div>
                  <h4 className="text-xs font-bold text-slate-200">2. Select Balance Sign Convention</h4>
                  <p className="text-xxs text-slate-400">Specify how positive and negative balance numbers are stored in your spreadsheet.</p>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <label
                    className={`p-3 rounded-xl border cursor-pointer transition-all flex flex-col justify-between ${
                      signConvention === "negative_is_credit"
                        ? "bg-indigo-950/60 border-indigo-500 text-indigo-200"
                        : "bg-slate-900 border-slate-800 text-slate-400 hover:border-slate-700"
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-bold">Negative is Credit</span>
                      <input
                        type="radio"
                        name="sign_convention"
                        value="negative_is_credit"
                        checked={signConvention === "negative_is_credit"}
                        onChange={() => setSignConvention("negative_is_credit")}
                        className="text-indigo-600 focus:ring-indigo-500"
                      />
                    </div>
                    <p className="text-xxs opacity-80 leading-relaxed">
                      +10,000 = Debit<br />-10,000 = Credit (Tally Default)
                    </p>
                  </label>

                  <label
                    className={`p-3 rounded-xl border cursor-pointer transition-all flex flex-col justify-between ${
                      signConvention === "positive_is_credit"
                        ? "bg-indigo-950/60 border-indigo-500 text-indigo-200"
                        : "bg-slate-900 border-slate-800 text-slate-400 hover:border-slate-700"
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-bold">Positive is Credit</span>
                      <input
                        type="radio"
                        name="sign_convention"
                        value="positive_is_credit"
                        checked={signConvention === "positive_is_credit"}
                        onChange={() => setSignConvention("positive_is_credit")}
                        className="text-indigo-600 focus:ring-indigo-500"
                      />
                    </div>
                    <p className="text-xxs opacity-80 leading-relaxed">
                      +10,000 = Credit<br />-10,000 = Debit
                    </p>
                  </label>

                  <label
                    className={`p-3 rounded-xl border cursor-pointer transition-all flex flex-col justify-between ${
                      signConvention === "separate_dr_cr_columns"
                        ? "bg-indigo-950/60 border-indigo-500 text-indigo-200"
                        : "bg-slate-900 border-slate-800 text-slate-400 hover:border-slate-700"
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-bold">Separate Dr / Cr</span>
                      <input
                        type="radio"
                        name="sign_convention"
                        value="separate_dr_cr_columns"
                        checked={signConvention === "separate_dr_cr_columns"}
                        onChange={() => {
                          setSignConvention("separate_dr_cr_columns");
                          setBalanceMode("separate_drcr");
                        }}
                        className="text-indigo-600 focus:ring-indigo-500"
                      />
                    </div>
                    <p className="text-xxs opacity-80 leading-relaxed">
                      Debit & Credit values are in dedicated columns.
                    </p>
                  </label>
                </div>
              </div>

              {/* TARGET AUDIT PERIOD DATES */}
              <div className="bg-slate-950/50 border border-slate-800/80 rounded-2xl p-4 space-y-3">
                <h4 className="text-xs font-bold text-slate-200">3. Target Audit Period</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xxs font-bold uppercase tracking-wider text-slate-400 mb-1">
                      Period Start Date
                    </label>
                    <input
                      type="date"
                      value={periodStart}
                      onChange={(e) => setPeriodStart(e.target.value)}
                      required
                      className="w-full bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded-xl px-3 py-2 focus:outline-none focus:border-indigo-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xxs font-bold uppercase tracking-wider text-slate-400 mb-1">
                      Period End Date
                    </label>
                    <input
                      type="date"
                      value={periodEnd}
                      onChange={(e) => setPeriodEnd(e.target.value)}
                      required
                      className="w-full bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded-xl px-3 py-2 focus:outline-none focus:border-indigo-500"
                    />
                  </div>
                </div>
              </div>

              {/* SAMPLE ROWS PREVIEW TABLE */}
              {previewData.sample_rows.length > 0 && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <h4 className="text-xs font-bold text-slate-300 uppercase tracking-wider">
                      Parsed Data Sample (First 5 Rows)
                    </h4>
                    <span className="text-xxs text-slate-400">Preview before DB write</span>
                  </div>

                  <div className="bg-slate-950 border border-slate-800 rounded-xl overflow-x-auto">
                    <table className="w-full text-left text-xs">
                      <thead className="bg-slate-900/90 text-slate-400 text-xxs uppercase tracking-wider border-b border-slate-800">
                        <tr>
                          <th className="py-2 px-3">#</th>
                          <th className="py-2 px-3">Ledger Name</th>
                          <th className="py-2 px-3">Parent Group</th>
                          <th className="py-2 px-3 text-right">Opening</th>
                          <th className="py-2 px-3 text-right">Closing</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800/60 font-mono text-xxs text-slate-300">
                        {previewData.sample_rows.map((row, idx) => {
                          const ledger = row.raw_data[userMapping.ledger_name] || row.raw_data.ledger_name || "-";
                          const group = row.raw_data[userMapping.group_name] || row.raw_data.group_name || "-";
                          const op = row.raw_data[userMapping.opening_balance] || row.raw_data.opening_balance || "0.00";
                          const cl = row.raw_data[userMapping.closing_balance] || row.raw_data.closing_balance || "0.00";

                          return (
                            <tr key={idx} className="hover:bg-slate-900/50">
                              <td className="py-2 px-3 text-slate-500">{row.row_number}</td>
                              <td className="py-2 px-3 font-semibold text-slate-100">{ledger}</td>
                              <td className="py-2 px-3 text-slate-400">{group}</td>
                              <td className="py-2 px-3 text-right text-slate-300">{op}</td>
                              <td className="py-2 px-3 text-right text-indigo-300 font-bold">{cl}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* ACTION BUTTONS */}
              <div className="flex items-center justify-end gap-3 pt-3 border-t border-slate-800">
                <button
                  type="button"
                  onClick={onClose}
                  className="px-4 py-2 text-xs font-semibold text-slate-400 hover:text-slate-200 bg-slate-900 border border-slate-800 rounded-xl transition-all cursor-pointer"
                >
                  Cancel
                </button>

                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="bg-emerald-600 hover:bg-emerald-500 disabled:bg-emerald-900 text-white px-5 py-2 rounded-xl text-xs font-bold transition-all cursor-pointer shadow-lg shadow-emerald-950/50 flex items-center gap-2"
                >
                  {isSubmitting ? (
                    <>
                      <div className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
                      <span>Importing Trial Balance...</span>
                    </>
                  ) : (
                    <>
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      <span>Confirm & Import Trial Balance</span>
                    </>
                  )}
                </button>
              </div>

            </form>
          )}

        </div>

      </div>
    </div>
  );
}
