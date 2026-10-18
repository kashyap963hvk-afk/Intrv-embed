"use client";

import { CircleCheck, CircleX } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { OAUTH_CHANNEL, oauthMessage, oauthResultFromParams, providerLabel } from "@/lib/connectors";

const CLOSE_AFTER_MS = 1_500;

/** Reports the OAuth outcome to the window that opened this popup, then closes it. */
export function ConnectedResult() {
  const params = useSearchParams();
  const result = oauthResultFromParams(params);
  const { status, provider, detail } = result;
  const [closeFailed, setCloseFailed] = useState(false);

  useEffect(() => {
    const message = oauthMessage({ status, provider, detail });
    // Only this app's own origin may receive the outcome.
    window.opener?.postMessage(message, window.location.origin);
    // Some providers' pages cut the popup off from its opener; a same-origin channel still reaches the app.
    if (typeof BroadcastChannel !== "undefined") {
      const channel = new BroadcastChannel(OAUTH_CHANNEL);
      channel.postMessage(message);
      channel.close();
    }
    const timer = setTimeout(() => {
      window.close();
      // Still open: this page was not opened as a popup (or the browser refused).
      setCloseFailed(true);
    }, CLOSE_AFTER_MS);
    return () => clearTimeout(timer);
  }, [status, provider, detail]);

  const label = provider ? providerLabel(provider) : "Your account";
  const ok = status === "ok";

  return (
    <div className="flex flex-col gap-4">
      <Alert variant={ok ? "default" : "destructive"}>
        {ok ? <CircleCheck aria-hidden /> : <CircleX aria-hidden />}
        <AlertTitle>{ok ? `${label} connected` : `${label} was not connected`}</AlertTitle>
        <AlertDescription>
          {detail && <p>{detail}</p>}
          <p>{closeFailed ? "You can close this window." : "This window closes by itself."}</p>
        </AlertDescription>
      </Alert>
      <div className="flex gap-2">
        <Button
          onClick={() => {
            window.close();
            setCloseFailed(true);
          }}
        >
          Close
        </Button>
        {closeFailed && (
          <Link href="/app/knowledge" className={buttonVariants({ variant: "outline" })}>
            Back to Knowledge
          </Link>
        )}
      </div>
    </div>
  );
}
