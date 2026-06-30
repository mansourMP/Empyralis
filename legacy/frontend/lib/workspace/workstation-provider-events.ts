'use client';

export type WorkstationProviderChangedEventDetail = {
  workspaceId: string;
  providerId: string | null;
  action: 'saved' | 'deleted';
};

const WORKSTATION_PROVIDER_CHANGED_EVENT = 'workstation:provider-changed';

export function subscribeWorkstationProviderChanged(
  listener: (detail: WorkstationProviderChangedEventDetail) => void,
): () => void {
  if (typeof window === 'undefined') {
    return () => {};
  }

  const handler = (event: Event) => {
    const detail = (event as CustomEvent<WorkstationProviderChangedEventDetail>).detail;
    if (detail) {
      listener(detail);
    }
  };

  window.addEventListener(WORKSTATION_PROVIDER_CHANGED_EVENT, handler as EventListener);
  return () => {
    window.removeEventListener(WORKSTATION_PROVIDER_CHANGED_EVENT, handler as EventListener);
  };
}

export function emitWorkstationProviderChanged(
  detail: WorkstationProviderChangedEventDetail,
): void {
  if (typeof window === 'undefined') {
    return;
  }

  window.dispatchEvent(
    new CustomEvent<WorkstationProviderChangedEventDetail>(
      WORKSTATION_PROVIDER_CHANGED_EVENT,
      { detail },
    ),
  );
}
