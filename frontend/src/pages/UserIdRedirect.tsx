import { useEffect, useMemo, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../App";
import LoadingIndicator from "../components/LoadingIndicator";
import { resolveUsernameByUserId } from "../lib/api";
import type { ApiAuth } from "../lib/api";

export default function UserIdRedirect() {
  const { auth, authReady } = useAuth();
  const params = useParams();
  const navigate = useNavigate();
  const userId = (params.userId ?? "").trim();

  const apiAuth: ApiAuth = useMemo(
    () => ({ mode: auth.mode, accessToken: auth.accessToken, guestId: auth.guestId }),
    [auth.mode, auth.accessToken, auth.guestId]
  );

  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!authReady || auth.mode === "none") return;
    if (!userId) {
      setError("Missing user id.");
      return;
    }
    let cancelled = false;
    const run = async () => {
      try {
        const resolved = await resolveUsernameByUserId(apiAuth, userId);
        if (cancelled) return;
        navigate(`/u/${encodeURIComponent(resolved.username)}`, { replace: true });
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "User not found.");
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [apiAuth, auth.mode, authReady, navigate, userId]);

  if (!authReady) {
    return (
      <div className="min-h-screen px-6 py-16">
        <div className="mx-auto max-w-4xl">
          <div className="glass-panel p-8 text-center">
            <LoadingIndicator label="Loading profile..." />
          </div>
        </div>
      </div>
    );
  }

  if (auth.mode === "none") return <Navigate to="/" replace />;

  return (
    <div className="min-h-screen px-6 py-16">
      <div className="mx-auto max-w-4xl">
        <div className="glass-panel p-8 text-center">
          {error ? <div className="text-sm text-rose-600">{error}</div> : <LoadingIndicator label="Redirecting..." />}
        </div>
      </div>
    </div>
  );
}

