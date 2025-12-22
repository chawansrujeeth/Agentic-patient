import { useEffect, useRef } from "react";

export default function CaseAutoStart({
  enabled,
  onStart,
}: {
  enabled: boolean;
  onStart: () => void;
}) {
  const hasStarted = useRef(false);

  useEffect(() => {
    if (!enabled || hasStarted.current) {
      return;
    }
    hasStarted.current = true;
    onStart();
  }, [enabled, onStart]);

  return null;
}
