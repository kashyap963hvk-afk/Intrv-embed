import type { Metadata } from "next";

import { SearchPlayground } from "./search-playground";

export const metadata: Metadata = { title: "Knowledge search" };

export default function KnowledgeSearchPage() {
  return <SearchPlayground />;
}
