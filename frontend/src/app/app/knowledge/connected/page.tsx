import type { Metadata } from "next";
import { Suspense } from "react";

import { ConnectedResult } from "./connected-result";

export const metadata: Metadata = { title: "Account connection" };

/** Where the backend sends the OAuth popup after the provider's consent screen. */
export default function ConnectedPage() {
  return (
    <div className="mx-auto flex w-full max-w-sm flex-col gap-6">
      <Suspense fallback={<p className="text-sm text-muted-foreground">Finishing sign-in…</p>}>
        <ConnectedResult />
      </Suspense>
    </div>
  );
}
