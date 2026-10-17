import type { Metadata } from "next";

import { LlmConsole } from "./llm-console";

export const metadata: Metadata = { title: "AI models" };

export default function LlmPage() {
  return <LlmConsole />;
}
