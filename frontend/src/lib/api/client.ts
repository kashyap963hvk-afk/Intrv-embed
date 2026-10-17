import createClient from "openapi-fetch";

import { env } from "@/lib/env";

import type { components, paths } from "./schema";

/** Typed client for the backend API. Auth uses httpOnly cookies, so credentials are always sent. */
export const api = createClient<paths>({
  baseUrl: env.apiBaseUrl,
  credentials: "include",
});

export type Schemas = components["schemas"];
