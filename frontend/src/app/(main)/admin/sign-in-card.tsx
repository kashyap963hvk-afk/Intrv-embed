"use client";

import { useQuery } from "@tanstack/react-query";

import { useGoogleSignIn } from "@/components/auth/google-sign-in-button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api, type Schemas } from "@/lib/api/client";
import { unwrap } from "@/lib/api/problem";
import { useAdminMutation } from "@/lib/admin-api";

type Policy = Schemas["OrgPolicy"];

/** Members sign in with Google through their invitation; owners keep their password. */
export function SignInCard() {
  const google = useGoogleSignIn();
  const policy = useQuery({
    queryKey: ["admin", "org-policy"],
    queryFn: async () => unwrap(await api.GET("/api/admin/org-policy")),
  });
  const save = useAdminMutation(
    async (next: Policy) => unwrap(await api.PUT("/api/admin/org-policy", { body: next })),
    "Sign-in setting saved",
  );
  const googleOnly = policy.data?.member_sign_in === "google";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Sign-in</CardTitle>
        <CardDescription>
          Give access by inviting any email address under Users. Owners can always sign in with a password.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        <div className="flex items-start justify-between gap-4">
          <Label htmlFor="member-google" className="flex-col items-start gap-1">
            Members sign in with Google only
            <span className="text-xs font-normal text-muted-foreground">
              An invited member opens their invitation and continues with the Google account for that email. Password
              sign-in is refused for members.
            </span>
          </Label>
          <Switch
            id="member-google"
            checked={googleOnly}
            disabled={!policy.data || save.isPending || (!google && !googleOnly)}
            onCheckedChange={(checked) =>
              policy.data && save.mutate({ ...policy.data, member_sign_in: checked ? "google" : "password" })
            }
          />
        </div>
        {!google && (
          <p className="text-xs text-muted-foreground">
            Google sign-in is not configured on this server: set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET
            (docs/DEPLOYMENT.md).
          </p>
        )}
      </CardContent>
    </Card>
  );
}
