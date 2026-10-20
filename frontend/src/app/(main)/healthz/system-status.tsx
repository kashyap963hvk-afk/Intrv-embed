"use client";

import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, type Schemas } from "@/lib/api/client";
import { env } from "@/lib/env";

const REFRESH_MS = 15_000;

type Readiness = Schemas["ReadinessResponse"];

async function fetchReadiness(): Promise<Readiness> {
  // 503 is an expected answer ("degraded") and carries the same body as 200.
  const { data, error, response } = await api.GET("/readyz");
  const body = data ?? error;
  if (!body) {
    throw new Error(`Unexpected response from /readyz: HTTP ${response.status}`);
  }
  return body;
}

export function SystemStatus() {
  const query = useQuery({ queryKey: ["readyz"], queryFn: fetchReadiness, refetchInterval: REFRESH_MS });
  const checks = query.data ? Object.entries(query.data.checks) : [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Backend
          {query.data && (
            <Badge variant={query.data.status === "ok" ? "success" : "destructive"}>
              {query.data.status === "ok" ? "Ready" : "Degraded"}
            </Badge>
          )}
          {query.isError && <Badge variant="destructive">Unreachable</Badge>}
        </CardTitle>
        <CardDescription className="font-mono text-xs">{env.apiBaseUrl}</CardDescription>
        <CardAction>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void query.refetch()}
            disabled={query.isFetching}
            aria-label="Refresh status"
          >
            <RefreshCw className={query.isFetching ? "animate-spin" : undefined} />
            Refresh
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        {query.isPending && <p className="text-sm text-muted-foreground">Checking…</p>}
        {query.isError && (
          <p role="alert" className="text-sm text-destructive">
            Cannot reach the backend. Check that it is running and that CORS_ALLOWED_ORIGINS includes{" "}
            <span className="font-mono">{env.appUrl}</span>. ({query.error.message})
          </p>
        )}
        {checks.length > 0 && (
          <table className="w-full text-sm">
            <caption className="sr-only">Dependency readiness</caption>
            <thead className="text-left text-muted-foreground">
              <tr>
                <th scope="col" className="py-2 font-medium">
                  Dependency
                </th>
                <th scope="col" className="py-2 font-medium">
                  Status
                </th>
                <th scope="col" className="py-2 text-right font-medium">
                  Latency
                </th>
              </tr>
            </thead>
            <tbody>
              {checks.map(([name, check]) => (
                <tr key={name} className="border-t align-top">
                  <td className="py-2 font-mono">{name}</td>
                  <td className="py-2">
                    <div className="flex flex-col gap-1">
                      <Badge variant={check.status === "ok" ? "success" : "destructive"}>{check.status}</Badge>
                      {check.error && <span className="text-xs text-muted-foreground">{check.error}</span>}
                    </div>
                  </td>
                  <td className="py-2 text-right tabular-nums">{check.latency_ms.toFixed(1)} ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {query.dataUpdatedAt > 0 && (
          <p className="mt-4 text-xs text-muted-foreground">
            Updated {new Date(query.dataUpdatedAt).toLocaleTimeString()} · refreshes every {REFRESH_MS / 1000}s
          </p>
        )}
      </CardContent>
    </Card>
  );
}
