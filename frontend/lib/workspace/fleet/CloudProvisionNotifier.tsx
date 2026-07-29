"use client";

// "Notify me once it's ready" — the completion half of the background
// provisioning flow.
//
// Mounted once, in FleetShell, so it covers every workspace route. That is
// the point: the setup modal can now be dismissed and the build takes 2-10
// minutes, so the person who dismissed it is very likely somewhere else
// (Tasks, an agent, Projects) when it lands. A notification that only fired
// on the Hardware page would have missed exactly the case this rebuild
// exists for.
//
// This is NOT a new notification system. It renders the app's existing
// PlatformNotification (lib/ui/platform-notification.tsx, already used by
// HardwareSection and the chat composer) and reads the one existing
// provisioning record from vps-provision-watch.ts. Nothing else can push
// through it.

import { useRouter } from "next/navigation";

import { PlatformNotification } from "@/lib/ui/platform-notification";
import { acknowledgeVpsProvisionWatch, useVpsProvisionWatch } from "@/lib/workspace/fleet/vps-provision-watch";

export function CloudProvisionNotifier({ workspaceId }: { workspaceId: string }) {
  const router = useRouter();
  const watch = useVpsProvisionWatch(workspaceId);

  if (!watch || watch.acknowledged) {
    return null;
  }
  if (watch.stage !== "connected" && watch.stage !== "failed") {
    return null;
  }

  if (watch.stage === "connected") {
    return (
      <PlatformNotification
        tone="success"
        title="Cloud server ready"
        detail={`${watch.providerLabel}${watch.regionLabel ? ` · ${watch.regionLabel}` : ""} is connected and ready for agents.`}
        action={{
          label: "View hardware",
          onClick: () => {
            acknowledgeVpsProvisionWatch(workspaceId);
            router.push(`/w/${encodeURIComponent(workspaceId)}/hardware`);
          },
        }}
        onClose={() => acknowledgeVpsProvisionWatch(workspaceId)}
      />
    );
  }

  return (
    <PlatformNotification
      tone="danger"
      title={`${watch.providerLabel} server setup failed`}
      // The real reason recorded against the build (the box's own
      // install_phase / install_error, else the backend's error) — never a
      // generic "something went wrong".
      detail={watch.error || "Setup did not complete."}
      action={{
        label: "See details",
        // The Hardware page keeps a persistent "Needs attention" row for a
        // failed build, with the full step list and the Delete server action
        // one click further in — so this only has to get the user there.
        onClick: () => {
          acknowledgeVpsProvisionWatch(workspaceId);
          router.push(`/w/${encodeURIComponent(workspaceId)}/hardware`);
        },
      }}
      onClose={() => acknowledgeVpsProvisionWatch(workspaceId)}
    />
  );
}
