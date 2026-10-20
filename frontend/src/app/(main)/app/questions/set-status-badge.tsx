import { LoaderCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { statusLabel, type SetStatus } from "@/lib/questions";

const VARIANT = {
  GENERATING: "secondary",
  READY: "success",
  FAILED: "destructive",
} as const satisfies Record<SetStatus, string>;

export function SetStatusBadge({ status }: { status: SetStatus }) {
  return (
    <Badge variant={VARIANT[status]}>
      {status === "GENERATING" && <LoaderCircle className="animate-spin" aria-hidden />}
      {statusLabel(status)}
    </Badge>
  );
}
