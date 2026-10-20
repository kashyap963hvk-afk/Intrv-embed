import type { Metadata } from "next";

import { QuestionsConsole } from "./questions-console";

export const metadata: Metadata = { title: "Questions" };

export default function QuestionsPage() {
  return <QuestionsConsole />;
}
