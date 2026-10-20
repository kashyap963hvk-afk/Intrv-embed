"use client";

import Link from "next/link";

import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/app/interviews", label: "Interviews" },
  { href: "/app/interviews/templates", label: "Templates" },
] as const;

/** Interviews ↔ Templates switch at the top of both pages. */
export function SectionNav({ current }: { current: (typeof LINKS)[number]["href"] }) {
  return (
    <nav aria-label="Interviews" className="inline-flex h-9 w-fit items-center rounded-lg bg-muted p-[3px]">
      {LINKS.map((link) => (
        <Link
          key={link.href}
          href={link.href}
          aria-current={link.href === current ? "page" : undefined}
          className={cn(
            "inline-flex h-full items-center rounded-md px-3 text-sm font-medium text-muted-foreground",
            link.href === current && "bg-background text-foreground shadow-sm",
          )}
        >
          {link.label}
        </Link>
      ))}
    </nav>
  );
}
