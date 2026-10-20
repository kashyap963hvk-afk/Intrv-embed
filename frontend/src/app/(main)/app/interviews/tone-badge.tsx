import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import type { Tone } from "@/lib/interviews";

const VARIANT = { positive: "success", negative: "destructive", neutral: "secondary" } as const satisfies Record<
  Tone,
  string
>;

/** A recommendation or decision chip, coloured by direction. */
export function ToneBadge({ tone, children, title }: { tone: Tone; children: ReactNode; title?: string }) {
  return (
    <Badge variant={VARIANT[tone]} title={title}>
      {children}
    </Badge>
  );
}
