import type { Metadata } from "next";

import { InterviewsConsole } from "./interviews-console";

export const metadata: Metadata = { title: "Interviews" };

export default function InterviewsPage() {
  return <InterviewsConsole />;
}
