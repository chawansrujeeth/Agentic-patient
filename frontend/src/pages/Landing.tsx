import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../App";
import { supabase } from "../lib/supabaseClient";

const createGuestId = () => {
  if (typeof crypto !== "undefined") {
    if ("randomUUID" in crypto) {
      return crypto.randomUUID();
    }
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  return `guest-${Math.random().toString(16).slice(2, 10)}-${Date.now().toString(16)}`;
};

export default function Landing() {
  const { auth, authReady, setGuestMode } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (authReady && auth.mode !== "none") {
      navigate("/chat", { replace: true });
    }
  }, [authReady, auth.mode, navigate]);

  const handleGuest = () => {
    const guestId = createGuestId();
    setGuestMode(guestId);
    navigate("/chat");
  };

  const handleGoogle = async () => {
    setBusy(true);
    setError(null);
    localStorage.setItem("auth_mode", "google");
    const { error: signInError } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: `${window.location.origin}/chat`,
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

          {error ? <p className="mt-4 text-sm text-rose-500">{error}</p> : null}
        </div>
      </div>
    </div>
  );
}
