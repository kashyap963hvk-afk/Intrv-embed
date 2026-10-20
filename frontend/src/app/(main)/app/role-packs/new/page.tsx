import type { Metadata } from "next";

import { NewPack } from "./new-pack";

export const metadata: Metadata = { title: "New role pack" };

export default function NewRolePackPage() {
  return <NewPack />;
}
