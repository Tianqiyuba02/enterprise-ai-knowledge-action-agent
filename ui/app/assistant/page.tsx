import { DataUnavailable } from "@/components/shared";
import { AssistantWorkspace } from "@/components/assistant-workspace";
import { backendFetch } from "@/lib/backend";
import type { EmployeeProfile } from "@/lib/contracts";

export const metadata = { title: "Assistant" };

export default async function AssistantPage() {
  let profile: EmployeeProfile;
  try {
    profile = await backendFetch<EmployeeProfile>("/me/profile");
  } catch {
    return <div className="page-shell"><DataUnavailable /></div>;
  }
  return (
    <div className="assistant-page">
      <AssistantWorkspace firstName={profile.full_name.split(" ")[0]} />
    </div>
  );
}
