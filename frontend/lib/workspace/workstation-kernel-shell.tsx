'use client';

import type { PropsWithChildren } from 'react';
import Link from 'next/link';
import { useMemo } from 'react';
import { usePathname, useSearchParams } from 'next/navigation';

import { Plus } from 'lucide-react';

import { joinClassNames } from '@/lib/ui/primitives';
import { buildApplicationTabHref } from '@/lib/workspace/application-surface-tabs';
import { WorkstationHardwareStatus } from '@/lib/workspace/workstation-hardware-status';
import { WorkstationTitlebar } from '@/lib/workspace/workstation-titlebar';
import { useWorkspaceBoundary } from '@/lib/workspace/workspace-boundary';
import { resolveRouteIdFromHref } from '@/lib/workspace/workspace-shell';
import {
  buildWorkspaceRouteHref,
  getWorkspaceNavRouteDefinition,
  type WorkspaceNavDestinationId,
} from '../../shared/nav-manifest';

function buildStudioCreateAgentHref(
  workspaceId: string,
  searchParams: { toString: () => string },
): string {
  const params = new URLSearchParams(searchParams.toString());
  params.set('createAgent', '1');
  params.delete('agent');
  params.delete('externalAgent');
  params.delete('agentComputer');
  const query = params.toString();
  return `${buildWorkspaceRouteHref(workspaceId, 'studio')}?${query}`;
}

/**
 * Kernel shell: topbar (hardware status + contextual quick actions) plus
 * the main content canvas. The old per-destination left rail (assistant
 * nav, studio agent list, settings sections, agent-detail tab nav) was
 * removed — PrimaryRail (frontend/lib/workspace/fleet/PrimaryRail.tsx) is
 * now the only navigation surface across every workspace route. Per-agent
 * detail content (Overview/Chat/Memory/Channels/Tools) still renders from
 * WorkstationDeployedAgentsPane itself, which reads its own `agent`/`tab`
 * query params — it never depended on the deleted rail for content, only
 * for a now-redundant tab switcher.
 */
export function WorkstationKernelShell({
  children,
}: PropsWithChildren) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { bootstrap, workspaceId } = useWorkspaceBoundary();

  const activeRouteId = useMemo(
    () => resolveRouteIdFromHref(workspaceId, pathname),
    [pathname, workspaceId],
  );
  const activeDestinationId: WorkspaceNavDestinationId = useMemo(() => {
    if (!activeRouteId) {
      return 'sage';
    }
    return getWorkspaceNavRouteDefinition(activeRouteId).destinationId;
  }, [activeRouteId]);
  const hardwareHref = buildWorkspaceRouteHref(workspaceId, 'hardware');
  const selectedStudioAgentId = activeDestinationId === 'studio' ? (searchParams.get('agent') || null) : null;
  const selectedStudioExternalAgentId = activeDestinationId === 'studio' ? (searchParams.get('externalAgent') || null) : null;
  const studioAgentDetailActive = activeDestinationId === 'studio' && Boolean(
    selectedStudioAgentId || selectedStudioExternalAgentId,
  );

  return (
    <div
      data-workstation-shell="kernel"
      data-workstation-route={activeRouteId ?? 'unknown'}
      data-workstation-destination={activeDestinationId}
      className={joinClassNames(
        'workstation-shell',
        activeDestinationId === 'sage' && 'workstation-shell--sage',
        studioAgentDetailActive && 'workstation-shell--studio-agent-detail',
        activeRouteId === 'chat' && 'workstation-shell--chat',
      )}
    >
      <div className="workstation-shell__main-column" data-workstation-shell-zone="main-canvas">
        <div className="workstation-shell__topbar" data-workstation-main-pane="topbar">
          <WorkstationTitlebar
            surfaceLabel=""
            surfaceControl={null}
            diagnosticsVisible={false}
            onToggleDiagnostics={() => {}}
            actions={(
              <>
                <WorkstationHardwareStatus
                  runtimeTargets={bootstrap.runtime.runtimeTargets}
                  hardwareHref={hardwareHref}
                />
                {activeDestinationId === 'studio' ? (
                  <Link
                    href={buildStudioCreateAgentHref(workspaceId, searchParams)}
                    className="workstation-titlebar__link"
                    title="Add Business Agent"
                    aria-label="Add agent"
                  >
                    <Plus size={16} aria-hidden="true" />
                    <span>Add agent</span>
                  </Link>
                ) : activeDestinationId === 'applications' ? (
                  <Link
                    href={buildApplicationTabHref(workspaceId, 'my_apps', searchParams)}
                    className="workstation-titlebar__link workstation-titlebar__link--icon"
                    title="Create app"
                    aria-label="Create app"
                  >
                    <Plus size={16} aria-hidden="true" />
                  </Link>
                ) : null}
              </>
            )}
            navigation={null}
          />
        </div>

        <div className="workstation-shell__body" data-workstation-main-pane="content-body">
          <div
            className="workstation-layout"
            data-workstation-destination={activeDestinationId}
            data-workstation-main-zone="main"
          >
            <section
              className="workstation-primary-canvas"
              data-workstation-focus-surface={activeRouteId ?? 'unknown'}
              data-workstation-main-pane="content"
            >
              {children}
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
