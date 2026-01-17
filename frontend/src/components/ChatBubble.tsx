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
        className={`max-w-[75%] rounded-lg border px-4 py-3 text-sm leading-relaxed shadow-sm ${
          patient
            ? "border-border bg-card text-ink"
            : "border-accent/30 bg-accent text-white"
        }`}
      >
        <div className={`mb-2 text-[0.72rem] font-semibold ${patient ? "text-muted" : "text-white/80"}`}>
          {patient ? "Patient" : "You"}
        </div>
        <div className="whitespace-pre-wrap">{message.content}</div>
      </div>
    </div>
  );
}
