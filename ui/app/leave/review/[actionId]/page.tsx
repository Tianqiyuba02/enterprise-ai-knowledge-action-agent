import { notFound } from "next/navigation";

import { ReviewAuthorization } from "@/components/review-authorization";
import { BackLink, DataUnavailable } from "@/components/shared";
import { backendFetch, isMissingPortalResource } from "@/lib/backend";
import type { ActionDetail } from "@/lib/contracts";

export const metadata = { title: "Review annual leave" };

export default async function ReviewPage({ params }: { params: Promise<{ actionId: string }> }) {
  const { actionId } = await params;
  let detail: ActionDetail;
  try {
    detail = await backendFetch<ActionDetail>(`/actions/${encodeURIComponent(actionId)}/detail`);
  } catch (error) {
    if (isMissingPortalResource(error)) notFound();
    return <div className="page-shell"><DataUnavailable /></div>;
  }
  if (detail.action_type !== "submit_annual_leave") notFound();
  return (
    <div className="page-shell review-page">
      <BackLink href="/assistant">Back to assistant</BackLink>
      <ReviewAuthorization initialDetail={detail} />
    </div>
  );
}
