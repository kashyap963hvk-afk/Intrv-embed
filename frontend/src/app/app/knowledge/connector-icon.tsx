import { Box, Building2, Cloud, CloudCog, Database, Globe, HardDrive, type LucideIcon } from "lucide-react";

import type { ConnectorKind } from "@/lib/connectors";

const ICONS: Record<ConnectorKind, LucideIcon> = {
  GOOGLE_DRIVE: HardDrive,
  GOOGLE_DRIVE_ORG: Building2,
  S3_COMPATIBLE: Database,
  AZURE_BLOB: CloudCog,
  MS_GRAPH: Cloud,
  DROPBOX: Box,
  URL: Globe,
};

export function ConnectorIcon({ kind, className }: { kind: ConnectorKind; className?: string }) {
  const Icon = ICONS[kind];
  return <Icon className={className} aria-hidden />;
}
