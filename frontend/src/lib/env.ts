import { z } from "zod";

const publicEnvSchema = z.object({
  NEXT_PUBLIC_API_BASE_URL: z.url({ protocol: /^https?$/ }),
  NEXT_PUBLIC_WS_BASE_URL: z.url({ protocol: /^wss?$/ }),
  NEXT_PUBLIC_APP_URL: z.url({ protocol: /^https?$/ }),
  // Optional: the Google Cloud project number, so files picked in the Google Picker are granted to the app.
  NEXT_PUBLIC_GOOGLE_APP_ID: z
    .union([z.literal(""), z.string().regex(/^\d+$/, "must be the Google Cloud project number")])
    .optional(),
});

// Next.js inlines NEXT_PUBLIC_* at build time only when each variable is referenced literally.
const parsed = publicEnvSchema.safeParse({
  NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
  NEXT_PUBLIC_WS_BASE_URL: process.env.NEXT_PUBLIC_WS_BASE_URL,
  NEXT_PUBLIC_APP_URL: process.env.NEXT_PUBLIC_APP_URL,
  NEXT_PUBLIC_GOOGLE_APP_ID: process.env.NEXT_PUBLIC_GOOGLE_APP_ID,
});

if (!parsed.success) {
  throw new Error(
    `Invalid public environment variables (see .env.example and docs/DEPLOYMENT.md §4.1):\n${z.prettifyError(parsed.error)}`,
  );
}

const stripSlash = (url: string) => url.replace(/\/+$/, "");

export const env = {
  apiBaseUrl: stripSlash(parsed.data.NEXT_PUBLIC_API_BASE_URL),
  wsBaseUrl: stripSlash(parsed.data.NEXT_PUBLIC_WS_BASE_URL),
  appUrl: stripSlash(parsed.data.NEXT_PUBLIC_APP_URL),
  googleAppId: parsed.data.NEXT_PUBLIC_GOOGLE_APP_ID || null,
} as const;
