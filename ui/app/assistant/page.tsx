import { DataUnavailable } from "@/components/shared";
import { AssistantWorkspace } from "@/components/assistant-workspace";
import { backendFetch } from "@/lib/backend";
import type { DemoReadiness, EmployeeProfile, GuidedScenarios } from "@/lib/contracts";

export const metadata = { title: "Assistant" };

export default async function AssistantPage() {
  let profile: EmployeeProfile;
  let readiness: DemoReadiness | null = null;
  let scenarios: GuidedScenarios | null = null;
  try {
    [profile, readiness, scenarios] = await Promise.all([
      backendFetch<EmployeeProfile>("/me/profile"),
      backendFetch<DemoReadiness>("/demo/readiness").catch(() => null),
      backendFetch<GuidedScenarios>("/demo/guided-scenarios").catch(() => null),
    ]);
  } catch {
    return <div className="page-shell"><DataUnavailable /></div>;
  }
  return (
    <div className="assistant-page">
      <AssistantWorkspace
        firstName={profile.full_name.split(" ")[0]}
        readiness={readiness}
        scenarios={scenarios?.items ?? []}
      />
    </div>
  );
}
