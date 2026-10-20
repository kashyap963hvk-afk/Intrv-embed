import type { Metadata } from "next";

import { AccountSettings } from "./account-settings";

export const metadata: Metadata = { title: "Account" };

export default function AccountPage() {
  return <AccountSettings />;
}
