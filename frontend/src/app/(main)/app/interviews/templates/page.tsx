import type { Metadata } from "next";

import { TemplatesConsole } from "./templates-console";

export const metadata: Metadata = { title: "Interview templates" };

export default function TemplatesPage() {
  return <TemplatesConsole />;
}
