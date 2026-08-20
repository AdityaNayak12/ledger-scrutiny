import React, { useState, useEffect, useRef } from "react";
import { INITIAL_MOCK_ENTITIES, MOCK_EXCEPTIONS } from "./mockData";
import type { Entity, Exception } from "./mockData";
import XlsxUploadModal from "./components/XlsxUploadModal";

declare global {
  interface Window {
    google?: any;
  }
}

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

function AuthScreen({
  baseUrl,
  onSuccess,
}: {
  baseUrl: string;
  onSuccess: (token: string, user: { email: string; organization_name: string }) => void;
}) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [organizationName, setOrganizationName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Pending Google Registration state (when 422 requires organization_name)
  const [pendingGoogleToken, setPendingGoogleToken] = useState<string | null>(null);
  const [googleOrgNamePrompt, setGoogleOrgNamePrompt] = useState("");

  const googleClientId = import.meta.env.VITE_GOOGLE_CLIENT_ID || "";

  const handleGoogleCredentialResponse = async (response: any) => {
    if (!response?.credential) return;
    setAuthError(null);
    setIsSubmitting(true);
    const idToken = response.credential;

    try {
      const res = await fetch(`${baseUrl}/auth/google`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id_token: idToken,
        }),
      });

      if (res.status === 422) {
        // New Google account requires an Organization Name
        setPendingGoogleToken(idToken);
        setAuthError(null);
        setIsSubmitting(false);
        return;
      }

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || "Google authentication failed");
      }

      const data = await res.json();
      onSuccess(data.access_token, {
        email: data.email,
        organization_name: data.organization_name,
      });
    } catch (err: any) {
      setAuthError(err.message);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleGoogleOrgSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!pendingGoogleToken || !googleOrgNamePrompt.trim()) return;

    setAuthError(null);
    setIsSubmitting(true);

    try {
      const res = await fetch(`${baseUrl}/auth/google`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id_token: pendingGoogleToken,
          organization_name: googleOrgNamePrompt.trim(),
        }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || "Google registration failed");
      }

      const data = await res.json();
      setPendingGoogleToken(null);
      onSuccess(data.access_token, {
        email: data.email,
        organization_name: data.organization_name,
      });
    } catch (err: any) {
      setAuthError(err.message);
    } finally {
      setIsSubmitting(false);
    }
  };

  useEffect(() => {
    if (googleClientId && window.google?.accounts?.id && !pendingGoogleToken) {
      try {
        window.google.accounts.id.initialize({
          client_id: googleClientId,
          callback: handleGoogleCredentialResponse,
        });

        const btnContainer = document.getElementById("google-signin-btn");
        if (btnContainer) {
          btnContainer.innerHTML = "";
          window.google.accounts.id.renderButton(btnContainer, {
            theme: "outline",
            size: "large",
            width: 380,
            text: mode === "login" ? "signin_with" : "signup_with",
            shape: "rectangular",
          });
        }
      } catch (e) {
        console.error("GIS initialization error:", e);
      }
    }
  }, [googleClientId, mode, pendingGoogleToken]);

  const handleLoginSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setAuthError(null);
    setIsSubmitting(true);

    try {
      const res = await fetch(`${baseUrl}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), password }),
      });

      if (res.status === 401) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || "Invalid credentials");
      }

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || "Login failed");
      }

      const data = await res.json();
      onSuccess(data.access_token, {
        email: data.email,
        organization_name: data.organization_name,
      });
    } catch (err: any) {
      setAuthError(err.message);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleRegisterSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setAuthError(null);

    if (password !== confirmPassword) {
      setAuthError("Passwords do not match");
      return;
    }

    setIsSubmitting(true);

    try {
      const res = await fetch(`${baseUrl}/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          organization_name: organizationName.trim(),
          email: email.trim(),
          password,
        }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || "Registration failed");
      }

      const data = await res.json();
      onSuccess(data.access_token, {
        email: data.email,
        organization_name: data.organization_name,
      });
    } catch (err: any) {
      setAuthError(err.message);
    } finally {
      setIsSubmitting(false);
    }
  };

  // If new Google account needs Organization Name, render prompt view
  if (pendingGoogleToken) {
    return (
      <div className="flex-1 flex items-center justify-center p-6 bg-slate-900">
        <div className="w-full max-w-md bg-slate-950/80 border border-indigo-500/40 rounded-2xl p-8 shadow-2xl backdrop-blur-xl">
          <div className="text-center mb-6">
            <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-indigo-600/20 border border-indigo-500/30 text-indigo-400 mb-4 shadow-lg">
              <svg className="w-7 h-7" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
              </svg>
            </div>
            <h2 className="text-xl font-bold text-white tracking-tight">Complete Google Registration</h2>
            <p className="text-xs text-slate-400 mt-2">
              Welcome! Please enter your CA Firm / Organization Name to finish setting up your account.
            </p>
          </div>

          {authError && (
            <div className="mb-4 p-3 bg-rose-950/60 border border-rose-800 text-rose-300 text-xs rounded-xl flex items-center gap-2">
              <span>{authError}</span>
            </div>
          )}

          <form onSubmit={handleGoogleOrgSubmit} className="space-y-4">
            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1.5 uppercase tracking-wider">
                CA Firm / Organization Name *
              </label>
              <input
                type="text"
                required
                value={googleOrgNamePrompt}
                onChange={(e) => setGoogleOrgNamePrompt(e.target.value)}
                placeholder="e.g. Shah & Mehta Chartered Accountants"
                className="w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
              />
            </div>

            <div className="flex gap-3">
              <button
                type="button"
                onClick={() => setPendingGoogleToken(null)}
                className="w-1/3 bg-slate-800 hover:bg-slate-700 text-slate-300 font-semibold rounded-xl py-3 text-xs transition-colors cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={isSubmitting}
                className="w-2/3 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold rounded-xl py-3 text-xs transition-all shadow-lg cursor-pointer"
              >
                {isSubmitting ? "Completing..." : "Complete Registration"}
              </button>
            </div>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex items-center justify-center p-6 bg-slate-900">
      <div className="w-full max-w-md bg-slate-950/80 border border-slate-800/90 rounded-2xl p-8 shadow-2xl backdrop-blur-xl">
        
        {/* HEADER ICON & TITLE */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-indigo-600/20 border border-indigo-500/30 text-indigo-400 mb-4 shadow-lg shadow-indigo-950/50">
            <svg className="w-7 h-7" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
          </div>
          <h2 className="text-2xl font-bold text-white tracking-tight">
            {mode === "login" ? "Sign In to Firm Workspace" : "Register Your CA Firm"}
          </h2>
          <p className="text-xs text-slate-400 mt-2 leading-relaxed">
            {mode === "login"
              ? "Access your firm's multi-tenant client audit workspaces and pre-audit scrutiny engine"
              : "Set up a new organization workspace to start analyzing Tally exports"}
          </p>
        </div>

        {/* ERROR ALERT BANNER */}
        {authError && (
          <div className="mb-6 p-3.5 bg-rose-950/60 border border-rose-800/80 text-rose-300 text-xs rounded-xl flex items-center gap-2.5">
            <svg className="w-4 h-4 text-rose-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span>{authError}</span>
          </div>
        )}

        {/* GOOGLE SIGN-IN BUTTON CONTAINER */}
        <div className="mb-6">
          {googleClientId ? (
            <div id="google-signin-btn" className="flex justify-center w-full min-h-[44px]"></div>
          ) : (
            <div className="p-3 bg-slate-900/60 border border-slate-800 rounded-xl text-center text-xs text-slate-500">
              Set <code className="text-indigo-400">VITE_GOOGLE_CLIENT_ID</code> in <code className="text-indigo-400">frontend/.env</code> to enable "Continue with Google"
            </div>
          )}

          <div className="relative my-6 text-center">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-slate-800"></div>
            </div>
            <span className="relative bg-slate-950 px-4 text-xxs font-semibold uppercase tracking-wider text-slate-500">
              Or continue with email
            </span>
          </div>
        </div>

        {/* FORM */}
        <form onSubmit={mode === "login" ? handleLoginSubmit : handleRegisterSubmit} className="space-y-4">
          {mode === "register" && (
            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1.5 uppercase tracking-wider">
                CA Firm / Organization Name
              </label>
              <input
                type="text"
                required
                value={organizationName}
                onChange={(e) => setOrganizationName(e.target.value)}
                placeholder="e.g. Acme & Co. Chartered Accountants"
                className="w-full bg-slate-900 border border-slate-700/80 rounded-xl px-4 py-3 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
              />
            </div>
          )}

          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1.5 uppercase tracking-wider">
              Email Address
            </label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="auditor@cafirm.com"
              className="w-full bg-slate-900 border border-slate-700/80 rounded-xl px-4 py-3 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1.5 uppercase tracking-wider">
              Password
            </label>
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••••••"
              className="w-full bg-slate-900 border border-slate-700/80 rounded-xl px-4 py-3 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
            />
          </div>

          {mode === "register" && (
            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1.5 uppercase tracking-wider">
                Confirm Password
              </label>
              <input
                type="password"
                required
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="••••••••••••"
                className="w-full bg-slate-900 border border-slate-700/80 rounded-xl px-4 py-3 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
              />
            </div>
          )}

          <button
            type="submit"
            disabled={isSubmitting}
            className="w-full mt-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-800/50 text-white font-semibold rounded-xl py-3 text-sm transition-all shadow-lg shadow-indigo-950/60 cursor-pointer flex items-center justify-center gap-2"
          >
            {isSubmitting ? (
              <>
                <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
                <span>Please wait...</span>
              </>
            ) : mode === "login" ? (
              "Sign In to Workspace"
            ) : (
              "Register Firm & Continue"
            )}
          </button>
        </form>

        {/* MODE TOGGLE LINK */}
        <div className="mt-6 pt-6 border-t border-slate-800/80 text-center">
          {mode === "login" ? (
            <p className="text-xs text-slate-400">
              Don't have a firm account?{" "}
              <button
                type="button"
                onClick={() => {
                  setMode("register");
                  setAuthError(null);
                }}
                className="text-indigo-400 hover:text-indigo-300 font-semibold transition-colors cursor-pointer"
              >
                Register CA Firm
              </button>
            </p>
          ) : (
            <p className="text-xs text-slate-400">
              Already registered?{" "}
              <button
                type="button"
                onClick={() => {
                  setMode("login");
                  setAuthError(null);
                }}
                className="text-indigo-400 hover:text-indigo-300 font-semibold transition-colors cursor-pointer"
              >
                Log In
              </button>
            </p>
          )}
        </div>

      </div>
    </div>
  );
}

export default function App() {
  const [isMock, setIsMock] = useState<boolean>(() => {
    const saved = localStorage.getItem("isMockMode");
    return saved !== null ? saved === "true" : true;
  });

  const [token, setToken] = useState<string | null>(() => {
    return localStorage.getItem("ledger_scrutiny_token");
  });

  const [user, setUser] = useState<{ email: string; organization_name: string } | null>(() => {
    const saved = localStorage.getItem("ledger_scrutiny_user");
    return saved ? JSON.parse(saved) : null;
  });

  const isAuthenticated = Boolean(token);

  const handleLogout = () => {
    localStorage.removeItem("ledger_scrutiny_token");
    localStorage.removeItem("ledger_scrutiny_user");
    setToken(null);
    setUser(null);
    setSelectedEntityId(null);
    setEntities([]);
    setPeriods([]);
    setSelectedPeriod(null);
    setExceptions([]);
    setSelectedException(null);
  };

  const handleAuthSuccess = (newToken: string, userInfo: { email: string; organization_name: string }) => {
    localStorage.setItem("ledger_scrutiny_token", newToken);
    localStorage.setItem("ledger_scrutiny_user", JSON.stringify(userInfo));
    setToken(newToken);
    setUser(userInfo);
  };

  const authFetch = async (url: string, options: RequestInit = {}) => {
    const headers = new Headers(options.headers || {});
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }

    const res = await fetch(url, {
      ...options,
      headers,
    });

    if (res.status === 401 && !isMock) {
      handleLogout();
      throw new Error("Session expired or invalid credentials. Please log in again.");
    }

    return res;
  };

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
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [sortBy, setSortBy] = useState<string>("severity");

  // Review Drawer State
  const [selectedException, setSelectedException] = useState<Exception | null>(null);
  const [noteText, setNoteText] = useState<string>("");
  const [updatingExcId, setUpdatingExcId] = useState<number | null>(null);

  // Modals & Forms
  const [showAddModal, setShowAddModal] = useState<boolean>(false);
  const [newEntity, setNewEntity] = useState({
    name: "",
    materiality_threshold: "15000",
  });
  const [gstinLookup, setGstinLookup] = useState<string>("");
  const [isLookingUpGstin, setIsLookingUpGstin] = useState<boolean>(false);

  // Period management states
  const [periods, setPeriods] = useState<{ period_start: string; period_end: string; source?: string }[]>([]);
  const [selectedPeriod, setSelectedPeriod] = useState<{ period_start: string; period_end: string; source?: string } | null>(null);
  const [showAddPeriodModal, setShowAddPeriodModal] = useState<boolean>(false);
  const [uploadSource, setUploadSource] = useState<"tally_xml" | "xlsx" | "sap" | null>(null);
  const [showXlsxModal, setShowXlsxModal] = useState<boolean>(false);
  const [xlsxInitialPeriod, setXlsxInitialPeriod] = useState<{start: string, end: string} | null>(null);
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
    if (isMock || token) {
      fetchEntities();
    }
  }, [isMock, token]);

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
        const res = await authFetch(`${BASE_URL}/entities`);
        if (!res.ok) throw new Error("Failed to fetch entities from server");
        const data = await res.json();
        const enriched = data.map((e: any) => ({
          ...e,
          has_uploaded: true,
          scrutinized: true,
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
        const res = await authFetch(`${BASE_URL}/gstin/lookup`, {
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
      setNewEntity({
        name: "",
        materiality_threshold: "15000",
      });
    } else {
      try {
        const res = await authFetch(`${BASE_URL}/entities`, {
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
        const res = await authFetch(`${BASE_URL}/entities/${entityId}/periods`);
        if (!res.ok) throw new Error("Failed to fetch periods");
        const data = await res.json();
        setPeriods(data);
        if (data.length > 0) {
          setSelectedPeriod(data[0]);
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
        const res = await authFetch(`${BASE_URL}/entities/${selectedEntityId}/upload?clear_only_period=true&target_period_start=${selectedPeriod.period_start}&target_period_end=${selectedPeriod.period_end}`, {
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
        setUploadSource(null);
        const newP = { period_start: newPeriodDates.start, period_end: newPeriodDates.end };
        const updatedPeriods = [...periods, newP];
        setPeriods(updatedPeriods);
        setSelectedPeriod(newP);
      }, 1500);
    } else {
      const formData = new FormData();
      formData.append("file", file);
      try {
        const res = await authFetch(`${BASE_URL}/entities/${selectedEntityId}/upload?clear_only_period=true&target_period_start=${newPeriodDates.start}&target_period_end=${newPeriodDates.end}`, {
          method: "POST",
          body: formData,
        });
        if (!res.ok) {
          const errData = await res.json();
          throw new Error(errData.detail || "Failed to upload XML file");
        }
        
        setShowAddPeriodModal(false);
        setUploadSource(null);
        await fetchPeriods(selectedEntityId);
      } catch (err: any) {
        setErrorMsg(`Failed to add period: ${err.message}`);
        setShowAddPeriodModal(false);
        setUploadSource(null);
      } finally {
        setIsUploading(false);
      }
    }
  };

  const handleXlsxSuccess = async (periodStart: string, periodEnd: string) => {
    if (selectedEntityId === null) return;
    setErrorMsg(null);
    await fetchPeriods(selectedEntityId);
    const newP = { period_start: periodStart, period_end: periodEnd };
    setSelectedPeriod(newP);
    fetchExceptions(selectedEntityId, periodStart, periodEnd);
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
        const res = await authFetch(`${BASE_URL}/entities/${selectedEntityId}/scrutiny-run?period_start=${selectedPeriod.period_start}&period_end=${selectedPeriod.period_end}`, {
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

  const handleDeleteEntity = async () => {
    if (selectedEntityId === null) return;
    const entity = entities.find((e) => e.id === selectedEntityId);
    if (!entity) return;
    
    if (
      !window.confirm(
        `Are you sure you want to permanently delete the client workspace "${entity.name}"? This will delete all financial periods, XML snapshots, transactions, and audit exceptions. This action cannot be undone.`
      )
    ) {
      return;
    }

    if (isMock) {
      setEntities((prev) => prev.filter((e) => e.id !== selectedEntityId));
      setSelectedEntityId(null);
      setPeriods([]);
      setSelectedPeriod(null);
      setExceptions([]);
      setSelectedException(null);
    } else {
      try {
        const res = await authFetch(`${BASE_URL}/entities/${selectedEntityId}`, {
          method: "DELETE",
        });
        if (!res.ok) throw new Error("Failed to delete client");
        setEntities((prev) => prev.filter((e) => e.id !== selectedEntityId));
        setSelectedEntityId(null);
        setPeriods([]);
        setSelectedPeriod(null);
        setExceptions([]);
        setSelectedException(null);
      } catch (err: any) {
        setErrorMsg(`Failed to delete client: ${err.message}`);
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
        const res = await authFetch(url);
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

  const updateExceptionStatus = async (exceptionId: number, status: string, notes: string | null) => {
    if (selectedEntityId === null) return;
    setUpdatingExcId(exceptionId);
    if (isMock) {
      setExceptions((prev) =>
        prev.map((exc) =>
          exc.id === exceptionId
            ? { ...exc, status: status as any, auditor_notes: notes }
            : exc
        )
      );
      setSelectedException((prev) =>
        prev && prev.id === exceptionId
          ? { ...prev, status: status as any, auditor_notes: notes }
          : prev
      );
      setUpdatingExcId(null);
    } else {
      try {
        const res = await authFetch(`${BASE_URL}/entities/${selectedEntityId}/exceptions/${exceptionId}`, {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            status: status,
            auditor_notes: notes,
          }),
        });
        if (!res.ok) throw new Error("Failed to update exception review state");
        const updated = await res.json();
        setExceptions((prev) =>
          prev.map((exc) => (exc.id === exceptionId ? updated : exc))
        );
        setSelectedException((prev) =>
          prev && prev.id === exceptionId ? updated : prev
        );
      } catch (err: any) {
        setErrorMsg(`Failed to save review: ${err.message}`);
      } finally {
        setUpdatingExcId(null);
      }
    }
  };

  const selectedEntity = entities.find((e) => e.id === selectedEntityId);

  const severityWeight: Record<string, number> = { critical: 3, error: 3, warning: 2, info: 1 };
  const formatSeverity = (sev: string) => (sev?.toLowerCase() === "error" ? "CRITICAL" : sev?.toUpperCase() || "");

  const processedExceptions = exceptions
    .filter((exc) => {
      if (severityFilter === "all") return true;
      const s = exc.severity.toLowerCase();
      const f = severityFilter.toLowerCase();
      if (f === "error" || f === "critical") {
        return s === "error" || s === "critical";
      }
      return s === f;
    })
    .filter((exc) => {
      if (statusFilter === "all") return true;
      return exc.status === statusFilter;
    })
    .sort((a, b) => {
      if (sortBy === "severity") {
        const weightA = severityWeight[a.severity] || 0;
        const weightB = severityWeight[b.severity] || 0;
        return weightB - weightA;
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

  if (!isMock && !isAuthenticated) {
    return (
      <div className="min-h-screen bg-slate-900 text-slate-100 flex flex-col font-sans">
        {/* HEADER NAVBAR */}
        <header className="bg-slate-950 border-b border-slate-800 py-4 px-6 flex items-center justify-between shadow-lg">
          <div className="flex items-center gap-3">
            <div className="bg-indigo-600 text-white rounded-lg p-2 font-bold text-lg tracking-wider shadow-md shadow-indigo-900/50">
              LS
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-tight text-white m-0">LedgerScrutiny</h1>
              <p className="text-xs text-indigo-400 font-semibold tracking-wider uppercase">CA Pre-Audit Scrutiny Engine</p>
            </div>
          </div>

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

        {/* AUTH SCREEN WITH GOOGLE GIS INTEGRATION */}
        <AuthScreen baseUrl={BASE_URL} onSuccess={handleAuthSuccess} />
      </div>
    );
  }

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

        {/* RIGHT CONTROLS: Mock Mode + User Badge & Logout */}
        <div className="flex items-center gap-4">
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

          {!isMock && isAuthenticated && (
            <div className="flex items-center gap-3 pl-4 border-l border-slate-800">
              <div className="flex flex-col text-right">
                <span className="text-xs text-slate-400 font-medium">Logged in as</span>
                <span className="text-sm font-semibold text-slate-200 truncate max-w-[180px]">
                  {user?.organization_name || user?.email}
                </span>
              </div>
              <button
                onClick={handleLogout}
                className="px-3.5 py-2 text-xs font-semibold text-slate-300 hover:text-rose-400 bg-slate-900 hover:bg-rose-950/40 border border-slate-800 hover:border-rose-800/60 rounded-xl transition-all cursor-pointer flex items-center gap-1.5 shadow-sm"
                title="Log out of session"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                </svg>
                Log Out
              </button>
            </div>
          )}
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
                  <div className="flex items-center gap-3">
                    <h2 className="text-2xl font-bold text-white tracking-tight m-0">{selectedEntity.name}</h2>
                    <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-indigo-950 border border-indigo-800/60 text-indigo-300">
                      Materiality: ₹{selectedEntity.materiality_threshold.toLocaleString()}
                    </span>
                  </div>
                  <p className="text-xs text-slate-400 mt-1 m-0">Client Workspace ID: #{selectedEntity.id}</p>
                </div>

                {/* ACTION BUTTONS */}
                <div className="flex items-center gap-3">
                  <button
                    onClick={handleDeleteEntity}
                    className="px-3.5 py-2 text-xs font-semibold text-rose-400 hover:text-rose-200 bg-rose-950/40 hover:bg-rose-900/60 border border-rose-800/60 rounded-xl transition-all cursor-pointer flex items-center gap-1.5 shadow-sm"
                  >
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                    </svg>
                    Delete Workspace
                  </button>
                </div>
              </div>

              {/* FINANCIAL PERIOD CONTROL BAR */}
              <div className="bg-slate-950/40 rounded-2xl border border-slate-800 p-4 flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div className="flex items-center gap-3 flex-wrap">
                  <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">Financial Period:</span>
                  
                  {periods.length === 0 ? (
                    <span className="text-xs text-amber-400 bg-amber-950/50 border border-amber-800/60 px-3 py-1 rounded-lg">
                      No trial balance data uploaded yet
                    </span>
                  ) : (
                    <div className="flex items-center gap-2">
                      {periods.map((p, idx) => {
                        const isSelected = selectedPeriod?.period_start === p.period_start && selectedPeriod?.period_end === p.period_end;
                        return (
                          <button
                            key={idx}
                            onClick={() => setSelectedPeriod(p)}
                            className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${
                              isSelected
                                ? "bg-indigo-600 text-white shadow-md shadow-indigo-900/50"
                                : "bg-slate-900 text-slate-400 hover:text-slate-200 border border-slate-800"
                            }`}
                          >
                            {formatPeriodLabel(p)}
                          </button>
                        );
                      })}
                    </div>
                  )}

                  <button
                    onClick={() => setShowAddPeriodModal(true)}
                    className="text-xs font-semibold text-indigo-400 hover:text-indigo-300 bg-indigo-950/40 hover:bg-indigo-900/60 border border-indigo-800/50 px-2.5 py-1.5 rounded-lg transition-all cursor-pointer flex items-center gap-1"
                  >
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                    </svg>
                    Add Period
                  </button>
                </div>

                <div className="flex items-center gap-3">
                  {selectedPeriod && (
                    <>
                      {selectedPeriod.source === "xlsx_trial_balance" ? (
                        <button
                          onClick={() => { setXlsxInitialPeriod({ start: selectedPeriod.period_start, end: selectedPeriod.period_end }); setShowXlsxModal(true); }}
                          className="bg-slate-800 hover:bg-slate-700 text-slate-200 px-3.5 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer border border-slate-700/60 flex items-center gap-1.5"
                        >
                          <svg className="w-3.5 h-3.5 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                          </svg>
                          Re-upload Excel ({formatPeriodLabel(selectedPeriod)})
                        </button>
                      ) : selectedPeriod.source === "sap_gl_dump" ? (
                        <button
                          disabled
                          className="bg-slate-800 text-slate-500 px-3.5 py-2 rounded-xl text-xs font-semibold border border-slate-700/60 flex items-center gap-1.5 cursor-not-allowed opacity-60"
                        >
                          <svg className="w-3.5 h-3.5 text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                          </svg>
                          Re-upload SAP ({formatPeriodLabel(selectedPeriod)})
                        </button>
                      ) : (
                        <label className="bg-slate-800 hover:bg-slate-700 text-slate-200 px-3.5 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer border border-slate-700/60 flex items-center gap-1.5">
                          <svg className="w-3.5 h-3.5 text-indigo-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                          </svg>
                          {isUploading ? "Uploading..." : `Re-upload XML (${formatPeriodLabel(selectedPeriod)})`}
                          <input
                            type="file"
                            accept=".xml"
                            onChange={handleReuploadFile}
                            ref={fileInputRef}
                            disabled={isUploading}
                            className="hidden"
                          />
                        </label>
                      )}

                      <button
                        onClick={handleTriggerScrutiny}
                        disabled={isScrutinizing}
                        className="bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-900 text-white px-4 py-2 rounded-xl text-xs font-bold transition-all cursor-pointer shadow-lg shadow-indigo-950/50 flex items-center gap-1.5"
                      >
                        {isScrutinizing ? (
                          <>
                            <div className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
                            <span>Running Rules...</span>
                          </>
                        ) : (
                          <>
                            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                            <span>Run Scrutiny Rules</span>
                          </>
                        )}
                      </button>
                    </>
                  )}
                </div>
              </div>

              {/* AUDIT EXCEPTIONS SECTION */}
              <div className="flex-1 flex flex-col bg-slate-950/60 rounded-2xl border border-slate-800 p-5 shadow-xl overflow-hidden">
                
                {/* TOOLBAR: FILTERS & SORTING */}
                <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-5 pb-4 border-b border-slate-800/80">
                  <div>
                    <h3 className="text-base font-bold text-white tracking-tight m-0 flex items-center gap-2">
                      <span>Audit Exception Register</span>
                      {selectedPeriod && (
                        <span className="text-xs font-medium text-slate-400 bg-slate-900 px-2.5 py-0.5 rounded-full border border-slate-800">
                          {formatPeriodLabel(selectedPeriod)}
                        </span>
                      )}
                    </h3>
                    <p className="text-xs text-slate-400 mt-0.5 m-0">Flagged anomalies requiring audit review and documentation</p>
                  </div>

                  <div className="flex items-center gap-3 flex-wrap text-xs">
                    
                    {/* Severity Filter */}
                    <div className="flex items-center gap-1.5 bg-slate-900 px-3 py-1.5 rounded-xl border border-slate-800">
                      <span className="text-slate-500 font-semibold uppercase tracking-wider text-xxs">Severity:</span>
                      <select
                        value={severityFilter}
                        onChange={(e) => setSeverityFilter(e.target.value)}
                        className="bg-transparent text-slate-200 font-semibold focus:outline-none cursor-pointer"
                      >
                        <option value="all" className="bg-slate-900 text-slate-200">All Severities</option>
                        <option value="critical" className="bg-slate-900 text-slate-200">Critical Only</option>
                        <option value="warning" className="bg-slate-900 text-slate-200">Warning Only</option>
                        <option value="info" className="bg-slate-900 text-slate-200">Info Only</option>
                      </select>
                    </div>

                    {/* Status Filter */}
                    <div className="flex items-center gap-1.5 bg-slate-900 px-3 py-1.5 rounded-xl border border-slate-800">
                      <span className="text-slate-500 font-semibold uppercase tracking-wider text-xxs">Status:</span>
                      <select
                        value={statusFilter}
                        onChange={(e) => setStatusFilter(e.target.value)}
                        className="bg-transparent text-slate-200 font-semibold focus:outline-none cursor-pointer"
                      >
                        <option value="all" className="bg-slate-900 text-slate-200">All Statuses</option>
                        <option value="PENDING" className="bg-slate-900 text-slate-200">Pending Only</option>
                        <option value="REVIEWED" className="bg-slate-900 text-slate-200">Reviewed Only</option>
                        <option value="CLEARED" className="bg-slate-900 text-slate-200">Cleared Only</option>
                      </select>
                    </div>

                    {/* Sort By */}
                    <div className="flex items-center gap-1.5 bg-slate-900 px-3 py-1.5 rounded-xl border border-slate-800">
                      <span className="text-slate-500 font-semibold uppercase tracking-wider text-xxs">Sort:</span>
                      <select
                        value={sortBy}
                        onChange={(e) => setSortBy(e.target.value)}
                        className="bg-transparent text-slate-200 font-semibold focus:outline-none cursor-pointer"
                      >
                        <option value="severity" className="bg-slate-900 text-slate-200">Severity (High to Low)</option>
                        <option value="account" className="bg-slate-900 text-slate-200">Ledger Account</option>
                        <option value="rule" className="bg-slate-900 text-slate-200">Scrutiny Rule Name</option>
                      </select>
                    </div>
                  </div>
                </div>

                {/* TABLE OF EXCEPTIONS */}
                <div className="flex-1 overflow-y-auto">
                  {isLoadingExceptions ? (
                    <div className="h-48 flex flex-col items-center justify-center text-slate-500 gap-2">
                      <div className="w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
                      <span className="text-xs">Loading exception items...</span>
                    </div>
                  ) : processedExceptions.length === 0 ? (
                    <div className="h-48 flex flex-col items-center justify-center text-slate-500 text-center border-2 border-dashed border-slate-850 rounded-2xl p-6">
                      <svg className="w-10 h-10 text-slate-700 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                      <p className="text-sm font-semibold text-slate-400 m-0">No Audit Exceptions Found</p>
                      <p className="text-xs text-slate-600 mt-1 m-0">
                        {!hasRunScrutiny 
                          ? "Click 'Run Scrutiny Rules' above to analyze ledger trial balances against accounting principles."
                          : "All accounts cleared scrutiny checks without matching any rule exception patterns."}
                      </p>
                    </div>
                  ) : (
                    <table className="w-full text-left border-collapse text-xs">
                      <thead>
                        <tr className="border-b border-slate-800 text-slate-400 font-semibold uppercase tracking-wider text-xxs bg-slate-900/60 sticky top-0 z-10">
                          <th className="py-3 px-4">Severity</th>
                          <th className="py-3 px-4">Ledger Account</th>
                          <th className="py-3 px-4">Scrutiny Rule</th>
                          <th className="py-3 px-4">Exception Description</th>
                          <th className="py-3 px-4">Status</th>
                          <th className="py-3 px-4 text-right">Action</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-850">
                        {processedExceptions.map((exc) => {
                          const isCritical = exc.severity.toLowerCase() === "critical" || exc.severity.toLowerCase() === "error";
                          const isWarning = exc.severity.toLowerCase() === "warning";
                          
                          return (
                            <tr key={exc.id} className="hover:bg-slate-900/50 transition-colors group">
                              
                              {/* Severity Badge */}
                              <td className="py-3 px-4 whitespace-nowrap">
                                <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xxs font-bold uppercase tracking-wider ${
                                  isCritical
                                    ? "bg-rose-950 text-rose-300 border border-rose-800/60"
                                    : isWarning
                                    ? "bg-amber-950 text-amber-300 border border-amber-800/60"
                                    : "bg-blue-950 text-blue-300 border border-blue-800/60"
                                }`}>
                                  {formatSeverity(exc.severity)}
                                </span>
                              </td>

                              {/* Ledger Account */}
                              <td className="py-3 px-4 font-bold text-slate-200 whitespace-nowrap">
                                {exc.ledger_account_name || (
                                  <span className="text-slate-500 italic font-normal">N/A (Entity-wide)</span>
                                )}
                              </td>

                              {/* Rule Name */}
                              <td className="py-3 px-4 font-semibold text-indigo-300 whitespace-nowrap">
                                {exc.rule_name}
                              </td>

                              {/* Message */}
                              <td className="py-3 px-4 text-slate-300 max-w-xs truncate" title={exc.message}>
                                {exc.message}
                              </td>

                              {/* Status Badge */}
                              <td className="py-3 px-4 whitespace-nowrap">
                                <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xxs font-bold ${
                                  exc.status === "CLEARED"
                                    ? "bg-emerald-950 text-emerald-300 border border-emerald-800/60"
                                    : exc.status === "REVIEWED"
                                    ? "bg-indigo-950 text-indigo-300 border border-indigo-800/60"
                                    : "bg-slate-800 text-slate-400 border border-slate-700"
                                }`}>
                                  {exc.status}
                                </span>
                              </td>

                              {/* Action Button */}
                              <td className="py-3 px-4 text-right whitespace-nowrap">
                                <button
                                  onClick={() => {
                                    setSelectedException(exc);
                                    setNoteText(exc.auditor_notes || "");
                                  }}
                                  className="text-xs font-semibold text-indigo-400 hover:text-indigo-300 bg-indigo-950/40 hover:bg-indigo-900/60 border border-indigo-800/50 px-3 py-1 rounded-lg transition-all cursor-pointer"
                                >
                                  Review Workpaper
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  )}
                </div>
              </div>

            </div>
          )}
        </section>
      </main>

      {/* MODAL: ADD CLIENT ENTITY */}
      {showAddModal && (
        <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl max-w-md w-full p-6 shadow-2xl">
            <div className="flex justify-between items-center mb-4">
              <h3 className="text-lg font-bold text-white m-0">Register Client Entity</h3>
              <button onClick={() => setShowAddModal(false)} className="text-slate-400 hover:text-slate-200 font-bold text-xl cursor-pointer">
                &times;
              </button>
            </div>

            {/* GSTIN LOOKUP INTEGRATION */}
            <div className="mb-5 p-4 bg-slate-950/60 rounded-xl border border-slate-800">
              <label className="block text-xs font-semibold text-slate-400 mb-1.5 uppercase tracking-wider">
                GSTIN Lookup (Auto-fill Details)
              </label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={gstinLookup}
                  onChange={(e) => setGstinLookup(e.target.value)}
                  placeholder="e.g. 27AAAAA1111A1Z1"
                  className="flex-1 bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                />
                <button
                  type="button"
                  onClick={handleGstinLookup}
                  disabled={isLookingUpGstin}
                  className="bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-900 text-white px-3 py-2 rounded-xl text-xs font-bold transition-all cursor-pointer"
                >
                  {isLookingUpGstin ? "Looking up..." : "Fetch Company"}
                </button>
              </div>
            </div>

            <form onSubmit={handleCreateEntity} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-slate-300 mb-1">Entity Name *</label>
                <input
                  type="text"
                  required
                  value={newEntity.name}
                  onChange={(e) => setNewEntity({ ...newEntity, name: e.target.value })}
                  placeholder="Acme Industrial Pvt Ltd"
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-xs text-slate-100 focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-300 mb-1">Materiality Threshold (₹)</label>
                <input
                  type="number"
                  value={newEntity.materiality_threshold}
                  onChange={(e) => setNewEntity({ ...newEntity, materiality_threshold: e.target.value })}
                  placeholder="15000"
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-xs text-slate-100 focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowAddModal(false)}
                  className="px-4 py-2 text-xs font-semibold text-slate-400 hover:text-slate-200 bg-slate-800 rounded-xl transition-all cursor-pointer"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-500 rounded-xl transition-all shadow-md shadow-indigo-950/50 cursor-pointer"
                >
                  Create Client
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: ADD FINANCIAL PERIOD */}
      {showAddPeriodModal && selectedEntity && (
        <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl max-w-md w-full p-6 shadow-2xl">
            <div className="flex justify-between items-center mb-4">
              <h3 className="text-lg font-bold text-white m-0">Add Financial Period for {selectedEntity.name}</h3>
              <button onClick={() => { setShowAddPeriodModal(false); setUploadSource(null); }} className="text-slate-400 hover:text-slate-200 font-bold text-xl cursor-pointer">
                &times;
              </button>
            </div>

            {uploadSource === null ? (
              <div className="space-y-3">
                <p className="text-xs text-slate-400 mb-3">Select the data source to import a trial balance:</p>
                
                <button
                  onClick={() => setUploadSource("tally_xml")}
                  className="w-full flex items-center gap-3 p-4 rounded-xl bg-slate-800 hover:bg-slate-700 border border-slate-700 transition-colors cursor-pointer text-left"
                >
                  <div className="p-2 bg-indigo-500/20 text-indigo-400 rounded-lg">
                    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
                    </svg>
                  </div>
                  <div>
                    <div className="font-bold text-slate-200 text-sm">Tally XML Export</div>
                    <div className="text-xs text-slate-500">Import structured XML exported directly from Tally</div>
                  </div>
                </button>
                
                <button
                  onClick={() => {
                    setShowAddPeriodModal(false);
                    setUploadSource(null);
                    setXlsxInitialPeriod(null);
                    setShowXlsxModal(true);
                  }}
                  className="w-full flex items-center gap-3 p-4 rounded-xl bg-slate-800 hover:bg-slate-700 border border-slate-700 transition-colors cursor-pointer text-left"
                >
                  <div className="p-2 bg-emerald-500/20 text-emerald-400 rounded-lg">
                    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                    </svg>
                  </div>
                  <div>
                    <div className="font-bold text-slate-200 text-sm">Excel Trial Balance (XLSX)</div>
                    <div className="text-xs text-slate-500">Import a generic trial balance spreadsheet</div>
                  </div>
                </button>
                
                <button
                  disabled
                  className="w-full flex items-center gap-3 p-4 rounded-xl bg-slate-900/50 border border-slate-800 opacity-60 cursor-not-allowed text-left relative overflow-hidden"
                >
                  <div className="p-2 bg-slate-700/50 text-slate-500 rounded-lg">
                    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4" />
                    </svg>
                  </div>
                  <div>
                    <div className="font-bold text-slate-400 text-sm">SAP GL Dump</div>
                    <div className="text-xs text-slate-500">Import raw SAP general ledger extract</div>
                  </div>
                  <span className="absolute right-4 top-1/2 -translate-y-1/2 text-[10px] font-bold uppercase tracking-wider text-slate-500 bg-slate-800 px-2 py-1 rounded">Coming Soon</span>
                </button>
              </div>
            ) : uploadSource === "tally_xml" ? (
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-semibold text-slate-300 mb-1">Period Start</label>
                    <input
                      type="date"
                      value={newPeriodDates.start}
                      onChange={(e) => setNewPeriodDates({ ...newPeriodDates, start: e.target.value })}
                      className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-indigo-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-slate-300 mb-1">Period End</label>
                    <input
                      type="date"
                      value={newPeriodDates.end}
                      onChange={(e) => setNewPeriodDates({ ...newPeriodDates, end: e.target.value })}
                      className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-indigo-500"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-semibold text-slate-300 mb-1">Tally XML Export File *</label>
                  <input
                    type="file"
                    accept=".xml"
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) {
                        handleAddPeriodSubmit(f);
                        setUploadSource(null);
                      }
                    }}
                    className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2 text-xs text-slate-300 file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-indigo-600 file:text-white hover:file:bg-indigo-500 cursor-pointer"
                  />
                </div>

                <div className="flex justify-between items-center pt-2">
                  <button
                    type="button"
                    onClick={() => setUploadSource(null)}
                    className="px-4 py-2 text-xs font-semibold text-slate-400 hover:text-slate-200 transition-colors cursor-pointer"
                  >
                    &larr; Back
                  </button>
                  <button
                    type="button"
                    onClick={() => { setShowAddPeriodModal(false); setUploadSource(null); }}
                    className="px-4 py-2 text-xs font-semibold text-slate-400 hover:text-slate-200 bg-slate-800 rounded-xl transition-all cursor-pointer"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      )}

      {/* WORKPAPER REVIEW DRAWER */}
      {selectedException && (
        <div className="fixed inset-0 bg-slate-950/70 backdrop-blur-sm z-50 flex justify-end">
          <div className="w-full max-w-xl bg-slate-900 border-l border-slate-800 h-full flex flex-col p-6 shadow-2xl overflow-y-auto">
            <div className="flex justify-between items-center pb-4 border-b border-slate-800 mb-6">
              <div>
                <span className="text-xxs font-bold uppercase tracking-wider text-indigo-400 bg-indigo-950 border border-indigo-800/60 px-2.5 py-0.5 rounded-full">
                  Audit Workpaper Review
                </span>
                <h3 className="text-xl font-bold text-white mt-2 m-0">Exception #{selectedException.id}</h3>
              </div>
              <button
                onClick={() => setSelectedException(null)}
                className="text-slate-400 hover:text-slate-200 text-2xl font-bold focus:outline-none cursor-pointer"
              >
                &times;
              </button>
            </div>

            <div className="space-y-6 flex-1">
              
              {/* DETAILS CARD */}
              <div className="bg-slate-950/60 rounded-xl border border-slate-800 p-4 space-y-3">
                <div className="flex justify-between items-center text-xs">
                  <span className="text-slate-400 font-semibold">Ledger Account:</span>
                  <span className="font-bold text-slate-100">{selectedException.ledger_account_name || "N/A (Entity-wide)"}</span>
                </div>
                <div className="flex justify-between items-center text-xs">
                  <span className="text-slate-400 font-semibold">Rule Triggered:</span>
                  <span className="font-bold text-indigo-300">{selectedException.rule_name}</span>
                </div>
                <div className="flex justify-between items-center text-xs">
                  <span className="text-slate-400 font-semibold">Severity:</span>
                  <span className="font-bold uppercase text-rose-400">
                    {formatSeverity(selectedException.severity)}
                  </span>
                </div>
              </div>

              {/* MESSAGE */}
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">Audit Observation Description</h4>
                <div className="bg-slate-950 border border-slate-800 rounded-xl p-4 text-xs text-slate-200 leading-relaxed font-mono">
                  {selectedException.message}
                </div>
              </div>

              {/* AUDITOR NOTES INPUT */}
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">Auditor Notes & Justification</h4>
                <textarea
                  rows={4}
                  value={noteText}
                  onChange={(e) => setNoteText(e.target.value)}
                  placeholder="Enter auditor review notes, justification, or board resolution details..."
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-xs text-slate-100 placeholder-slate-600 focus:outline-none focus:border-indigo-500 transition-colors"
                />
              </div>

              {/* REVIEW STATUS ACTION BUTTONS */}
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">Update Review State</h4>
                <div className="grid grid-cols-3 gap-3">
                  <button
                    onClick={() => updateExceptionStatus(selectedException.id, "PENDING", noteText)}
                    disabled={updatingExcId === selectedException.id}
                    className={`py-2.5 rounded-xl text-xs font-bold border transition-all cursor-pointer ${
                      selectedException.status === "PENDING"
                        ? "bg-slate-800 border-slate-600 text-slate-100"
                        : "bg-slate-950 border-slate-800 text-slate-400 hover:bg-slate-850"
                    }`}
                  >
                    Set Pending
                  </button>
                  
                  <button
                    onClick={() => updateExceptionStatus(selectedException.id, "REVIEWED", noteText)}
                    disabled={updatingExcId === selectedException.id}
                    className={`py-2.5 rounded-xl text-xs font-bold border transition-all cursor-pointer ${
                      selectedException.status === "REVIEWED"
                        ? "bg-indigo-900/80 border-indigo-600 text-indigo-200"
                        : "bg-slate-950 border-slate-800 text-slate-400 hover:bg-indigo-950/40"
                    }`}
                  >
                    Mark Reviewed
                  </button>

                  <button
                    onClick={() => updateExceptionStatus(selectedException.id, "CLEARED", noteText)}
                    disabled={updatingExcId === selectedException.id}
                    className={`py-2.5 rounded-xl text-xs font-bold border transition-all cursor-pointer ${
                      selectedException.status === "CLEARED"
                        ? "bg-emerald-900/80 border-emerald-600 text-emerald-200"
                        : "bg-slate-950 border-slate-800 text-slate-400 hover:bg-emerald-950/40"
                    }`}
                  >
                    Clear Exception
                  </button>
                </div>
              </div>

            </div>

            <div className="pt-6 border-t border-slate-800 flex justify-end">
              <button
                onClick={() => setSelectedException(null)}
                className="bg-indigo-600 hover:bg-indigo-500 text-white font-bold px-5 py-2.5 rounded-xl text-xs transition-all shadow-lg shadow-indigo-950/50 cursor-pointer"
              >
                Close Workpaper
              </button>
            </div>
          </div>
        </div>
      )}

      {/* XLSX UPLOAD MODAL */}
      <XlsxUploadModal
        isOpen={showXlsxModal}
        onClose={() => setShowXlsxModal(false)}
        entityId={selectedEntityId}
        entityName={selectedEntity?.name || ""}
        baseUrl={BASE_URL}
        authFetch={authFetch}
        onSuccess={handleXlsxSuccess}
        isMock={isMock}
        initialPeriodStart={xlsxInitialPeriod?.start}
        initialPeriodEnd={xlsxInitialPeriod?.end}
      />

    </div>
  );
}
