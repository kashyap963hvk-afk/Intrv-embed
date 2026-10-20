import { LoaderCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { statusLabel, type InterviewStatus } from "@/lib/interviews";

const VARIANT = {
  PREPARING: "secondary",
  PLANNED: "outline",
  LIVE: "default",
  COMPLETED: "success",
  ABORTED: "secondary",
  FAILED: "destructive",
} as const satisfies Record<InterviewStatus, string>;

export function InterviewStatusBadge({ status }: { status: InterviewStatus }) {
  return (
    <Badge variant={VARIANT[status]}>
      {status === "PREPARING" && <LoaderCircle className="animate-spin" aria-hidden />}
      {statusLabel(status)}
    </Badge>
  );
}
