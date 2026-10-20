import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";

export default function Home() {
  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h1 className="text-3xl font-semibold tracking-tight">Intrv-embed</h1>
        <p className="max-w-2xl text-muted-foreground">
          An agentic AI interviewer: Embedded Systems by default, any role through editable role packs, and any model
          you choose, from cloud APIs to a model on your own machine.
        </p>
      </div>
      <p className="max-w-2xl text-sm text-muted-foreground">
        This build contains the platform foundation. Sign-in, knowledge spaces, question generation and live interviews
        arrive in the next phases.
      </p>
      <div>
        <Link href="/healthz" className={buttonVariants({ variant: "outline" })}>
          Check system status
        </Link>
      </div>
    </section>
  );
}
