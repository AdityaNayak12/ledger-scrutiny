import React, { useState, useEffect, useRef } from "react";
import { INITIAL_MOCK_ENTITIES, MOCK_EXCEPTIONS } from "./mockData";
import type { Entity, Exception } from "./mockData";

const BASE_URL = "http://localhost:8000";

const formatPeriodLabel = (p: { period_start: string; period_end: string }) => {
  const startYear = p.period_start.split("-")[0];
  const endYear = p.period_end.split("-")[0];
  const startYrNum = parseInt(startYear);
  const endYrNum = parseInt(endYear);
  if (!isNaN(startYrNum) && !isNaN(endYrNum)) {
    return `FY ${startYear}-${String(endYrNum).substring(2)}`;
  }
  return `${p.period_start} to ${p.period_end}`;
};

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
    materiality_threshold: "15000",
  });
  const [gstinLookup, setGstinLookup] = useState<string>("");
  const [isLookingUpGstin, setIsLookingUpGstin] = useState<boolean>(false);

  // Period management states
  const [periods, setPeriods] = useState<{ period_start: string; period_end: string }[]>([]);
  const [selectedPeriod, setSelectedPeriod] = useState<{ period_start: string; period_end: string } | null>(null);
  const [showAddPeriodModal, setShowAddPeriodModal] = useState<boolean>(false);
  const [newPeriodDates, setNewPeriodDates] = useState({
    start: "2026-04-01",
    end: "2027-03-31"
  });
  const [hasRunScrutiny, setHasRunScrutiny] = useState<boolean>(false);

  useEffect(() => {
    setHasRunScrutiny(false);
  }, [selectedPeriod]);

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

  // Fetch periods when selected entity changes
  useEffect(() => {
    if (selectedEntityId !== null) {
      fetchPeriods(selectedEntityId);
    } else {
      setPeriods([]);
      setSelectedPeriod(null);
      setExceptions([]);
    }
  }, [selectedEntityId, isMock]);

  // Fetch exceptions when selected period changes
  useEffect(() => {
    if (selectedEntityId !== null && selectedPeriod !== null) {
      fetchExceptions(selectedEntityId, selectedPeriod.period_start, selectedPeriod.period_end);
    } else {
      setExceptions([]);
    }
  }, [selectedEntityId, selectedPeriod, isMock]);

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

  const handleGstinLookup = async () => {
    setErrorMsg(null);
    const cleaned = gstinLookup.trim().toUpperCase();
    if (!cleaned) return;

    setIsLookingUpGstin(true);
    try {
      if (isMock) {
        // Validate GSTIN format in mock mode
        const gstinRegex = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/;
        if (!gstinRegex.test(cleaned)) {
          throw new Error("Invalid GSTIN format. E.g. 27AAAAA1111A1Z1");
        }

        const pan = cleaned.substring(2, 12);
        
        const mockMap: Record<string, string> = {
          "27AAAAA1111A1Z1": "Acme Industrial Solutions Pvt Ltd",
          "07BBBBB2222B2Z2": "Capital Trading Corporation",
          "29CCCCC3333C3Z3": "Bangalore Tech Ventures LLC",
        };
        
        const companyName = mockMap[cleaned] || `${pan.substring(0, 5)} Enterprises Pvt Ltd`;
        
        setNewEntity((prev) => ({
          ...prev,
          name: companyName,
        }));
      } else {
        const res = await fetch(`${BASE_URL}/gstin/lookup`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ gstin: cleaned }),
        });
        if (!res.ok) {
          const errData = await res.json();
          throw new Error(errData.detail || "GSTIN lookup failed");
        }
        const data = await res.json();
        setNewEntity((prev) => ({
          ...prev,
          name: data.company_name,
        }));
      }
    } catch (err: any) {
      setErrorMsg(err.message);
    } finally {
      setIsLookingUpGstin(false);
    }
  };

  const handleCreateEntity = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);
    
    const payload = {
      name: newEntity.name.trim(),
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
        financial_year_start: "2025-04-01",
        financial_year_end: "2026-03-31",
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

  const fetchPeriods = async (entityId: number) => {
    if (isMock) {
      if (entityId === 1) {
        const mockPeriods = [{ period_start: "2025-04-01", period_end: "2026-03-31" }];
        setPeriods(mockPeriods);
        setSelectedPeriod(mockPeriods[0]);
      } else if (entityId === 3) {
        const mockPeriods = [{ period_start: "2024-04-01", period_end: "2025-03-31" }];
        setPeriods(mockPeriods);
        setSelectedPeriod(mockPeriods[0]);
      } else {
        setPeriods([]);
        setSelectedPeriod(null);
      }
    } else {
      try {
        const res = await fetch(`${BASE_URL}/entities/${entityId}/periods`);
        if (!res.ok) throw new Error("Failed to fetch periods");
        const data = await res.json();
        setPeriods(data);
        if (data.length > 0) {
          setSelectedPeriod(data[0]); // Default to the most recent period
        } else {
          setSelectedPeriod(null);
        }
      } catch (err: any) {
        setErrorMsg(`Failed to load periods: ${err.message}`);
        setPeriods([]);
        setSelectedPeriod(null);
      }
    }
  };

  const handleReuploadFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || selectedEntityId === null || !selectedPeriod) return;
    setErrorMsg(null);
    setIsUploading(true);

    if (isMock) {
      setTimeout(() => {
        setIsUploading(false);
        if (fileInputRef.current) fileInputRef.current.value = "";
      }, 1500);
    } else {
      const formData = new FormData();
      formData.append("file", file);
      try {
        const res = await fetch(`${BASE_URL}/entities/${selectedEntityId}/upload?clear_only_period=true&target_period_start=${selectedPeriod.period_start}&target_period_end=${selectedPeriod.period_end}`, {
          method: "POST",
          body: formData,
        });
        if (!res.ok) {
          const errData = await res.json();
          throw new Error(errData.detail || "Failed to upload XML file");
        }
        
        await fetchPeriods(selectedEntityId);
        if (fileInputRef.current) fileInputRef.current.value = "";
      } catch (err: any) {
        setErrorMsg(`Re-upload failed: ${err.message}`);
      } finally {
        setIsUploading(false);
      }
    }
  };

  const handleAddPeriodSubmit = async (file: File) => {
    if (selectedEntityId === null) return;
    setErrorMsg(null);
    setIsUploading(true);

    if (isMock) {
      setTimeout(() => {
        setIsUploading(false);
        setShowAddPeriodModal(false);
        const newP = { period_start: newPeriodDates.start, period_end: newPeriodDates.end };
        const updatedPeriods = [...periods, newP];
        setPeriods(updatedPeriods);
        setSelectedPeriod(newP);
      }, 1500);
    } else {
      const formData = new FormData();
      formData.append("file", file);
      try {
        const res = await fetch(`${BASE_URL}/entities/${selectedEntityId}/upload?clear_only_period=true&target_period_start=${newPeriodDates.start}&target_period_end=${newPeriodDates.end}`, {
          method: "POST",
          body: formData,
        });
        if (!res.ok) {
          const errData = await res.json();
          throw new Error(errData.detail || "Failed to upload XML file");
        }
        
        setShowAddPeriodModal(false);
        await fetchPeriods(selectedEntityId);
      } catch (err: any) {
        setErrorMsg(`Failed to add period: ${err.message}`);
      } finally {
        setIsUploading(false);
      }
    }
  };

  const handleTriggerScrutiny = async () => {
    if (selectedEntityId === null || !selectedPeriod) return;
    setErrorMsg(null);
    setIsScrutinizing(true);

    if (isMock) {
      setTimeout(() => {
        setIsScrutinizing(false);
        const mockExcs = MOCK_EXCEPTIONS[selectedEntityId] || [];
        setExceptions(mockExcs);
        setHasRunScrutiny(true);
      }, 1500);
    } else {
      try {
        const res = await fetch(`${BASE_URL}/entities/${selectedEntityId}/scrutiny-run?period_start=${selectedPeriod.period_start}&period_end=${selectedPeriod.period_end}`, {
          method: "POST",
        });
        if (!res.ok) throw new Error("Scrutiny run failed");
        
        await fetchExceptions(selectedEntityId, selectedPeriod.period_start, selectedPeriod.period_end);
        setHasRunScrutiny(true);
      } catch (err: any) {
        setErrorMsg(`Scrutiny run failed: ${err.message}`);
      } finally {
        setIsScrutinizing(false);
      }
    }
  };

  const fetchExceptions = async (entityId: number, start?: string, end?: string) => {
    setIsLoadingExceptions(true);
    if (isMock) {
      const mockExcs = MOCK_EXCEPTIONS[entityId] || [];
      setExceptions(mockExcs);
      if (mockExcs.length > 0) {
        setHasRunScrutiny(true);
      }
      setIsLoadingExceptions(false);
    } else {
      try {
        let url = `${BASE_URL}/entities/${entityId}/exceptions`;
        if (start && end) {
          url += `?period_start=${start}&period_end=${end}`;
        }
        const res = await fetch(url);
        if (!res.ok) throw new Error("Failed to load exceptions");
        const data = await res.json();
        setExceptions(data);
        if (data.length > 0) {
          setHasRunScrutiny(true);
        }
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
              onClick={() => {
                setGstinLookup("");
                setNewEntity({
                  name: "",
                  materiality_threshold: "15000",
                });
                setShowAddModal(true);
              }}
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
                      <span>Materiality: ₹{ent.materiality_threshold.toLocaleString()}</span>
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
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-slate-400 font-medium">
                    {periods.length > 0 ? (
                      <span className="flex items-center gap-1.5">
                        <svg className="w-4 h-4 text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>
                        Financial Period:
                        <select
                          value={selectedPeriod ? JSON.stringify(selectedPeriod) : ""}
                          onChange={(e) => setSelectedPeriod(e.target.value ? JSON.parse(e.target.value) : null)}
                          className="bg-slate-905 border border-slate-800 rounded-lg text-xs font-bold px-2 py-1 text-slate-200 focus:outline-none focus:border-indigo-500 cursor-pointer ml-1"
                        >
                          {periods.map((p, idx) => (
                            <option key={idx} value={JSON.stringify(p)}>
                              {formatPeriodLabel(p)} ({p.period_start} to {p.period_end})
                            </option>
                          ))}
                        </select>
                      </span>
                    ) : (
                      <span className="text-amber-400 font-semibold flex items-center gap-1">
                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
                        Awaiting XML Ingestion
                      </span>
                    )}
                    <span className="flex items-center gap-1.5">
                      <svg className="w-4 h-4 text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v12a2 2 0 00-2 2z" /></svg>
                      Materiality Threshold: <strong className="text-indigo-400 font-semibold">₹{selectedEntity.materiality_threshold.toLocaleString()}</strong>
                    </span>
                  </div>
                </div>

                {/* Pipeline controls */}
                <div className="flex items-center gap-3">
                  {periods.length === 0 ? (
                    <button
                      onClick={() => {
                        setNewPeriodDates({ start: "2025-04-01", end: "2026-03-31" });
                        setShowAddPeriodModal(true);
                      }}
                      className="bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl px-5 py-2.5 text-sm font-bold transition-all shadow-md shadow-indigo-900/30 flex items-center justify-center gap-2 cursor-pointer focus:outline-none"
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 4v16m8-8H4" /></svg>
                      <span>Add First Period</span>
                    </button>
                  ) : (
                    <div className="flex flex-col sm:flex-row gap-3">
                      {/* Run Scrutiny Pass */}
                      <button
                        onClick={handleTriggerScrutiny}
                        disabled={isScrutinizing || !selectedPeriod}
                        className="bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-800 disabled:text-slate-600 text-white rounded-xl px-4 py-2.5 text-sm font-extrabold tracking-wide uppercase transition-all shadow-lg shadow-indigo-950/50 flex items-center justify-center gap-2 cursor-pointer focus:outline-none"
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

                      {/* Re-upload XML option */}
                      <input
                        type="file"
                        accept=".xml"
                        ref={fileInputRef}
                        onChange={handleReuploadFile}
                        className="hidden"
                      />
                      <button
                        onClick={() => {
                          if (window.confirm(`Warning: Re-uploading will overwrite all existing snapshot and transaction data for the selected period (${selectedPeriod ? formatPeriodLabel(selectedPeriod) : ""}). This cannot be undone. Do you wish to proceed?`)) {
                            fileInputRef.current?.click();
                          }
                        }}
                        disabled={isUploading || isScrutinizing || !selectedPeriod}
                        className="bg-slate-905 border border-slate-800 hover:border-slate-700 text-slate-300 rounded-xl px-4 py-2.5 text-sm font-bold transition-all flex items-center justify-center gap-2 cursor-pointer focus:outline-none"
                      >
                        {isUploading ? "Uploading..." : "Re-upload XML"}
                      </button>

                      {/* Add New Period */}
                      <button
                        onClick={() => {
                          setNewPeriodDates({ start: "2026-04-01", end: "2027-03-31" });
                          setShowAddPeriodModal(true);
                        }}
                        disabled={isUploading || isScrutinizing}
                        className="bg-slate-905 border border-slate-800 hover:border-slate-700 text-indigo-400 hover:text-indigo-300 rounded-xl px-4 py-2.5 text-sm font-bold transition-all flex items-center justify-center gap-2 cursor-pointer focus:outline-none"
                      >
                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 4v16m8-8H4" /></svg>
                        <span>Add Period</span>
                      </button>
                    </div>
                  )}
                </div>
              </div>

              {/* EXCEPTIONS REPORT WORKSPACE */}
              {periods.length === 0 || !hasRunScrutiny ? (
                <div className="flex-1 bg-slate-950/30 border border-slate-850 rounded-3xl p-12 text-center flex flex-col items-center justify-center">
                  <div className="bg-slate-900 border border-slate-800 text-slate-400 p-5 rounded-full mb-4">
                    {periods.length > 0 ? (
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
                    {periods.length > 0 ? "XML Uploaded Successfully" : "Awaiting Tally XML Upload"}
                  </h3>
                  <p className="text-sm text-slate-400 max-w-sm mb-6 leading-relaxed">
                    {periods.length > 0
                      ? "The ledger data is normalized. Click 'Run Scrutiny Pass' to execute the rule validation pipeline."
                      : "Please upload the client's Tally XML export file to ingest their trial balance and transactions."}
                  </p>
                  
                  {/* Demo Helper Prompt for Tally XML upload */}
                  {periods.length === 0 && (
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
              
              <div className="flex gap-2 items-end">
                <div className="flex-1">
                  <label className="block text-xxs font-extrabold uppercase text-slate-400 tracking-wider mb-1.5">Auto-Fill via GSTIN (Optional)</label>
                  <input
                    type="text"
                    placeholder="e.g. 27AAAAA1111A1Z1"
                    value={gstinLookup}
                    onChange={(e) => setGstinLookup(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-850 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-indigo-500 placeholder-slate-700 font-semibold"
                  />
                </div>
                <button
                  type="button"
                  onClick={handleGstinLookup}
                  disabled={isLookingUpGstin || !gstinLookup.trim()}
                  className="bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-800 disabled:text-slate-500 text-white rounded-xl px-4 py-2.5 text-xs font-extrabold uppercase tracking-wide h-[42px] transition-all cursor-pointer focus:outline-none"
                >
                  {isLookingUpGstin ? "..." : "Lookup"}
                </button>
              </div>

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

      {/* ADD NEW PERIOD MODAL */}
      {showAddPeriodModal && (
        <div className="fixed inset-0 bg-slate-950/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-3xl w-full max-w-md shadow-2xl overflow-hidden animate-fadeIn">
            
            {/* Modal Header */}
            <div className="bg-slate-950 py-4 px-6 border-b border-slate-850 flex justify-between items-center">
              <h2 className="text-base font-bold text-white m-0">Ingest Financial Year Period</h2>
              <button
                onClick={() => setShowAddPeriodModal(false)}
                className="text-slate-400 hover:text-white font-bold text-xl cursor-pointer focus:outline-none"
              >
                &times;
              </button>
            </div>

            {/* Modal Form */}
            <div className="p-6 flex flex-col gap-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xxs font-extrabold uppercase text-slate-400 tracking-wider mb-1.5">Period Start Date</label>
                  <input
                    type="date"
                    required
                    value={newPeriodDates.start}
                    onChange={(e) => setNewPeriodDates({ ...newPeriodDates, start: e.target.value })}
                    className="w-full bg-slate-950 border border-slate-850 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-indigo-500 font-semibold"
                  />
                </div>
                <div>
                  <label className="block text-xxs font-extrabold uppercase text-slate-400 tracking-wider mb-1.5">Period End Date</label>
                  <input
                    type="date"
                    required
                    value={newPeriodDates.end}
                    onChange={(e) => setNewPeriodDates({ ...newPeriodDates, end: e.target.value })}
                    className="w-full bg-slate-950 border border-slate-850 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-indigo-500 font-semibold"
                  />
                </div>
              </div>

              <div>
                <label className="block text-xxs font-extrabold uppercase text-slate-400 tracking-wider mb-1.5">Select Tally XML Export</label>
                <input
                  type="file"
                  accept=".xml"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) {
                      handleAddPeriodSubmit(file);
                    }
                  }}
                  className="w-full text-sm text-slate-400 file:mr-4 file:py-2.5 file:px-4 file:rounded-xl file:border-0 file:text-xs file:font-extrabold file:uppercase file:bg-slate-800 file:text-slate-300 hover:file:bg-slate-700 cursor-pointer"
                />
              </div>

              {/* Modal Actions */}
              <div className="flex gap-3 justify-end mt-4">
                <button
                  type="button"
                  onClick={() => setShowAddPeriodModal(false)}
                  className="bg-slate-950 hover:bg-slate-850 border border-slate-800 text-slate-300 rounded-xl px-4 py-2.5 text-xs font-bold transition-all cursor-pointer focus:outline-none"
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
