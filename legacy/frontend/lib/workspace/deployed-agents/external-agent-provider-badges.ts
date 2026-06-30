import {
  humanizeToken,
  readString,
} from './utils';

export type ExternalProviderBadgeDescriptor = {
  label: string;
  initials: string;
  imageSrc?: string;
};

const EXTERNAL_PROVIDER_BADGES: Record<string, ExternalProviderBadgeDescriptor> = {
  openclaw: {
    label: 'OpenClaw',
    initials: 'OC',
    imageSrc: '/brand-assets/providers/openclaw.svg',
  },
  hermes: {
    label: 'Hermes',
    initials: 'H',
    imageSrc: '/brand-assets/providers/hermes.svg',
  },
  nemoclaw: {
    label: 'NemoClaw',
    initials: 'NC',
    imageSrc: '/brand-assets/providers/nemoclaw.svg',
  },
  custom: {
    label: 'Custom',
    initials: 'C',
  },
  custom_http: {
    label: 'Custom HTTP',
    initials: 'C',
  },
};

export function resolveExternalProviderBadge(providerKind: unknown): ExternalProviderBadgeDescriptor {
  const normalized = readString(providerKind, 'custom').toLowerCase().replace(/[^a-z0-9]+/g, '_');
  return EXTERNAL_PROVIDER_BADGES[normalized] ?? {
    label: humanizeToken(providerKind, 'Custom endpoint'),
    initials: readString(providerKind, 'C').slice(0, 2).toUpperCase(),
  };
}
