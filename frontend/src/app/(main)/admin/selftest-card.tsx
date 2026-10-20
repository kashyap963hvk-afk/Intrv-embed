"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useSelfTest } from "@/lib/knowledge-api";

export function SelfTestCard() {
  const run = useSelfTest();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          System self-test
          {run.data && (
            <Badge variant={run.data.ok ? "success" : "destructive"}>
              {run.data.ok ? "all checks passed" : "problems found"}
            </Badge>
          )}
        </CardTitle>
        <CardDescription>
          Embeds and reranks a sample, checks the vector store, and reports which document parsers are installed.
        </CardDescription>
        <CardAction>
          <Button size="sm" disabled={run.isPending} onClick={() => run.mutate(undefined)}>
            {run.isPending ? "Running…" : run.data ? "Run again" : "Run self-test"}
          </Button>
        </CardAction>
      </CardHeader>
      {run.data && (
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Check</TableHead>
                <TableHead>Result</TableHead>
                <TableHead>Detail</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {run.data.checks.map((check) => (
                <TableRow key={check.name}>
                  <TableCell className="font-medium whitespace-nowrap">{check.name}</TableCell>
                  <TableCell>
                    <Badge variant={check.ok ? "success" : "destructive"}>{check.ok ? "ok" : "failed"}</Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs break-all whitespace-normal text-muted-foreground">
                    {check.detail}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      )}
    </Card>
  );
}
