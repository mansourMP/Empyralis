'use client';

export type WorkstationApprovalResolvedEventDetail = {
  workspaceId: string;
  approvalId: string;
};

const WORKSTATION_APPROVAL_RESOLVED_EVENT = 'workstation:approval-resolved';

export function subscribeWorkstationApprovalResolved(
  listener: (detail: WorkstationApprovalResolvedEventDetail) => void,
): () => void {
  if (typeof window === 'undefined') {
    return () => {};
  }

  const handler = (event: Event) => {
    const detail = (event as CustomEvent<WorkstationApprovalResolvedEventDetail>).detail;
    if (detail) {
      listener(detail);
    }
  };

  window.addEventListener(WORKSTATION_APPROVAL_RESOLVED_EVENT, handler as EventListener);
  return () => {
    window.removeEventListener(WORKSTATION_APPROVAL_RESOLVED_EVENT, handler as EventListener);
  };
}

export function emitWorkstationApprovalResolved(
  detail: WorkstationApprovalResolvedEventDetail,
): void {
  if (typeof window === 'undefined') {
    return;
  }

  window.dispatchEvent(
    new CustomEvent<WorkstationApprovalResolvedEventDetail>(
      WORKSTATION_APPROVAL_RESOLVED_EVENT,
      { detail },
    ),
  );
}
