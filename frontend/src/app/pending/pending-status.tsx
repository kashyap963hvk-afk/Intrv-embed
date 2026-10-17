"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { RequireAuth } from "@/components/auth/require-auth";
import { Button } from "@/components/ui/button";
import { useMe } from "@/lib/auth";

export function PendingStatus() {
  const router = useRouter();
  const { refetch, isFetching, data } = useMe();

  useEffect(() => {
    if (data?.user.status === "ACTIVE") router.replace("/app");
  }, [data, router]);

  return (
    <RequireAuth requireActive={false}>
      {(me) => (
        <div className="flex flex-col gap-4 text-sm">
          <p>
            Thanks, {me.user.name}. Your request to join <strong>{me.org.name}</strong> is waiting for an owner to
            approve it. You will receive an email when it is approved.
          </p>
          <div>
            <Button variant="outline" onClick={() => void refetch()} disabled={isFetching}>
              {isFetching ? "Checking…" : "Check again"}
            </Button>
          </div>
        </div>
      )}
    </RequireAuth>
  );
}
