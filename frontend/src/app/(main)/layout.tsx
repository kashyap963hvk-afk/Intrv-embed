import { AppShell } from "@/components/app-shell";

/** Every signed-in and public page with the app header; the candidate's interview room (/join) has its own frame. */
export default function MainLayout({ children }: LayoutProps<"/">) {
  return <AppShell>{children}</AppShell>;
}
