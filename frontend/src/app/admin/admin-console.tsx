"use client";

import { RequireAuth } from "@/components/auth/require-auth";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { AiPolicyCard } from "./ai-policy-card";
import { AuditTab } from "./audit-tab";
import { GroupsTab } from "./groups-tab";
import { InvitesTab } from "./invites-tab";
import { OrgTab } from "./org-tab";
import { UsersTab } from "./users-tab";

export function AdminConsole() {
  return (
    <RequireAuth requireOwner>
      {(me) => (
        <section className="flex flex-col gap-6">
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-semibold tracking-tight">Administration</h1>
            <p className="text-sm text-muted-foreground">
              {me.org.name} · members join with the organization ID <span className="font-mono">{me.org.slug}</span>
            </p>
          </div>
          <Tabs defaultValue="users">
            <TabsList>
              <TabsTrigger value="users">Members</TabsTrigger>
              <TabsTrigger value="groups">Groups</TabsTrigger>
              <TabsTrigger value="invites">Invitations</TabsTrigger>
              <TabsTrigger value="audit">Audit log</TabsTrigger>
              <TabsTrigger value="ai">AI policy</TabsTrigger>
              <TabsTrigger value="org">Organization</TabsTrigger>
            </TabsList>
            <TabsContent value="users">
              <UsersTab currentUserId={me.user.id} />
            </TabsContent>
            <TabsContent value="groups">
              <GroupsTab />
            </TabsContent>
            <TabsContent value="invites">
              <InvitesTab />
            </TabsContent>
            <TabsContent value="audit">
              <AuditTab />
            </TabsContent>
            <TabsContent value="ai">
              <AiPolicyCard />
            </TabsContent>
            <TabsContent value="org">
              <OrgTab />
            </TabsContent>
          </Tabs>
        </section>
      )}
    </RequireAuth>
  );
}
