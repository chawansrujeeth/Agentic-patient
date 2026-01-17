import { NavLink } from "react-router-dom";
import type { AuthMode } from "../App";

const emailToUsername = (email: string) => {
  const trimmed = email.trim();
  const at = trimmed.indexOf("@");
  const candidate = (at >= 0 ? trimmed.slice(0, at) : trimmed).trim();
  return candidate || "User";
};

const formatIdentity = (mode: AuthMode, email: string | null, guestId: string | null) => {
  if (mode === "google") {
    return email ? emailToUsername(email) : "Google user";
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
  myUsername,
  onLogout,
}: {
  mode: AuthMode;
  email: string | null;
  guestId: string | null;
  myUsername?: string | null;
  onLogout: () => void;
}) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-border bg-card px-4 py-3 shadow-sm">
      <div className="flex flex-wrap items-center gap-4">
        <h1 className="text-xl font-semibold text-ink">Agentic Patient</h1>
        <nav className="flex flex-wrap items-center gap-2">
          <NavLink
            to="/problemset"
            className={({ isActive }) =>
              `btn-ghost ${isActive ? "bg-slate-100 text-ink" : ""}`
            }
          >
            Problem Set
          </NavLink>
          {myUsername ? (
            <NavLink
              to={`/u/${encodeURIComponent(myUsername)}`}
              className={({ isActive }) =>
                `btn-ghost ${isActive ? "bg-slate-100 text-ink" : ""}`
              }
            >
              My Profile
            </NavLink>
          ) : null}
        </nav>
      </div>
      <div className="flex items-center gap-3 rounded-md border border-border bg-card px-3 py-2">
        <span className="text-xs font-semibold text-muted">{mode}</span>
        <span className="text-sm font-semibold text-ink">{formatIdentity(mode, email, guestId)}</span>
        <button className="btn-secondary" onClick={onLogout}>
          Log out
        </button>
      </div>
    </header>
  );
}
