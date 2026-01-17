export default function LoadingIndicator({ label = "Patient is responding..." }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 rounded-md border border-border bg-card px-4 py-3 text-sm text-muted shadow-sm">
      <span className="relative flex h-3 w-3">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent/60" />
        <span className="relative inline-flex h-3 w-3 rounded-full bg-accent" />
      </span>
      <span>{label}</span>
    </div>
  );
}
