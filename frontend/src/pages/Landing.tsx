import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../App";
import { supabase } from "../lib/supabaseClient";
import { safeStorage } from "../lib/safeStorage";

const createGuestId = () => {
  try {
    const cryptoApi = globalThis.crypto;
    if (cryptoApi) {
      if (typeof cryptoApi.randomUUID === "function") {
        return cryptoApi.randomUUID();
      }
      const bytes = new Uint8Array(16);
      cryptoApi.getRandomValues(bytes);
      bytes[6] = (bytes[6] & 0x0f) | 0x40;
      bytes[8] = (bytes[8] & 0x3f) | 0x80;
      const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }
  } catch {
    // fall through to Math.random fallback
  }

  return `guest-${Math.random().toString(16).slice(2, 10)}-${Date.now().toString(16)}`;
};

export default function Landing() {
  const { auth, authReady, setGuestMode } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (authReady && auth.mode !== "none") {
    return <Navigate to="/problemset" replace />;
  }

  const handleGuest = () => {
    const guestId = createGuestId();
    setGuestMode(guestId);
    navigate("/problemset");
  };

  const handleGoogle = async () => {
    setBusy(true);
    setError(null);
    safeStorage.setItem("auth_mode", "google");
    const { error: signInError } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: `${window.location.origin}/problemset`,
      },
    });

    if (signInError) {
      setBusy(false);
      setError(signInError.message);
    }
  };

  return (
    <div className="min-h-screen px-6 py-16">
      <div className="mx-auto flex w-full max-w-4xl flex-col items-center gap-10">
        <div className="text-center">
          <h1 className="text-4xl font-semibold text-ink sm:text-5xl">Agentic Patient</h1>
          <p className="mt-4 max-w-2xl text-base text-muted">
            Start a new virtual visit with a lifelike patient, track each turn, and keep your clinical reasoning sharp.
          </p>
        </div>

        <div className="glass-panel w-full max-w-xl p-10 text-center">
          <h2 className="text-2xl font-semibold text-ink">Choose your session</h2>
          <p className="mt-3 text-sm text-muted">
            Sign in to save progress or hop in instantly as a guest.
          </p>

          <div className="mt-8 grid gap-4">
            <button className="btn-primary" onClick={handleGoogle} disabled={busy}>
              Sign in with Google
            </button>
            <button className="btn-secondary" onClick={handleGuest} disabled={busy}>
              Continue as Guest
            </button>
          </div>

          <div className="mt-6 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-700">
            Note: The backend can go idle on Render. If the first request fails, wait a moment for it to wake up,
            then refresh the page.
          </div>

          {error ? <p className="mt-4 text-sm text-rose-500">{error}</p> : null}
        </div>
      </div>
    </div>
  );
}
