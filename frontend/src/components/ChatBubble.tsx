import type { ChatMessage } from "../lib/api";

const isPatientRole = (role: string) => {
  const normalized = role.toLowerCase();
  return normalized.includes("patient") || normalized.includes("assistant");
};

export default function ChatBubble({ message }: { message: ChatMessage }) {
  const patient = isPatientRole(message.role || "patient");

  return (
    <div className={`flex ${patient ? "justify-start" : "justify-end"} animate-rise`}>
      <div
        className={`max-w-[75%] rounded-3xl border border-ink/80 px-5 py-4 text-sm leading-relaxed shadow-lg transition-shadow ${
          patient
            ? "bg-gradient-to-br from-white via-white to-amber-50/80 text-ink"
            : "bg-gradient-to-br from-emerald-500/90 via-emerald-600/90 to-emerald-700/90 text-white"
        }`}
      >
        <div className={`mb-2 text-[0.7rem] uppercase tracking-[0.24em] ${patient ? "text-muted" : "text-emerald-100"}`}>
          {patient ? "Patient" : "You"}
        </div>
        <div className="whitespace-pre-wrap">{message.content}</div>
      </div>
    </div>
  );
}
