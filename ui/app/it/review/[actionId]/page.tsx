import { notFound } from "next/navigation";

import { ITReviewAuthorization } from "@/components/it-review-authorization";
import { BackLink, DataUnavailable } from "@/components/shared";
import { backendFetch, PortalApiError } from "@/lib/backend";
import type { ActionDetail } from "@/lib/contracts";

export const metadata = { title: "Review IT support" };

export default async function ITReviewPage({
  params,
}: {
  params: Promise<{ actionId: string }>;
}) {
  const { actionId } = await params;
  let detail: ActionDetail;
  try {
    detail = await backendFetch<ActionDetail>(
      `/actions/${encodeURIComponent(actionId)}/detail`,
    );
  } catch (error) {
    if (error instanceof PortalApiError && error.status === 404) notFound();
    return <div className="page-shell"><DataUnavailable /></div>;
  }
  if (detail.action_type !== "create_it_support_ticket") notFound();
  return (
    <div className="page-shell review-page">
      <BackLink href="/it">Back to IT Support</BackLink>
      <ITReviewAuthorization initialDetail={detail} />
    </div>
  );
}
