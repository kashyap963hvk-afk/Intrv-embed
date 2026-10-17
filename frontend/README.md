# Intrv-embed frontend

Next.js (App Router) + TypeScript + Tailwind + shadcn/ui conventions + TanStack Query.
Deployed on Vercel (see `../docs/DEPLOYMENT.md`); `Dockerfile` serves self-hosted setups.

```bash
npm ci
npm run dev          # http://localhost:3000 (uses .env.development; override in .env.local)
npm run gen:api      # regenerate src/lib/api/schema.d.ts from ../backend/openapi.json
npm run lint && npm run typecheck && npm run build
```

`npm run build` fails fast unless `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_WS_BASE_URL` and
`NEXT_PUBLIC_APP_URL` are set (they are inlined into the client bundle at build time).

Add UI primitives with `npx shadcn@latest add <component>` (`components.json` is configured).
