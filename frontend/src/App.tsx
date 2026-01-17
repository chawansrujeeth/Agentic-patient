import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";
import { supabase } from "./lib/supabaseClient";
import { safeStorage } from "./lib/safeStorage";
import { getMe } from "./lib/api";
import Landing from "./pages/Landing";
import Chat from "./pages/Chat";
import ProblemSet from "./pages/ProblemSet";
import SubmissionViewer from "./pages/SubmissionViewer";
import ProblemWorkspace from "./pages/ProblemWorkspace";
import Profile from "./pages/Profile";
import UserIdRedirect from "./pages/UserIdRedirect";

export type AuthMode = "none" | "guest" | "google";

export type AuthState = {
  mode: AuthMode;
  accessToken: string | null;
  guestId: string | null;
  email: string | null;
};

type AuthContextValue = {
  auth: AuthState;
  authReady: boolean;
  me: { user_id: string; username: string; display_name: string } | null;
  setGuestMode: (guestId: string) => void;
  signOut: () => Promise<void>;
};

const EMPTY_AUTH: AuthState = {
  mode: "none",
  accessToken: null,
  guestId: null,
  email: null,
};

const AUTH_MODE_KEY = "auth_mode";
const GUEST_ID_KEY = "guest_user_id";

const AuthContext = createContext<AuthContextValue | null>(null);

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within AuthContext");
  }
  return context;
};

const readStoredMode = (): AuthMode | null => {
  const raw = safeStorage.getItem(AUTH_MODE_KEY);
  if (raw === "guest" || raw === "google") {
    return raw;
  }
  return null;
};

const resolveAuthState = (session: { access_token?: string; user?: { email?: string } } | null): AuthState => {
  const storedMode = readStoredMode();
  const guestId = safeStorage.getItem(GUEST_ID_KEY);

  if (storedMode === "google" && session?.access_token) {
    return {
      mode: "google",
      accessToken: session.access_token,
      guestId: null,
      email: session.user?.email ?? null,
    };
  }

  if (storedMode === "guest" && guestId) {
    return {
      mode: "guest",
      accessToken: null,
      guestId,
      email: null,
    };
  }

  if (session?.access_token) {
    return {
      mode: "google",
      accessToken: session.access_token,
      guestId: null,
      email: session.user?.email ?? null,
    };
  }

  return EMPTY_AUTH;
};

export default function App() {
  const [auth, setAuth] = useState<AuthState>(EMPTY_AUTH);
  const [authReady, setAuthReady] = useState(false);
  const [me, setMe] = useState<{ user_id: string; username: string; display_name: string } | null>(null);

  useEffect(() => {
    let isMounted = true;

    const syncSession = async () => {
      const { data } = await supabase.auth.getSession();
      if (!isMounted) return;
      if (data.session?.access_token) {
        safeStorage.setItem(AUTH_MODE_KEY, "google");
      }
      setAuth(resolveAuthState(data.session));
      setAuthReady(true);
    };

    syncSession();

    const { data: listener } = supabase.auth.onAuthStateChange((_event, session) => {
      if (session?.access_token) {
        safeStorage.setItem(AUTH_MODE_KEY, "google");
      }
      setAuth(resolveAuthState(session));
      setAuthReady(true);
    });

    return () => {
      isMounted = false;
      listener.subscription.unsubscribe();
    };
  }, []);

  useEffect(() => {
    if (!authReady || auth.mode === "none") {
      setMe(null);
      return;
    }
    let cancelled = false;
    const run = async () => {
      try {
        const response = await getMe({ mode: auth.mode, accessToken: auth.accessToken, guestId: auth.guestId });
        if (!cancelled) {
          setMe({
            user_id: response.user.user_id,
            username: response.user.username,
            display_name: response.user.display_name,
          });
        }
      } catch {
        if (!cancelled) setMe(null);
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [authReady, auth.mode, auth.accessToken, auth.guestId]);

  const setGuestMode = (guestId: string) => {
    safeStorage.setItem(GUEST_ID_KEY, guestId);
    safeStorage.setItem(AUTH_MODE_KEY, "guest");
    setAuth({
      mode: "guest",
      accessToken: null,
      guestId,
      email: null,
    });
  };

  const signOut = async () => {
    if (auth.mode === "guest") {
      safeStorage.removeItem(GUEST_ID_KEY);
      safeStorage.removeItem(AUTH_MODE_KEY);
      setAuth(EMPTY_AUTH);
      return;
    }

    if (auth.mode === "google") {
      await supabase.auth.signOut();
      safeStorage.removeItem(AUTH_MODE_KEY);
      setAuth(EMPTY_AUTH);
    }
  };

  const contextValue = useMemo(
    () => ({ auth, authReady, me, setGuestMode, signOut }),
    [auth, authReady, me]
  );

  return (
    <AuthContext.Provider value={contextValue}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/chat" element={<Navigate to="/problemset" replace />} />
          <Route path="/chat/:caseId" element={<Chat />} />
          <Route path="/problem/:caseId" element={<ProblemWorkspace />} />
          <Route path="/problem/:caseId/submission" element={<ProblemWorkspaceTabRedirect tab="submission" />} />
          <Route path="/problem/:caseId/submissions" element={<ProblemWorkspaceTabRedirect tab="solutions" />} />
          <Route path="/submission/:sessionId" element={<SubmissionViewer />} />
          <Route path="/u/:username" element={<Profile />} />
          <Route path="/user/:userId" element={<UserIdRedirect />} />
          <Route path="/problemset" element={<ProblemSet />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthContext.Provider>
  );
}

function ProblemWorkspaceTabRedirect({ tab }: { tab: string }) {
  const params = useParams();
  const caseId = params.caseId ?? "";
  const safeTab = encodeURIComponent(tab);
  return <Navigate to={`/problem/${encodeURIComponent(caseId)}?tab=${safeTab}`} replace />;
}
