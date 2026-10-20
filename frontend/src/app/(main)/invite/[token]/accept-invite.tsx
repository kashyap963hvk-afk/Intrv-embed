"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { FormField } from "@/components/form-field";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { ME_QUERY_KEY } from "@/lib/auth";
import { passwordSchema } from "@/lib/validation";

const schema = z
  .object({
    name: z.string().trim().min(1, "Enter your name").max(200),
    password: passwordSchema,
    confirm: z.string(),
  })
  .refine((v) => v.password === v.confirm, { path: ["confirm"], message: "Passwords do not match" });
type Values = z.infer<typeof schema>;

export function AcceptInvite() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const preview = useQuery({
    queryKey: ["invite", token],
    queryFn: async () => unwrap(await api.GET("/api/auth/invites/{token}", { params: { path: { token } } })),
    retry: false,
  });
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { name: "", password: "", confirm: "" },
  });
  const { errors, isSubmitting } = form.formState;

  async function onSubmit({ name, password }: Values) {
    setError(null);
    try {
      const me = unwrap(await api.POST("/api/auth/invites/accept", { body: { token, name, password } }));
      queryClient.setQueryData(ME_QUERY_KEY, me);
      router.push("/app");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not accept the invitation.");
    }
  }

  if (preview.isPending) return <p className="text-sm text-muted-foreground">Checking invitation…</p>;
  if (preview.isError || !preview.data.usable) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Invitation unavailable</AlertTitle>
        <AlertDescription>{preview.data?.reason ?? preview.error?.message}</AlertDescription>
      </Alert>
    );
  }

  return (
    <form className="flex flex-col gap-4" noValidate onSubmit={form.handleSubmit(onSubmit)}>
      <p className="text-sm text-muted-foreground">
        Join <strong className="text-foreground">{preview.data.org_name}</strong> as{" "}
        {preview.data.role === "OWNER" ? "an owner" : "a member"} with <strong>{preview.data.email}</strong>.
      </p>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <FormField id="name" label="Full name" error={errors.name?.message}>
        <Input id="name" autoComplete="name" aria-invalid={!!errors.name} {...form.register("name")} />
      </FormField>
      <FormField
        id="password"
        label="Choose a password"
        error={errors.password?.message}
        hint="At least 12 characters."
      >
        <Input id="password" type="password" autoComplete="new-password" {...form.register("password")} />
      </FormField>
      <FormField id="confirm" label="Confirm password" error={errors.confirm?.message}>
        <Input id="confirm" type="password" autoComplete="new-password" {...form.register("confirm")} />
      </FormField>
      <Button type="submit" disabled={isSubmitting}>
        {isSubmitting ? "Joining…" : "Join organization"}
      </Button>
    </form>
  );
}
