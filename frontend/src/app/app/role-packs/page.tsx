import type { Metadata } from "next";

import { RolePacksConsole } from "./role-packs-console";

export const metadata: Metadata = { title: "Role packs" };

export default function RolePacksPage() {
  return <RolePacksConsole />;
}
