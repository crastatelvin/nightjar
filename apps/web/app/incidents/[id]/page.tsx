import type { Metadata } from "next";
import { CaseFileClient } from "./case-file-client";

export const metadata: Metadata = {
  title: "Case file",
};

export default async function IncidentPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <CaseFileClient incidentId={id} />;
}
