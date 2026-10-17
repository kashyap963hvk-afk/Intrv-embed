import type { Metadata } from "next";

import { SignupForm } from "./signup-form";

export const metadata: Metadata = { title: "Request access" };

export default function SignupPage() {
  return (
    <div className="mx-auto flex w-full max-w-sm flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Request access</h1>
        <p className="text-sm text-muted-foreground">
          An owner of the organization must approve your account before you can use the app.
        </p>
      </div>
      <SignupForm />
    </div>
  );
}
