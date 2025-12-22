import type { AuthMode } from "../App";

const formatIdentity = (mode: AuthMode, email: string | null, guestId: string | null) => {
  if (mode === "google") {
    return email ?? "Google session";
  }
  if (mode === "guest") {
    return guestId ? `Guest ${guestId.slice(0, 8)}` : "Guest";
  }
  return "Not signed in";
};

export default function TopBar({
  mode,
  email,
  guestId,
  onLogout,
}: {
  mode: AuthMode;
  email: string | null;
  guestId: string | null;
  onLogout: () => void;
}) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-4">
      <div>
        <h1 className="text-3xl font-semibold text-ink">Agentic Patient</h1>
      </div>
      <div className="flex items-center gap-3 rounded-full border border-white/70 bg-white/70 px-4 py-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted">{mode}</span>
        <span className="text-sm font-semibold text-ink">{formatIdentity(mode, email, guestId)}</span>
        <button className="btn-ghost" onClick={onLogout}>
          Log out
        </button>
      </div>
    </header>
  );
}
