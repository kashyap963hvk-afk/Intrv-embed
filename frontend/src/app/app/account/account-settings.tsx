"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { RequireAuth } from "@/components/auth/require-auth";
import { FormField } from "@/components/form-field";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { passwordSchema } from "@/lib/validation";

const schema = z
  .object({ current_password: z.string().min(1, "Enter your current password"), new_password: passwordSchema, confirm: z.string() })
  .refine((v) => v.new_password === v.confirm, { path: ["confirm"], message: "Passwords do not match" });
type Values = z.infer<typeof schema>;

function ChangePassword() {
  const [status, setStatus] = useState<{ ok: boolean; message: string } | null>(null);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { current_password: "", new_password: "", confirm: "" },
  });
  const { errors, isSubmitting } = form.formState;

  async function onSubmit({ current_password, new_password }: Values) {
    setStatus(null);
    try {
      unwrap(await api.POST("/api/auth/password", { body: { current_password, new_password } }));
      form.reset();
      setStatus({ ok: true, message: "Password changed. Other devices were signed out." });
    } catch (e) {
      setStatus({ ok: false, message: e instanceof Error ? e.message : "Could not change the password." });
    }
  }

  return (
    <form className="flex max-w-sm flex-col gap-4" noValidate onSubmit={form.handleSubmit(onSubmit)}>
      {status && (
        <Alert variant={status.ok ? "default" : "destructive"}>
          <AlertDescription>{status.message}</AlertDescription>
        </Alert>
      )}
      <FormField id="current_password" label="Current password" error={errors.current_password?.message}>
        <Input id="current_password" type="password" autoComplete="current-password" {...form.register("current_password")} />
      </FormField>
      <FormField id="new_password" label="New password" error={errors.new_password?.message}>
        <Input id="new_password" type="password" autoComplete="new-password" {...form.register("new_password")} />
      </FormField>
      <FormField id="confirm" label="Confirm new password" error={errors.confirm?.message}>
        <Input id="confirm" type="password" autoComplete="new-password" {...form.register("confirm")} />
      </FormField>
      <Button type="submit" disabled={isSubmitting} className="w-fit">
        {isSubmitting ? "Saving…" : "Change password"}
      </Button>
    </form>
  );
}

export function AccountSettings() {
  return (
    <RequireAuth requireActive={false}>
      {(me) => (
        <section className="flex flex-col gap-6">
          <h1 className="text-2xl font-semibold tracking-tight">Account</h1>
          <Card>
            <CardHeader>
              <CardTitle>{me.user.name}</CardTitle>
              <CardDescription>
                {me.user.email} · {me.org.name}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ChangePassword />
            </CardContent>
          </Card>
        </section>
      )}
    </RequireAuth>
  );
}
