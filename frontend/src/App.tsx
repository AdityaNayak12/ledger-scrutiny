import React, { useState, useEffect, useRef } from "react";
import { INITIAL_MOCK_ENTITIES, MOCK_EXCEPTIONS } from "./mockData";
import type { Entity, Exception } from "./mockData";

const BASE_URL = "http://localhost:8000";

export default function App() {
  const [isMock, setIsMock] = useState<boolean>(() => {
    const saved = localStorage.getItem("isMockMode");
    return saved !== null ? saved === "true" : true;
  });

  const [entities, setEntities] = useState<Entity[]>([]);
  const [selectedEntityId, setSelectedEntityId] = useState<number | null>(null);
  const [exceptions, setExceptions] = useState<Exception[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Loading States
  const [isLoadingEntities, setIsLoadingEntities] = useState<boolean>(false);
  const [isUploading, setIsUploading] = useState<boolean>(false);
  const [isScrutinizing, setIsScrutinizing] = useState<boolean>(false);
  const [isLoadingExceptions, setIsLoadingExceptions] = useState<boolean>(false);

  // Filters & Sorting
  const [severityFilter, setSeverityFilter] = useState<string>("all");
  const [sortBy, setSortBy] = useState<string>("severity");

  // Modals & Forms
  const [showAddModal, setShowAddModal] = useState<boolean>(false);
  const [newEntity, setNewEntity] = useState({
    name: "",
    financial_year_start: "2025-04-01",
    financial_year_end: "2026-03-31",
    materiality_threshold: "15000",
  });

  const fileInputRef = useRef<HTMLInputElement>(null);

  // Persist mock mode toggle
  useEffect(() => {
    localStorage.setItem("isMockMode", String(isMock));
    setSelectedEntityId(null);
    setExceptions([]);
    setErrorMsg(null);
  }, [isMock]);

  // Fetch entities list
  useEffect(() => {
    fetchEntities();
  }, [isMock]);

  // Fetch exceptions when selected entity changes
  useEffect(() => {
    if (selectedEntityId !== null) {
      const selected = entities.find((e) => e.id === selectedEntityId);
      if (selected && selected.scrutinized) {
        fetchExceptions(selectedEntityId);
      } else {
        setExceptions([]);
      }
    } else {
      setExceptions([]);
    }
  }, [selectedEntityId, entities]);

  const fetchEntities = async () => {
    setErrorMsg(null);
    if (isMock) {
      const saved = localStorage.getItem("mock_entities");
      if (saved) {
        setEntities(JSON.parse(saved));
      } else {
        setEntities(INITIAL_MOCK_ENTITIES);
        localStorage.setItem("mock_entities", JSON.stringify(INITIAL_MOCK_ENTITIES));
      }
    } else {
      setIsLoadingEntities(true);
      try {
        const res = await fetch(`${BASE_URL}/entities`);
        if (!res.ok) throw new Error("Failed to fetch entities from server");
        const data = await res.json();
        // Live data needs parsing helper properties for UI
        const enriched = data.map((e: any) => ({
          ...e,
          has_uploaded: true, // If returned from API, assume they are registered
          scrutinized: true,   // Assume scrutiny can be triggered/listed
        }));
        setEntities(enriched);
      } catch (err: any) {
        setErrorMsg(`API Error: ${err.message}. Fallback to Mock Mode for testing.`);
        setEntities([]);
      } finally {
        setIsLoadingEntities(false);
      }
    }
  };

  const handleCreateEntity = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);
    
    const payload = {
      name: newEntity.name.trim(),
      financial_year_start: newEntity.financial_year_start,
      financial_year_end: newEntity.financial_year_end,
      materiality_threshold: parseFloat(newEntity.materiality_threshold) || 0,
    };

    if (!payload.name) {
      setErrorMsg("Entity name is required");
      return;
    }

    if (isMock) {
      const nextId = entities.length > 0 ? Math.max(...entities.map((ent) => ent.id)) + 1 : 1;
      const created: Entity = {
        id: nextId,
        name: payload.name,
        financial_year_start: payload.financial_year_start,
        financial_year_end: payload.financial_year_end,
        materiality_threshold: payload.materiality_threshold,
        has_uploaded: false,
        scrutinized: false,
      };
      const updated = [...entities, created];
      setEntities(updated);
      localStorage.setItem("mock_entities", JSON.stringify(updated));
      setSelectedEntityId(created.id);
      setShowAddModal(false);
      // Reset form
      setNewEntity({
        name: "",
        financial_year_start: "2025-04-01",
        financial_year_end: "2026-03-31",
        materiality_threshold: "15000",
      });
    } else {
      try {
        const res = await fetch(`${BASE_URL}/entities`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error("Failed to create entity");
        const created = await res.json();
        await fetchEntities();
        setSelectedEntityId(created.id);
        setShowAddModal(false);
      } catch (err: any) {
        setErrorMsg(err.message);
      }
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || selectedEntityId === null) return;
    setErrorMsg(null);

    if (isMock) {
      setIsUploading(true);
      setTimeout(() => {
        const updated = entities.map((ent) =>
          ent.id === selectedEntityId
            ? { ...ent, has_uploaded: true, scrutinized: false }
            : ent
        );
        setEntities(updated);
        localStorage.setItem("mock_entities", JSON.stringify(updated));
        setIsUploading(false);
        if (fileInputRef.current) fileInputRef.current.value = "";
      }, 1500);
    } else {
      setIsUploading(true);
      const formData = new FormData();
      formData.append("file", file);
      try {
        const res = await fetch(`${BASE_URL}/entities/${selectedEntityId}/upload`, {
          method: "POST",
          body: formData,
        });
        if (!res.ok) throw new Error("Failed to upload XML file");
        
        // Update local entities upload status
        setEntities((prev) =>
          prev.map((ent) =>
            ent.id === selectedEntityId
              ? { ...ent, has_uploaded: true, scrutinized: false }
              : ent
          )
        );
        if (fileInputRef.current) fileInputRef.current.value = "";
      } catch (err: any) {
        setErrorMsg(`Upload failed: ${err.message}`);
      } finally {
        setIsUploading(false);
      }
    }
  };

  const handleTriggerScrutiny = async () => {
    if (selectedEntityId === null) return;
    setErrorMsg(null);
    setIsScrutinizing(true);

    if (isMock) {
      setTimeout(() => {
        // Set entity as scrutinized in mock state
        const updated = entities.map((ent) =>
          ent.id === selectedEntityId ? { ...ent, scrutinized: true } : ent
        );
        setEntities(updated);
        localStorage.setItem("mock_entities", JSON.stringify(updated));
        setIsScrutinizing(false);
        // Load fixture exceptions
        const mockExcs = MOCK_EXCEPTIONS[selectedEntityId] || [];
        setExceptions(mockExcs);
      }, 1800);
    } else {
      try {
        const res = await fetch(`${BASE_URL}/entities/${selectedEntityId}/scrutiny-run`, {
          method: "POST",
        });
        if (!res.ok) throw new Error("Scrutiny run failed");
        
        // Mark as scrutinized
        setEntities((prev) =>
          prev.map((ent) =>
            ent.id === selectedEntityId ? { ...ent, scrutinized: true } : ent
          )
        );
        // Fetch generated exceptions
        await fetchExceptions(selectedEntityId);
      } catch (err: any) {
        setErrorMsg(`Scrutiny run failed: ${err.message}`);
      } finally {
        setIsScrutinizing(false);
      }
    }
  };

  const fetchExceptions = async (entityId: number) => {
    setIsLoadingExceptions(true);
    if (isMock) {
      const mockExcs = MOCK_EXCEPTIONS[entityId] || [];
      setExceptions(mockExcs);
      setIsLoadingExceptions(false);
    } else {
      try {
        const res = await fetch(`${BASE_URL}/entities/${entityId}/exceptions`);
        if (!res.ok) throw new Error("Failed to load exceptions");
        const data = await res.json();
        setExceptions(data);
      } catch (err: any) {
        setErrorMsg(`Failed to load exceptions: ${err.message}`);
      } finally {
        setIsLoadingExceptions(false);
      }
    }
  };

  const selectedEntity = entities.find((e) => e.id === selectedEntityId);

  // Sorting & Filtering Exceptions
  const severityWeight = { critical: 3, warning: 2, info: 1 };

  const processedExceptions = exceptions
    .filter((exc) => {
      if (severityFilter === "all") return true;
      return exc.severity.toLowerCase() === severityFilter.toLowerCase();
    })
    .sort((a, b) => {
      if (sortBy === "severity") {
        const weightA = severityWeight[a.severity] || 0;
        const weightB = severityWeight[b.severity] || 0;
        return weightB - weightA; // Critical first by default
      }
      if (sortBy === "account") {
        const nameA = a.ledger_account_name || "";
        const nameB = b.ledger_account_name || "";
        return nameA.localeCompare(nameB);
      }
      if (sortBy === "rule") {
        return a.rule_name.localeCompare(b.rule_name);
      }
      return 0;
    });

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100 flex flex-col font-sans">
      
      {/* HEADER NAVBAR */}
      <header className="bg-slate-950 border-b border-slate-800 py-4 px-6 flex items-center justify-between shadow-lg sticky top-0 z-40">
        <div className="flex items-center gap-3">
          <div className="bg-indigo-600 text-white rounded-lg p-2 font-bold text-lg tracking-wider shadow-md shadow-indigo-900/50">
            LS
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-white m-0">LedgerScrutiny</h1>
            <p className="text-xs text-indigo-400 font-semibold tracking-wider uppercase">CA Pre-Audit Scrutiny Engine</p>
          </div>
        </div>

        {/* Mock Mode Control Toggle */}
        <div className="flex items-center gap-4 bg-slate-900 px-4 py-2 rounded-xl border border-slate-800 shadow-inner">
          <div className="flex flex-col text-right">
            <span className="text-xs text-slate-400 font-medium">Environment Mode</span>
            <span className={`text-sm font-bold ${isMock ? "text-indigo-400" : "text-emerald-400"}`}>
              {isMock ? "Mock Demonstration Mode" : "Live API (Postgres)"}
            </span>
          </div>
          <button
            onClick={() => setIsMock(!isMock)}
            className={`w-14 h-7 flex items-center rounded-full p-1 cursor-pointer transition-colors duration-300 focus:outline-none ${
              isMock ? "bg-indigo-600" : "bg-emerald-600"
            }`}
          >
            <div
              className={`bg-white w-5 h-5 rounded-full shadow-md transform transition-transform duration-300 ${
                isMock ? "translate-x-7" : "translate-x-0"
              }`}
            />
          </button>
        </div>
      </header>

      {/* ERROR ALERT */}
      {errorMsg && (
        <div className="bg-rose-950/80 border-b border-rose-800 text-rose-200 px-6 py-3 text-sm flex justify-between items-center animate-pulse">
          <div className="flex items-center gap-2">
            <svg className="w-5 h-5 text-rose-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <span>{errorMsg}</span>
          </div>
          <button onClick={() => setErrorMsg(null)} className="text-rose-400 hover:text-rose-200 font-bold focus:outline-none text-lg">
            &times;
          </button>
        </div>
      )}

      {/* MAIN CONTAINER WORKSPACE */}
      <main className="flex-1 flex overflow-hidden">
        
        {/* SIDEBAR - Entity List */}
        <aside className="w-80 bg-slate-950/50 border-r border-slate-800 flex flex-col p-4 overflow-y-auto">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold tracking-wider uppercase text-slate-400 m-0">Client Entities</h2>
            <button
              onClick={() => setShowAddModal(true)}
              className="bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg px-2.5 py-1 text-xs font-bold transition-all shadow-md shadow-indigo-900/30 flex items-center gap-1 cursor-pointer"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 4v16m8-8H4" />
              </svg>
              Add Client
            </button>
          </div>

          {isLoadingEntities ? (
            <div className="flex-1 flex flex-col items-center justify-center text-slate-500 text-sm gap-2">
              <div className="w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
              <span>Loading clients...</span>
            </div>
          ) : entities.length === 0 ? (
            <div className="flex-1 flex flex-col items-center justify-center text-slate-500 text-center text-xs border-2 border-dashed border-slate-800 rounded-xl p-4">
              <svg className="w-8 h-8 text-slate-700 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
              </svg>
              <span>No clients registered. Click 'Add Client' to begin.</span>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {entities.map((ent) => {
                const isSelected = ent.id === selectedEntityId;
                return (
                  <button
                    key={ent.id}
                    onClick={() => setSelectedEntityId(ent.id)}
                    className={`text-left p-3 rounded-xl border transition-all duration-200 cursor-pointer ${
                      isSelected
                        ? "bg-slate-800/80 border-indigo-500/50 shadow-md shadow-indigo-950/20"
                        : "bg-slate-900/40 border-slate-850 hover:bg-slate-800/30 hover:border-slate-700"
                    }`}
                  >
                    <h3 className="font-bold text-sm text-slate-100 mb-1 tracking-tight truncate">{ent.name}</h3>
                    <div className="flex justify-between items-center text-xxs text-slate-400 font-semibold uppercase tracking-wider">
                      <span>FY: {ent.financial_year_start.split("-")[0]}-{ent.financial_year_end.split("-")[0].substring(2)}</span>
                      <span>Mat: ₹{ent.materiality_threshold.toLocaleString()}</span>
                    </div>

                    {/* Status Badge */}
                    <div className="mt-2.5 flex items-center justify-between">
                      <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold uppercase tracking-wider ${
                        ent.scrutinized
                          ? "bg-emerald-950/60 text-emerald-400 border border-emerald-900/50"
                          : ent.has_uploaded
                          ? "bg-amber-950/60 text-amber-400 border border-amber-900/50"
                          : "bg-slate-800 text-slate-400 border border-slate-700"
                      }`}>
                        {ent.scrutinized ? "Scrutinized" : ent.has_uploaded ? "Uploaded" : "Pending XML"}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </aside>

        {/* WORKSPACE DETAIL VIEW */}
        <section className="flex-1 flex flex-col bg-slate-900 overflow-y-auto p-6">
          {!selectedEntity ? (
            <div className="flex-1 flex flex-col items-center justify-center text-center text-slate-500 max-w-lg mx-auto">
              <div className="bg-indigo-950/30 border border-indigo-900/40 p-6 rounded-3xl mb-4 shadow-xl">
                <svg className="w-16 h-16 text-indigo-400 mx-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
              </div>
              <h2 className="text-xl font-bold text-slate-200 mb-2">Pre-Audit Scrutiny Dashboard</h2>
              <p className="text-sm text-slate-400 leading-relaxed">
                Select a client entity from the sidebar to review its ledger accounts, upload Tally XML files, and run the automated scrutiny rule pipeline.
              </p>
            </div>
          ) : (
            <div className="flex flex-col gap-6 flex-1">
              
              {/* ENTITY SUMMARY HEADER & ACTION BOARD */}
              <div className="bg-slate-950/60 rounded-2xl border border-slate-800 p-5 shadow-xl flex flex-col md:flex-row justify-between md:items-center gap-4">
                <div>
                  <span className="text-xxs text-indigo-400 font-bold uppercase tracking-wider">Active Client Scrutiny Workspace</span>
                  <h2 className="text-2xl font-extrabold text-white mt-1 mb-2 tracking-tight">{selectedEntity.name}</h2>
                  <div className="flex flex-wrap gap-x-4 gap-y-2 text-xs text-slate-400 font-medium">
                    <span className="flex items-center gap-1.5">
                      <svg className="w-4 h-4 text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>
                      Financial Period: <strong className="text-slate-200 font-semibold">{selectedEntity.financial_year_start} to {selectedEntity.financial_year_end}</strong>
                    </span>
                    <span className="flex items-center gap-1.5">
                      <svg className="w-4 h-4 text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v12a2 2 0 00-2 2z" /></svg>
                      Materiality Threshold: <strong className="text-indigo-400 font-semibold">₹{selectedEntity.materiality_threshold.toLocaleString()}</strong>
                    </span>
                  </div>
                </div>

                {/* Pipeline controls */}
                <div className="flex items-center gap-3">
                  {!selectedEntity.has_uploaded ? (
                    <div className="flex flex-col gap-2">
                      <label className="text-xs text-slate-400 font-semibold">Upload Tally XML Export</label>
                      <input
                        type="file"
                        accept=".xml"
                        ref={fileInputRef}
                        onChange={handleFileUpload}
                        disabled={isUploading}
                        className="hidden"
                      />
                      <button
                        onClick={() => fileInputRef.current?.click()}
                        disabled={isUploading}
                        className="bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-800 disabled:text-slate-600 text-white rounded-xl px-4 py-2.5 text-sm font-bold transition-all shadow-md shadow-indigo-900/30 flex items-center justify-center gap-2 cursor-pointer focus:outline-none"
                      >
                        {isUploading ? (
                          <>
                            <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
                            <span>Parsing XML...</span>
                          </>
                        ) : (
                          <>
                            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" /></svg>
                            <span>Select XML file</span>
                          </>
                        )}
                      </button>
                    </div>
                  ) : (
                    <div className="flex flex-col sm:flex-row gap-3">
                      {/* Upload new XML option */}
                      <input
                        type="file"
                        accept=".xml"
                        ref={fileInputRef}
                        onChange={handleFileUpload}
                        disabled={isUploading || isScrutinizing}
                        className="hidden"
                      />
                      <button
                        onClick={() => fileInputRef.current?.click()}
                        disabled={isUploading || isScrutinizing}
                        className="bg-slate-900 border border-slate-800 hover:border-slate-700 text-slate-300 rounded-xl px-4 py-2.5 text-sm font-bold transition-all flex items-center justify-center gap-2 cursor-pointer focus:outline-none"
                      >
                        {isUploading ? "Uploading..." : "Re-upload XML"}
                      </button>

                      {/* Scrutiny trigger button */}
                      <button
                        onClick={handleTriggerScrutiny}
                        disabled={isScrutinizing}
                        className="bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-800 disabled:text-slate-600 text-white rounded-xl px-5 py-2.5 text-sm font-extrabold tracking-wide uppercase transition-all shadow-lg shadow-indigo-950/50 flex items-center justify-center gap-2 cursor-pointer focus:outline-none"
                      >
                        {isScrutinizing ? (
                          <>
                            <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
                            <span>Analyzing...</span>
                          </>
                        ) : (
                          <>
                            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                            <span>Run Scrutiny Pass</span>
                          </>
                        )}
                      </button>
                    </div>
                  )}
                </div>
              </div>

              {/* EXCEPTIONS REPORT WORKSPACE */}
              {!selectedEntity.scrutinized ? (
                <div className="flex-1 bg-slate-950/30 border border-slate-850 rounded-3xl p-12 text-center flex flex-col items-center justify-center">
                  <div className="bg-slate-900 border border-slate-800 text-slate-400 p-5 rounded-full mb-4">
                    {selectedEntity.has_uploaded ? (
                      <svg className="w-12 h-12 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
                      </svg>
                    ) : (
                      <svg className="w-12 h-12 text-slate-500 animate-pulse" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                      </svg>
                    )}
                  </div>
                  <h3 className="text-lg font-bold text-slate-200 mb-2">
                    {selectedEntity.has_uploaded ? "XML Uploaded Successfully" : "Awaiting Tally XML Upload"}
                  </h3>
                  <p className="text-sm text-slate-400 max-w-sm mb-6 leading-relaxed">
                    {selectedEntity.has_uploaded
                      ? "The ledger data is normalized. Click 'Run Scrutiny Pass' to execute the rule validation pipeline."
                      : "Please upload the client's Tally XML export file to ingest their trial balance and transactions."}
                  </p>
                  
                  {/* Demo Helper Prompt for Tally XML upload */}
                  {!selectedEntity.has_uploaded && (
                    <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-3 max-w-md text-xxs text-slate-400 text-left">
                      <strong className="text-slate-300 font-bold block mb-1">Demonstration Notice:</strong>
                      In Mock Mode, any dummy XML file can be uploaded, or you can drag and drop [sample_tally_export.xml](file:///Users/adinayak18/Desktop/ledger-scrutiny/sample_data/sample_tally_export.xml) to trigger the simulated ingestion of their ledger records.
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex-1 flex flex-col bg-slate-950/40 rounded-3xl border border-slate-800 overflow-hidden shadow-xl">
                  
                  {/* Filter & Sort Bar */}
                  <div className="bg-slate-950 border-b border-slate-800 py-3.5 px-5 flex flex-col sm:flex-row justify-between sm:items-center gap-3">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">Filter Severity:</span>
                      <div className="flex bg-slate-900 border border-slate-850 p-1 rounded-xl">
                        {["all", "critical", "warning", "info"].map((sev) => (
                          <button
                            key={sev}
                            onClick={() => setSeverityFilter(sev)}
                            className={`px-3 py-1 rounded-lg text-xs font-bold uppercase transition-all cursor-pointer ${
                              severityFilter === sev
                                ? "bg-indigo-600 text-white shadow-sm"
                                : "text-slate-400 hover:text-slate-200"
                            }`}
                          >
                            {sev}
                          </button>
                        ))}
                      </div>
                    </div>

                    <div className="flex items-center gap-2">
                      <label className="text-xs font-bold text-slate-400 uppercase tracking-wider">Sort By:</label>
                      <select
                        value={sortBy}
                        onChange={(e) => setSortBy(e.target.value)}
                        className="bg-slate-900 border border-slate-800 rounded-xl text-xs font-semibold px-3 py-1.5 text-slate-200 focus:outline-none focus:border-indigo-500 cursor-pointer"
                      >
                        <option value="severity">Severity (Critical First)</option>
                        <option value="account">Ledger Account Name</option>
                        <option value="rule">Audit Rule Name</option>
                      </select>
                    </div>
                  </div>

                  {/* Exception Table Container */}
                  <div className="flex-1 overflow-x-auto">
                    {isLoadingExceptions ? (
                      <div className="h-64 flex flex-col items-center justify-center text-slate-500 text-sm gap-2">
                        <div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
                        <span>Loading exceptions...</span>
                      </div>
                    ) : processedExceptions.length === 0 ? (
                      <div className="h-64 flex flex-col items-center justify-center text-slate-500 text-sm p-4">
                        <svg className="w-10 h-10 text-emerald-500/30 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                        <span className="font-semibold text-slate-300">Scrutiny Complete — Clean Run</span>
                        <p className="text-slate-500 text-xs text-center mt-1">No exceptions match the selected filter/materiality configuration.</p>
                      </div>
                    ) : (
                      <table className="w-full text-left border-collapse table-auto">
                        <thead>
                          <tr className="bg-slate-950/60 text-slate-400 border-b border-slate-850 uppercase text-xxs font-bold tracking-wider">
                            <th className="py-3 px-5 w-32">Severity</th>
                            <th className="py-3 px-5 w-48">Rule Name</th>
                            <th className="py-3 px-5 w-52">Ledger Account</th>
                            <th className="py-3 px-5">Scrutiny Audit Findings</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-850">
                          {processedExceptions.map((exc) => {
                            const isCritical = exc.severity === "critical";
                            const isWarning = exc.severity === "warning";
                            
                            return (
                              <tr key={exc.id} className="hover:bg-slate-900/30 transition-all group">
                                
                                {/* Severity Badge Column */}
                                <td className="py-4 px-5">
                                  <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xxs font-extrabold uppercase tracking-widest border ${
                                    isCritical
                                      ? "bg-rose-950/30 text-rose-400 border-rose-900/50 shadow-sm shadow-rose-950/20"
                                      : isWarning
                                      ? "bg-amber-950/30 text-amber-400 border-amber-900/50 shadow-sm shadow-amber-950/20"
                                      : "bg-blue-950/30 text-blue-400 border-blue-900/50 shadow-sm shadow-blue-950/20"
                                  }`}>
                                    <span className={`w-1.5 h-1.5 rounded-full ${
                                      isCritical ? "bg-rose-500" : isWarning ? "bg-amber-500" : "bg-blue-500"
                                    }`} />
                                    {exc.severity}
                                  </span>
                                </td>

                                {/* Rule Name Column */}
                                <td className="py-4 px-5 font-mono text-xs text-slate-300 font-semibold">
                                  {exc.rule_name}
                                </td>

                                {/* Ledger Account Column */}
                                <td className="py-4 px-5 font-bold text-sm text-slate-200 tracking-tight">
                                  {exc.ledger_account_name ? (
                                    <span className="flex items-center gap-1.5">
                                      <span className="w-1.5 h-1.5 rounded-full bg-slate-600" />
                                      {exc.ledger_account_name}
                                    </span>
                                  ) : (
                                    <span className="text-slate-600 font-normal italic">N/A</span>
                                  )}
                                </td>

                                {/* Message Finding Column */}
                                <td className="py-4 px-5 text-sm text-slate-300 font-medium leading-relaxed">
                                  {exc.message}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </section>
      </main>

      {/* CREATE NEW CLIENT ENTITY MODAL */}
      {showAddModal && (
        <div className="fixed inset-0 bg-slate-950/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-3xl w-full max-w-md shadow-2xl overflow-hidden animate-fadeIn">
            
            {/* Modal Header */}
            <div className="bg-slate-950 py-4 px-6 border-b border-slate-850 flex justify-between items-center">
              <h2 className="text-base font-bold text-white m-0">Register Client Entity</h2>
              <button
                onClick={() => setShowAddModal(false)}
                className="text-slate-400 hover:text-white font-bold text-xl cursor-pointer focus:outline-none"
              >
                &times;
              </button>
            </div>

            {/* Modal Form */}
            <form onSubmit={handleCreateEntity} className="p-6 flex flex-col gap-4">
              
              <div>
                <label className="block text-xxs font-extrabold uppercase text-slate-400 tracking-wider mb-1.5">Company / Client Name</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. Rahul Enterprises"
                  value={newEntity.name}
                  onChange={(e) => setNewEntity({ ...newEntity, name: e.target.value })}
                  className="w-full bg-slate-950 border border-slate-850 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-indigo-500 placeholder-slate-600 font-semibold"
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xxs font-extrabold uppercase text-slate-400 tracking-wider mb-1.5">FY Start Date</label>
                  <input
                    type="date"
                    required
                    value={newEntity.financial_year_start}
                    onChange={(e) => setNewEntity({ ...newEntity, financial_year_start: e.target.value })}
                    className="w-full bg-slate-950 border border-slate-850 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-indigo-500 font-semibold"
                  />
                </div>
                <div>
                  <label className="block text-xxs font-extrabold uppercase text-slate-400 tracking-wider mb-1.5">FY End Date</label>
                  <input
                    type="date"
                    required
                    value={newEntity.financial_year_end}
                    onChange={(e) => setNewEntity({ ...newEntity, financial_year_end: e.target.value })}
                    className="w-full bg-slate-950 border border-slate-850 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-indigo-500 font-semibold"
                  />
                </div>
              </div>

              <div>
                <label className="block text-xxs font-extrabold uppercase text-slate-400 tracking-wider mb-1.5">Materiality Threshold (INR)</label>
                <input
                  type="number"
                  required
                  placeholder="e.g. 15000"
                  value={newEntity.materiality_threshold}
                  onChange={(e) => setNewEntity({ ...newEntity, materiality_threshold: e.target.value })}
                  className="w-full bg-slate-950 border border-slate-850 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-indigo-500 placeholder-slate-600 font-bold"
                />
              </div>

              {/* Modal Actions */}
              <div className="flex gap-3 justify-end mt-4">
                <button
                  type="button"
                  onClick={() => setShowAddModal(false)}
                  className="bg-slate-950 hover:bg-slate-850 border border-slate-800 text-slate-300 rounded-xl px-4 py-2.5 text-xs font-bold transition-all cursor-pointer focus:outline-none"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl px-4 py-2.5 text-xs font-extrabold uppercase tracking-wide transition-all shadow-md shadow-indigo-900/30 cursor-pointer focus:outline-none"
                >
                  Create Workspace
                </button>
              </div>

            </form>
          </div>
        </div>
      )}
    </div>
  );
}
