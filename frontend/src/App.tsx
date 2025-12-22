import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { supabase } from "./lib/supabaseClient";
import Landing from "./pages/Landing";
import Chat from "./pages/Chat";

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
  const raw = localStorage.getItem(AUTH_MODE_KEY);
  if (raw === "guest" || raw === "google") {
    return raw;
  }
  return null;
};

const resolveAuthState = (session: { access_token?: string; user?: { email?: string } } | null): AuthState => {
  const storedMode = readStoredMode();
  const guestId = localStorage.getItem(GUEST_ID_KEY);

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

  useEffect(() => {
    let isMounted = true;

    const syncSession = async () => {
      const { data } = await supabase.auth.getSession();
      if (!isMounted) return;
      if (data.session?.access_token) {
        localStorage.setItem(AUTH_MODE_KEY, "google");
      }
      setAuth(resolveAuthState(data.session));
      setAuthReady(true);
    };

    syncSession();

    const { data: listener } = supabase.auth.onAuthStateChange((_event, session) => {
      if (session?.access_token) {
        localStorage.setItem(AUTH_MODE_KEY, "google");
      }
      setAuth(resolveAuthState(session));
      setAuthReady(true);
    });

    return () => {
      isMounted = false;
      listener.subscription.unsubscribe();
    };
  }, []);

  const setGuestMode = (guestId: string) => {
    localStorage.setItem(GUEST_ID_KEY, guestId);
    localStorage.setItem(AUTH_MODE_KEY, "guest");
    setAuth({
      mode: "guest",
      accessToken: null,
      guestId,
      email: null,
    });
  };

  const signOut = async () => {
    if (auth.mode === "guest") {
      localStorage.removeItem(GUEST_ID_KEY);
      localStorage.removeItem(AUTH_MODE_KEY);
      setAuth(EMPTY_AUTH);
      return;
    }

    if (auth.mode === "google") {
      await supabase.auth.signOut();
      localStorage.removeItem(AUTH_MODE_KEY);
      setAuth(EMPTY_AUTH);
    }
  };

  const contextValue = useMemo(
    () => ({ auth, authReady, setGuestMode, signOut }),
    [auth, authReady]
  );

  return (
    <AuthContext.Provider value={contextValue}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthContext.Provider>
  );
}
