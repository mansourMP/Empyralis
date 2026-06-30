'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  BookOpen,
  Bot,
  Car,
  Check,
  DollarSign,
  type LucideIcon,
  MessageSquare,
  PackageCheck,
  Target,
  Utensils,
  Users,
  Wrench,
} from 'lucide-react';

import { EmptyPanel } from '@/lib/ui/empty-panel';
import { ListDetailPanel } from '@/lib/ui/list-detail';
import { AppButton, AppModal, joinClassNames } from '@/lib/ui/primitives';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import type { MarketplacePackageRecord } from '@/lib/workspace/workstation-client';
import { useWorkspaceBoundary } from '@/lib/workspace/workspace-boundary';
import { useWorkspaceServices } from '@/lib/workspace/workspace-services';
import { WorkstationSurfaceRoot } from '@/lib/workspace/workstation-surface-primitives';

const KIND_FILTERS = ['agent_template', 'skill', 'connector', 'bundle'] as const;
const COMPOSER_KINDS = ['app'] as const;
const VERIFICATION_OPTIONS = ['unverified', 'partner', 'verified'] as const;
const REVIEW_OPTIONS = ['pending', 'approved', 'restricted'] as const;
const HEALTH_OPTIONS = ['setup_required', 'healthy', 'degraded'] as const;
const POLICY_OPTIONS = ['governed', 'restricted'] as const;
const MONETIZATION_OPTIONS = ['free', 'metered', 'subscription', 'revenue_share'] as const;

type KindFilter = typeof KIND_FILTERS[number];
type ComposerKind = 'app' | 'provider';

function isVisibleMarketplaceKind(kind: string): boolean {
  const normalized = kind.trim().toLowerCase();
  return (
    normalized === 'agent_template'
    || normalized === 'skill'
    || normalized === 'connector'
    || normalized === 'bundle'
  );
}

function marketplaceKindMatchesFilter(item: MarketplacePackageRecord, filter: KindFilter): boolean {
  const normalized = readString(item.kind).trim().toLowerCase();
  if (filter === 'bundle') {
    return normalized === 'bundle';
  }
  return normalized === filter;
}

function marketplaceApiKindForFilter(filter: KindFilter): string | null {
  return filter === 'agent_template' || filter === 'skill' || filter === 'connector'
    ? filter
    : null;
}

function normalizeMarketplaceKindFilter(value: string | null): KindFilter {
  return KIND_FILTERS.some((filter) => filter === value) ? value as KindFilter : 'agent_template';
}

type BaseComposerDraft = {
  label: string;
  description: string;
  category: string;
  publisherLabel: string;
  publisherWebsite: string;
  docsUrl: string;
  supportUrl: string;
  privacyUrl: string;
  termsUrl: string;
  publisherDomain: string;
  verificationStatus: string;
  reviewState: string;
  healthState: string;
  policyPosture: string;
  monetizationKind: string;
  revenueShareBps: string;
  billingProductId: string;
  settlementProvider: string;
  ledgerKey: string;
  hookKind: string;
  approvalRequired: boolean;
};

type AppComposerDraft = BaseComposerDraft & {
  appId: string;
  hostedUrl: string;
  iconUrl: string;
  version: string;
  permissions: string;
  allowedOrigins: string;
  bridgeContracts: string;
};

type ProviderComposerDraft = BaseComposerDraft & {
  providerId: string;
  defaultModel: string;
  authModes: string;
  privacyPosture: string;
  jurisdiction: string;
  residency: string;
  enterpriseRiskNote: string;
  capabilityLabels: string;
  models: string;
};

type MarketplaceCardView = {
  id: string;
  kind: string;
  name: string;
  description: string;
  category: string;
  verificationStatus: string;
  reviewState: string;
  healthState: string;
  policyPosture: string;
  installed: boolean;
  approvalRequired: boolean;
  monetizationKind: string;
  installCount: number | null;
  runtimeEventCount: number | null;
  docsHref: string;
  openHref: string;
  publisherLabel: string;
  runtimeSurface: string;
  previewOnly: boolean;
  installEligible: boolean;
  installBlockers: string[];
  item: MarketplacePackageRecord;
};

const DEFAULT_APP_DRAFT: AppComposerDraft = {
  label: '',
  description: '',
  category: 'Applications',
  publisherLabel: '',
  publisherWebsite: '',
  docsUrl: '',
  supportUrl: '',
  privacyUrl: '',
  termsUrl: '',
  publisherDomain: '',
  verificationStatus: 'unverified',
  reviewState: 'pending',
  healthState: 'setup_required',
  policyPosture: 'governed',
  monetizationKind: 'free',
  revenueShareBps: '',
  billingProductId: '',
  settlementProvider: '',
  ledgerKey: '',
  hookKind: 'distribution_install',
  approvalRequired: false,
  appId: '',
  hostedUrl: '',
  iconUrl: '',
  version: '1.0.0',
  permissions: '',
  allowedOrigins: '',
  bridgeContracts: '',
};

const DEFAULT_PROVIDER_DRAFT: ProviderComposerDraft = {
  label: '',
  description: '',
  category: 'Models',
  publisherLabel: '',
  publisherWebsite: '',
  publisherDomain: '',
  docsUrl: '',
  supportUrl: '',
  privacyUrl: '',
  termsUrl: '',
  verificationStatus: 'unverified',
  reviewState: 'pending',
  healthState: 'setup_required',
  policyPosture: 'governed',
  monetizationKind: 'free',
  revenueShareBps: '',
  billingProductId: '',
  settlementProvider: '',
  ledgerKey: '',
  hookKind: 'distribution_install',
  approvalRequired: false,
  providerId: '',
  defaultModel: '',
  authModes: 'api_key',
  privacyPosture: '',
  jurisdiction: '',
  residency: '',
  enterpriseRiskNote: '',
  capabilityLabels: '',
  models: '',
};

function readString(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function readNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function readBoolean(value: unknown): boolean {
  return value === true;
}

function readRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function readStringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => readString(item)).filter(Boolean)
    : [];
}

function humanizeToken(value: string): string {
  if (!value) {
    return 'Unknown';
  }
  return value
    .split(/[_\s.-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function packageKindLabel(kind: string): string {
  const normalized = kind.trim().toLowerCase();
  if (normalized === 'provider') {
    return 'Model';
  }
  if (normalized === 'connector') {
    return 'MCP';
  }
  if (normalized === 'mini_app') {
    return 'Mini-app';
  }
  if (normalized === 'agent_template') {
    return 'Agent template';
  }
  if (normalized === 'link') {
    return 'Link';
  }
  if (normalized === 'community') {
    return 'Community';
  }
  if (normalized === 'platform') {
    return 'Platform';
  }
  if (normalized === 'skill') {
    return 'Skill';
  }
  return humanizeToken(normalized || 'package');
}

function normalizeMarketplacePackages(payload: unknown): MarketplacePackageRecord[] {
  if (!payload || typeof payload !== 'object') {
    return [];
  }
  const items = (payload as Record<string, unknown>).items;
  return Array.isArray(items)
    ? items.filter((item): item is MarketplacePackageRecord => Boolean(item) && typeof item === 'object')
    : [];
}

function splitCsvTokens(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

function inferOrigin(urlValue: string): string[] {
  const trimmed = urlValue.trim();
  if (!trimmed) {
    return [];
  }
  try {
    return [new URL(trimmed).origin];
  } catch {
    return [];
  }
}

function parseBridgeContractsInput(value: string): Record<string, string[]> {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .reduce<Record<string, string[]>>((contracts, line) => {
      const [kindPart, typesPart] = line.split(':');
      const kind = readString(kindPart).toLowerCase();
      const types = splitCsvTokens(typesPart || '').map((item) => item.toLowerCase());
      if (kind && types.length) {
        contracts[kind] = types;
      }
      return contracts;
    }, {});
}

function parseProviderModelLines(value: string, defaultModel: string): Record<string, unknown>[] {
  type ProviderModelDraft = {
    id: string;
    label: string;
  };
  const parsed = value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [idPart, labelPart] = line.split('|').map((part) => part.trim());
      const id = readString(idPart);
      if (!id) {
        return null;
      }
      return {
        id,
        label: readString(labelPart, humanizeToken(id)),
      };
    })
    .filter((item): item is ProviderModelDraft => Boolean(item));
  if (parsed.length) {
    return parsed.map((item) => ({ ...item }));
  }
  const fallbackModel = readString(defaultModel);
  return fallbackModel ? [{ id: fallbackModel, label: humanizeToken(fallbackModel) }] : [];
}

function buildMarketplaceCardView(item: MarketplacePackageRecord, index: number): MarketplaceCardView {
  const runtimeTruth = readRecord(item.runtime_truth);
  const publisher = readRecord(item.publisher);
  const onboarding = readRecord(item.onboarding);
  const billing = readRecord(item.billing);
  const analytics = readRecord(item.analytics);
  return {
    id: readString(item.package_id, `marketplace-package-${index}`),
    kind: readString(item.kind, 'package'),
    name: readString(item.label, 'Unnamed package'),
    description: readString(item.description, 'No public description has been added yet.'),
    category: readString(item.category, 'Marketplace'),
    verificationStatus: readString(item.verification_status, 'unverified'),
    reviewState: readString(item.review_state, 'pending'),
    healthState: readString(item.health_state, 'setup_required'),
    policyPosture: readString(runtimeTruth.policy_posture ?? item.policy_posture, 'governed'),
    installed: readBoolean(item.installed),
    approvalRequired: readBoolean(item.approval_required),
    monetizationKind: readString(billing.monetization_kind, 'free'),
    installCount: readNumber(analytics.install_count),
    runtimeEventCount: readNumber(analytics.runtime_event_count),
    docsHref: readString(onboarding.docs_url) || readString(publisher.docs_url) || readString(publisher.website),
    openHref: readString(runtimeTruth.open_href),
    publisherLabel: readString(publisher.label, 'Unknown publisher'),
    runtimeSurface: readString(runtimeTruth.surface, 'distribution'),
    previewOnly: readBoolean(item.preview_only),
    installEligible: readBoolean(item.install_eligible) || readBoolean(item.installed),
    installBlockers: readStringList(item.install_blockers),
    item,
  };
}

function isDeprecatedMarketplacePackage(
  item: MarketplacePackageRecord,
  packagePayload: Record<string, unknown>,
  runtimeTruth: Record<string, unknown>,
): boolean {
  return (
    readBoolean(item.deprecated)
    || readBoolean(packagePayload.deprecated)
    || readString(item.deprecated_at) !== ''
    || readString(packagePayload.deprecated_at) !== ''
    || readString(runtimeTruth.lifecycle).toLowerCase() === 'deprecated'
  );
}

function marketplaceLifecycleLabel(
  item: MarketplacePackageRecord,
  packagePayload: Record<string, unknown>,
  runtimeTruth: Record<string, unknown>,
): string {
  if (isDeprecatedMarketplacePackage(item, packagePayload, runtimeTruth)) {
    return 'Deprecated';
  }
  if (readBoolean(item.preview_only) || readString(packagePayload.release_channel).toLowerCase() === 'preview') {
    return 'Preview';
  }
  return 'Active';
}

function marketplacePrimaryActionLabel(card: MarketplaceCardView): string {
  if (card.previewOnly) {
    return 'Preview only';
  }
  if (!card.installed && !card.installEligible) {
    return 'Blocked';
  }
  if (card.installed) {
    if (card.kind === 'agent_template') {
      return 'Open in Studio';
    }
    if (card.kind === 'app' || card.kind === 'mini_app') {
      return 'Open app';
    }
    return 'Open setup';
  }
  if (card.kind === 'agent_template') {
    return 'Use template';
  }
  if (card.kind === 'mini_app' || card.kind === 'app') {
    return 'Install app';
  }
  return 'Install';
}

function marketplaceInstallTargetLabel(kind: string): string {
  switch (kind) {
    case 'agent_template':
      return 'Creates a draft agent in Studio.';
    case 'connector':
      return 'Opens setup in Integrations.';
    case 'skill':
      return 'Becomes a callable tool for Sage and eligible agents.';
    case 'mini_app':
      return 'Installs as a workspace mini-app.';
    case 'app':
      return 'Installs as a workspace app.';
    case 'provider':
      return 'Adds model options for Sage and Studio.';
    default:
      return 'Adds a workspace capability.';
  }
}

function marketplaceBillingPilotLabel(billing: Record<string, unknown>, monetizationKind: string): string {
  const paymentProcessingLive = readBoolean(billing.payment_processing_live);
  const monetization = monetizationKind || readString(billing.monetization_kind, 'free');
  const monetizationLabel = humanizeToken(monetization);
  if (monetization === 'free') {
    return paymentProcessingLive ? 'Free' : 'Free; no live billing';
  }
  if (monetization === 'metered') {
    return paymentProcessingLive ? 'Usage-based' : 'Usage-based; not charging yet';
  }
  if (monetization === 'subscription') {
    return paymentProcessingLive ? 'Subscription' : 'Subscription metadata only';
  }
  if (monetization === 'revenue_share') {
    return paymentProcessingLive ? 'Revenue share' : 'Revenue-share metadata only';
  }
  return monetizationLabel;
}

type MarketplaceSelectedDetails = {
  item: MarketplacePackageRecord;
  publisher: Record<string, unknown>;
  onboarding: Record<string, unknown>;
  billing: Record<string, unknown>;
  analytics: Record<string, unknown>;
  runtimeTruth: Record<string, unknown>;
  install: Record<string, unknown>;
  packagePayload: Record<string, unknown>;
  accountingHook: Record<string, unknown>;
  permissionList: string[];
  allowedOrigins: string[];
  providerAuthModes: string[];
  providerCapabilities: string[];
  appBridgeContracts: [string, unknown][];
  providerModels: Record<string, unknown>[];
};

type MarketplaceDetailSection = {
  title: string;
  body?: string;
  icon: LucideIcon;
  items?: string[];
};

type MarketplaceAppMetadataItem = {
  label: string;
  value: string;
  href?: string;
};

function marketplaceAppMetadataItems(card: MarketplaceCardView, details: MarketplaceSelectedDetails): MarketplaceAppMetadataItem[] {
  if (card.kind !== 'app' && card.kind !== 'mini_app') {
    return [];
  }
  const runtimeType = readString(details.packagePayload.runtime_type, readString(details.runtimeTruth.runtime_type, 'community'));
  const destinationUrl = readString(details.packagePayload.destination_url);
  return [
    { label: 'Runtime', value: humanizeToken(runtimeType) },
    { label: 'Category', value: card.category },
    runtimeType === 'community' ? { label: 'Publisher', value: card.publisherLabel } : null,
    runtimeType === 'community' ? { label: 'Verification', value: humanizeToken(card.verificationStatus) } : null,
    runtimeType === 'link' && destinationUrl ? { label: 'URL', value: destinationUrl, href: destinationUrl } : null,
  ].filter((item): item is MarketplaceAppMetadataItem => Boolean(item));
}

function buildMarketplaceSelectedDetails(item: MarketplacePackageRecord): MarketplaceSelectedDetails {
  const publisher = readRecord(item.publisher);
  const onboarding = readRecord(item.onboarding);
  const billing = readRecord(item.billing);
  const runtimeTruth = readRecord(item.runtime_truth);
  const analytics = readRecord(item.analytics);
  const install = readRecord(item.install);
  const packagePayload = readRecord(item.package);
  const accountingHook = readRecord(billing.accounting_hook);
  return {
    item,
    publisher,
    onboarding,
    billing,
    runtimeTruth,
    analytics,
    install,
    packagePayload,
    accountingHook,
    permissionList: readStringList(packagePayload.permissions),
    allowedOrigins: readStringList(packagePayload.allowed_origins),
    providerAuthModes: readStringList(packagePayload.auth_modes),
    providerCapabilities: readStringList(packagePayload.capability_labels),
    appBridgeContracts: Object.entries(readRecord(packagePayload.bridge_contracts)).filter(([, value]) => Array.isArray(value)),
    providerModels: Array.isArray(packagePayload.models)
      ? packagePayload.models.filter((model): model is Record<string, unknown> => Boolean(model) && typeof model === 'object')
      : [],
  };
}

function nonEmptyRecord(value: unknown): Record<string, unknown> | null {
  const record = readRecord(value);
  return Object.keys(record).length > 0 ? record : null;
}

function marketplacePackageSpecificPayload(card: MarketplaceCardView, details: MarketplaceSelectedDetails): Record<string, unknown> {
  if (card.kind === 'agent_template') {
    return nonEmptyRecord(details.packagePayload.agent_template) ?? details.packagePayload;
  }
  if (card.kind === 'mini_app' || card.kind === 'app') {
    return nonEmptyRecord(details.packagePayload.app) ?? details.packagePayload;
  }
  if (card.kind === 'connector') {
    return nonEmptyRecord(details.packagePayload.connector) ?? details.packagePayload;
  }
  if (card.kind === 'skill') {
    return nonEmptyRecord(details.packagePayload.skill) ?? details.packagePayload;
  }
  if (card.kind === 'provider') {
    return nonEmptyRecord(details.packagePayload.provider) ?? details.packagePayload;
  }
  return details.packagePayload;
}

function marketplaceBlockerLabel(blocker: string): string {
  switch (blocker) {
    case 'preview_only':
      return 'Preview only; installation is not enabled yet.';
    case 'review_not_approved':
      return 'Waiting for marketplace review.';
    case 'verification_required':
      return 'Publisher verification is required.';
    case 'policy_restricted':
      return 'Restricted by marketplace policy.';
    case 'manual_approval_required':
      return 'Owner approval is required before install.';
    case 'excessive_permissions_requested':
      return 'Requests too much access for automatic install.';
    case 'excessive_domain_scope':
      return 'Requests a broad domain scope.';
    case 'unsafe_local_runtime_permission_combo':
      return 'Requests unsafe local-computer permissions.';
    case 'owner_resource_boundary_violation':
      return 'Requests owner resources that require review.';
    case 'missing_app_icon':
      return 'Link and community apps must provide an icon.';
    case 'missing_destination_url':
      return 'Link apps must provide a destination URL.';
    case 'missing_hosted_url':
      return 'Community apps must provide a hosted URL.';
    case 'missing_platform_route':
      return 'Platform apps must provide an internal route.';
    default:
      return humanizeToken(blocker);
  }
}

function marketplaceSetupSummary(card: MarketplaceCardView, details: MarketplaceSelectedDetails): string {
  const specificPayload = marketplacePackageSpecificPayload(card, details);
  if (card.previewOnly) {
    return 'Preview only. You can inspect it, but installation is disabled.';
  }
  if (!card.installed && !card.installEligible && card.installBlockers.length > 0) {
    return marketplaceBlockerLabel(card.installBlockers[0]);
  }
  if (card.kind === 'agent_template') {
    const requiredConnectors = readStringList(specificPayload.required_connectors);
    const checklist = readStringList(specificPayload.launch_checklist);
    if (requiredConnectors.length) {
      return `Connect ${requiredConnectors.slice(0, 3).join(', ')} before launch.`;
    }
    if (checklist.length) {
      return checklist[0];
    }
    return 'Customize the draft before launch.';
  }
  if (card.kind === 'connector') {
    const authModes = readStringList(specificPayload.auth_modes);
    return authModes.length ? `Connect with ${authModes.map(humanizeToken).join(' or ')}.` : 'Connect the account in Integrations.';
  }
  if (card.kind === 'skill') {
    const permissions = readStringList(specificPayload.permissions);
    return permissions.length ? `Requires ${permissions.slice(0, 3).map(humanizeToken).join(', ')}.` : 'No extra tool permissions declared.';
  }
  if (card.kind === 'provider') {
    const authModes = details.providerAuthModes.length ? details.providerAuthModes : ['api_key'];
    return `Add credentials with ${authModes.map(humanizeToken).join(' or ')}.`;
  }
  if (card.kind === 'app' || card.kind === 'mini_app') {
    if (details.permissionList.length) {
      return `Uses ${details.permissionList.slice(0, 3).map(humanizeToken).join(', ')}.`;
    }
    return 'No extra app permissions declared.';
  }
  return 'Review setup before enabling.';
}

function marketplaceIconForCard(card: MarketplaceCardView): LucideIcon {
  const name = card.name.toLowerCase();
  const category = card.category.toLowerCase();
  if (name.includes('restaurant') || name.includes('order') || category.includes('food')) {
    return Utensils;
  }
  if (name.includes('auto') || name.includes('car') || name.includes('parts')) {
    return Car;
  }
  if (name.includes('flashcard') || name.includes('study') || name.includes('faq') || name.includes('docs')) {
    return BookOpen;
  }
  if (name.includes('support') || name.includes('chat') || name.includes('message')) {
    return MessageSquare;
  }
  if (card.kind === 'agent_template') {
    return Bot;
  }
  return PackageCheck;
}

function marketplaceCardBadges(card: MarketplaceCardView, details: MarketplaceSelectedDetails): string[] {
  const specificPayload = marketplacePackageSpecificPayload(card, details);
  if (card.kind === 'app' || card.kind === 'mini_app') {
    const runtimeType = readString(specificPayload.runtime_type, 'community');
    return [humanizeToken(runtimeType), humanizeToken(card.reviewState)].filter(Boolean);
  }
  const requiredConnectors = readStringList(specificPayload.required_connectors);
  const setupToken = card.previewOnly
    ? 'Preview'
    : requiredConnectors.length
      ? `Needs ${humanizeToken(requiredConnectors[0])}`
      : card.healthState === 'setup_required'
        ? 'Needs setup'
        : 'Ready';
  const trustToken = card.verificationStatus === 'verified'
    ? 'Verified'
    : humanizeToken(card.verificationStatus);
  const costToken = marketplaceBillingPilotLabel(details.billing, card.monetizationKind).replace('; no live billing', '');
  return [setupToken, costToken, trustToken].filter(Boolean);
}

function marketplaceWhatAddsSummary(card: MarketplaceCardView, details: MarketplaceSelectedDetails): string {
  if (card.kind === 'agent_template') {
    const proof = readAgentTemplateProofDetails(details.packagePayload);
    return proof.does === 'Business behavior is defined in this template contract.' ? card.description : proof.does;
  }
  if (card.kind === 'connector') {
    const actions = readStringList(details.packagePayload.actions);
    return actions.length
      ? `Connects ${card.name} so Sage or agents can use ${actions.slice(0, 3).map(humanizeToken).join(', ')}.`
      : `Connects ${card.name} to this workspace.`;
  }
  if (card.kind === 'skill') {
    const contracts = Object.keys(readRecord(details.packagePayload.tool_contracts));
    return contracts.length
      ? `Adds ${contracts.slice(0, 3).map(humanizeToken).join(', ')} tool capability.`
      : `Adds ${card.name} as a callable tool.`;
  }
  if (card.kind === 'provider') {
    const defaultModel = readString(details.packagePayload.default_model);
    return defaultModel ? `Adds ${defaultModel} and related models as routing options.` : 'Adds a model provider for Sage and Studio.';
  }
  if (card.kind === 'app' || card.kind === 'mini_app') {
    return card.description;
  }
  return card.description;
}

function marketplaceDestinationSummary(card: MarketplaceCardView, details: MarketplaceSelectedDetails): string {
  const base = marketplaceInstallTargetLabel(card.kind);
  if (card.installed && readString(details.install.open_href || details.runtimeTruth.open_href)) {
    return `${base} This item is already installed.`;
  }
  return base;
}

function marketplaceCreatedItems(card: MarketplaceCardView, details: MarketplaceSelectedDetails): string[] {
  const specificPayload = marketplacePackageSpecificPayload(card, details);
  if (card.kind === 'agent_template') {
    const checklist = readStringList(specificPayload.launch_checklist);
    const connector = readStringList(specificPayload.required_connectors)[0];
    return [
      `${card.name} handler and private test conversation`,
      checklist.length ? `Setup checklist for ${connector ? humanizeToken(connector) : 'launch'}` : 'Studio draft agent',
      'Workspace-visible activity and cost tracking',
    ];
  }
  if (card.kind === 'mini_app' || card.kind === 'app') {
    return [
      `${card.name} app entry in this workspace`,
      details.permissionList.length ? `${humanizeToken(details.permissionList[0])} permission request` : 'Workspace install record',
    ];
  }
  return [marketplaceInstallTargetLabel(card.kind)];
}

function marketplaceSetupItems(card: MarketplaceCardView, details: MarketplaceSelectedDetails): string[] {
  const specificPayload = marketplacePackageSpecificPayload(card, details);
  if (card.previewOnly || (!card.installed && !card.installEligible && card.installBlockers.length > 0)) {
    return card.installBlockers.length
      ? card.installBlockers.map(marketplaceBlockerLabel)
      : ['Preview only; installation is not enabled yet.'];
  }
  if (card.kind === 'agent_template') {
    const checklist = readStringList(specificPayload.launch_checklist);
    const connectors = readStringList(specificPayload.required_connectors).map((connector) => `Connect ${humanizeToken(connector)}`);
    return [...connectors, ...checklist].filter(Boolean).slice(0, 4);
  }
  if (card.kind === 'app' || card.kind === 'mini_app') {
    return details.permissionList.length
      ? details.permissionList.slice(0, 4).map((permission) => `Allow ${humanizeToken(permission)}`)
      : ['No extra app setup declared.'];
  }
  return [marketplaceSetupSummary(card, details)];
}

function marketplaceCostSummary(card: MarketplaceCardView, details: MarketplaceSelectedDetails): string {
  if (card.kind === 'app' || card.kind === 'mini_app') {
    return 'No platform payment processing is attached to this app listing.';
  }
  const label = marketplaceBillingPilotLabel(details.billing, card.monetizationKind);
  if (card.monetizationKind === 'free') {
    return 'Free. Uses credits for messages and actions.';
  }
  return label;
}

function marketplaceDetailSections(card: MarketplaceCardView, details: MarketplaceSelectedDetails): MarketplaceDetailSection[] {
  if (card.kind === 'app' || card.kind === 'mini_app') {
    return [];
  }
  const sections: MarketplaceDetailSection[] = [
    {
      title: 'What it does',
      body: marketplaceWhatAddsSummary(card, details),
      icon: Target,
    },
    {
      title: 'Where it goes',
      items: marketplaceCreatedItems(card, details),
      icon: PackageCheck,
    },
    {
      title: 'Setup needed',
      items: marketplaceSetupItems(card, details),
      icon: Wrench,
    },
    {
      title: 'Trust and safety',
      body: card.kind === 'agent_template'
        ? 'Creates a governed Studio draft. Workspace access and launch approvals stay controlled by your workspace settings.'
        : 'Install access, permissions, and approvals stay governed by your workspace settings.',
      icon: Users,
    },
    {
      title: 'Cost',
      body: marketplaceCostSummary(card, details),
      icon: DollarSign,
    },
  ];
  return sections;
}

function marketplaceTrustTokens(card: MarketplaceCardView): string[] {
  const tokens = [
    humanizeToken(card.verificationStatus),
    humanizeToken(card.reviewState),
    humanizeToken(card.healthState),
    `Policy: ${humanizeToken(card.policyPosture)}`,
  ];
  if (card.approvalRequired) {
    tokens.push('Requires approval');
  }
  if (card.previewOnly) {
    tokens.push('Preview');
  }
  return tokens;
}

type AgentTemplateProofSnapshot = {
  does: string;
  channels: string[];
  dataNeeds: string[];
  moneyModel: string;
  runtimeTier: string;
  safetyPolicy: string;
};

function readAgentTemplateProofDetails(packagePayload: Record<string, unknown>): AgentTemplateProofSnapshot {
  const agentTemplatePayload = nonEmptyRecord(packagePayload.agent_template) ?? packagePayload;
  const envelope = readRecord(agentTemplatePayload.context_envelope);
  const proofContract = readRecord(envelope.proof_agent_seed_contract);
  const channels = Array.isArray(proofContract.channels)
    ? proofContract.channels
      .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
      .map((item) => readString(item.label, readString(item.channel_key)))
      .filter(Boolean)
    : [];
  const dataNeeds = Array.isArray(proofContract.default_data_sources)
    ? proofContract.default_data_sources
      .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
      .map((item) => readString(item.label, readString(item.source_id)))
      .filter(Boolean)
    : [];
  const monetizationHint = readRecord(proofContract.monetization_hint);
  const runtimeTierRecommendation = readRecord(proofContract.runtime_tier_recommendation);
  const approvalPolicy = readRecord(proofContract.approval_policy);
  const fallbackMoneyModel = readString(monetizationHint.kind, readString(agentTemplatePayload.specialist_kind, 'service'));
  const fallbackOffer = readString(monetizationHint.suggested_offer);
  const moneyModel = fallbackOffer || (fallbackMoneyModel ? humanizeToken(fallbackMoneyModel) : 'Revenue model pending');
  const runtimeTierToken = readString(runtimeTierRecommendation.tier, readString(agentTemplatePayload.runtime_tier, 'hosted_secure'));
  const runtimeTier = runtimeTierToken ? humanizeToken(runtimeTierToken) : 'Hosted secure';
  const safetyPolicy = readString(approvalPolicy.default_mode)
    ? `${humanizeToken(readString(approvalPolicy.default_mode))} (${readStringList(approvalPolicy.owner_approval_required_for).length} owner-reviewed actions)`
    : 'Owner review policy defined in template';
  return {
    does: readString(proofContract.description, readString(packagePayload.description, 'Business behavior is defined in this template contract.')),
    channels: channels.length ? channels : readStringList(agentTemplatePayload.required_connectors),
    dataNeeds: dataNeeds.length ? dataNeeds : readStringList(agentTemplatePayload.required_connectors),
    moneyModel,
    runtimeTier,
    safetyPolicy,
  };
}

function MarketplaceSkeleton() {
  return (
    <div className="marketplace-pane__grid">
      {Array.from({ length: 6 }).map((_, index) => (
        <article key={`marketplace-skeleton-${index}`} className="marketplace-pane__card">
          <div className="marketplace-pane__card-copy">
            <div className="marketplace-pane__meta-row">
              <SkeletonBlock height="1.4rem" width="5rem" />
              <SkeletonBlock height="1.4rem" width="6rem" />
            </div>
            <SkeletonBlock height="1.25rem" width="10rem" />
            <SkeletonBlock height="3.5rem" />
            <div className="marketplace-pane__status-row">
              <SkeletonBlock height="1.25rem" width="5rem" />
              <SkeletonBlock height="1.25rem" width="4.5rem" />
              <SkeletonBlock height="1.25rem" width="4rem" />
            </div>
            <div className="marketplace-pane__stats-row">
              <SkeletonBlock height="1.15rem" width="4rem" />
              <SkeletonBlock height="1.15rem" width="6rem" />
            </div>
          </div>
          <div className="marketplace-pane__card-actions">
            <SkeletonBlock height="2.5rem" />
            <SkeletonBlock height="2.5rem" />
          </div>
        </article>
      ))}
    </div>
  );
}

function MarketplaceField({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="marketplace-pane__field">
      <span className="marketplace-pane__field-label">{label}</span>
      {children}
      {hint ? <span className="marketplace-pane__field-hint">{hint}</span> : null}
    </label>
  );
}

function MarketplacePackageDetail({
  card,
  details,
  installingPackageId,
  onInstall,
  onOpenHref,
}: {
  card: MarketplaceCardView;
  details: MarketplaceSelectedDetails;
  installingPackageId: string | null;
  onInstall: (packageId: string) => void;
  onOpenHref: (href: string) => void;
}) {
  const DetailIcon = marketplaceIconForCard(card);
  const detailSections = marketplaceDetailSections(card, details);
  const appMetadataItems = marketplaceAppMetadataItems(card, details);
  const appIconUrl = readString(details.packagePayload.icon_url);

  return (
    <div className="marketplace-pane__detail-stack">
      <div className="marketplace-pane__detail-hero">
        <div className="marketplace-pane__detail-icon" aria-hidden="true">
          {appIconUrl ? <img src={appIconUrl} alt="" /> : <DetailIcon />}
        </div>
        <div className="marketplace-pane__detail-hero-copy">
          <strong className="marketplace-pane__detail-hero-title">{card.name}</strong>
          <div className="marketplace-pane__detail-kind-row">
            <span className={joinClassNames('marketplace-pane__kind-pill', `marketplace-pane__kind-pill--${card.kind}`)}>
              {packageKindLabel(card.kind)}
            </span>
            {card.previewOnly ? (
              <span className="marketplace-pane__status-badge marketplace-pane__status-badge--preview">
                Preview
              </span>
            ) : card.installed ? (
              <span className="marketplace-pane__status-badge marketplace-pane__status-badge--installed">
                Installed
              </span>
            ) : (
              <span className="marketplace-pane__status-badge marketplace-pane__status-badge--approved">
                {marketplaceLifecycleLabel(details.item, details.packagePayload, details.runtimeTruth)}
              </span>
            )}
          </div>
          <p className="marketplace-pane__detail-description">{card.description}</p>
        </div>
      </div>

      {appMetadataItems.length ? (
        <div className="marketplace-pane__detail-grid" aria-label="App details">
          {appMetadataItems.map((item) => (
            <div key={`${item.label}:${item.value}`} className="marketplace-pane__detail-item">
              <span className="marketplace-pane__detail-label">{item.label}</span>
              {item.href ? (
                <a className="marketplace-pane__detail-value" href={item.href} target="_blank" rel="noreferrer">
                  {item.value}
                </a>
              ) : (
                <span className="marketplace-pane__detail-value">{item.value}</span>
              )}
            </div>
          ))}
        </div>
      ) : null}

      <div className="marketplace-pane__detail-section-list">
        {detailSections.map((section) => {
          const SectionIcon = section.icon;
          return (
            <section key={section.title} className="marketplace-pane__detail-section">
              <div className="marketplace-pane__detail-section-icon" aria-hidden="true">
                <SectionIcon />
              </div>
              <div className="marketplace-pane__detail-section-copy">
                <strong className="marketplace-pane__detail-title">{section.title}</strong>
                {section.body ? (
                  <p className="marketplace-pane__panel-copy">{section.body}</p>
                ) : null}
                {section.items?.length ? (
                  <div className="marketplace-pane__detail-checklist">
                    {section.items.map((item) => (
                      <div key={item} className="marketplace-pane__detail-checklist-item">
                        <Check aria-hidden="true" />
                        <span>{item}</span>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            </section>
          );
        })}
      </div>

      <div className="marketplace-pane__detail-footer">
        <button
          type="button"
          className={joinClassNames(
            'marketplace-pane__link-button',
            card.installed && 'marketplace-pane__link-button--installed',
            card.previewOnly && 'marketplace-pane__link-button--preview',
          )}
          disabled={card.previewOnly || (!card.installed && !card.installEligible) || installingPackageId === card.id}
          onClick={() => {
            if (card.previewOnly) {
              return;
            }
            if (card.installed && card.openHref) {
              onOpenHref(card.openHref);
              return;
            }
            onInstall(card.id);
          }}
        >
          {installingPackageId === card.id
            ? 'Installing...'
            : marketplacePrimaryActionLabel(card)}
        </button>
        {card.docsHref ? (
          <a
            href={card.docsHref}
            target="_blank"
            rel="noreferrer"
            className="marketplace-pane__bookmark-link"
            aria-label="Open publisher docs"
          >
            Docs
          </a>
        ) : null}
      </div>
    </div>
  );
}

export function MarketplacePane() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const services = useWorkspaceServices();
  const { hasCapability } = useWorkspaceBoundary();
  const requestedKindFilter = normalizeMarketplaceKindFilter(searchParams.get('category'));
  const [kindFilter, setKindFilter] = useState<KindFilter>(requestedKindFilter);
  const [items, setItems] = useState<MarketplacePackageRecord[]>([]);
  const [submissionItems, setSubmissionItems] = useState<MarketplacePackageRecord[]>([]);
  const [selectedPackageId, setSelectedPackageId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [installingPackageId, setInstallingPackageId] = useState<string | null>(null);
  const [composerKind, setComposerKind] = useState<ComposerKind>('app');
  const [appDraft, setAppDraft] = useState<AppComposerDraft>(DEFAULT_APP_DRAFT);
  const [providerDraft, setProviderDraft] = useState<ProviderComposerDraft>(DEFAULT_PROVIDER_DRAFT);
  const [composerError, setComposerError] = useState<string | null>(null);
  const [composerStatus, setComposerStatus] = useState<string | null>(null);
  const [submittingComposer, setSubmittingComposer] = useState(false);
  const [reviewingPackageId, setReviewingPackageId] = useState<string | null>(null);
  const [showDeveloperRegistration, setShowDeveloperRegistration] = useState(false);
  const canPublishPackages = hasCapability('workspace_admin_enabled');
  const activeRequestControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    setKindFilter((current) => (current === requestedKindFilter ? current : requestedKindFilter));
  }, [requestedKindFilter]);

  const loadMarketplacePackages = useCallback(async (requestedKind: KindFilter) => {
    setLoading(true);
    setError(null);
    activeRequestControllerRef.current?.abort();
    const requestController = new AbortController();
    activeRequestControllerRef.current = requestController;
    try {
      const payload = await services.client.listMarketplacePackages({
        kind: marketplaceApiKindForFilter(requestedKind),
      });
      if (requestController.signal.aborted || activeRequestControllerRef.current !== requestController) {
        return [];
      }
      const nextItems = normalizeMarketplacePackages(payload);
      setItems(nextItems);
      return nextItems;
    } catch (loadError) {
      if (requestController.signal.aborted || activeRequestControllerRef.current !== requestController) {
        return [];
      }
      setError(loadError instanceof Error ? loadError.message : 'Marketplace items could not be loaded.');
      setItems([]);
      return [];
    } finally {
      if (activeRequestControllerRef.current === requestController) {
        activeRequestControllerRef.current = null;
      }
      if (!requestController.signal.aborted) {
        setLoading(false);
      }
    }
  }, [services.client]);

  const loadMarketplaceAppSubmissions = useCallback(async () => {
    if (!canPublishPackages) {
      setSubmissionItems([]);
      return [];
    }
    try {
      const payload = await services.client.listMarketplaceAppSubmissions();
      const nextItems = normalizeMarketplacePackages(payload);
      setSubmissionItems(nextItems);
      return nextItems;
    } catch {
      setSubmissionItems([]);
      return [];
    }
  }, [canPublishPackages, services.client]);

  useEffect(() => {
    void loadMarketplacePackages(kindFilter);
    return () => {
      activeRequestControllerRef.current?.abort();
      activeRequestControllerRef.current = null;
    };
  }, [kindFilter, loadMarketplacePackages]);

  useEffect(() => {
    if (showDeveloperRegistration) {
      void loadMarketplaceAppSubmissions();
    }
  }, [loadMarketplaceAppSubmissions, showDeveloperRegistration]);

  useEffect(() => {
    if (!items.length) {
      if (selectedPackageId !== null) {
        setSelectedPackageId(null);
      }
      return;
    }
    if (selectedPackageId === null) {
      return;
    }
    const visibleItems = items.filter((item) => marketplaceKindMatchesFilter(item, kindFilter));
    const hasSelected = visibleItems.some((item) => readString(item.package_id) === selectedPackageId);
    if (!hasSelected) {
      setSelectedPackageId(null);
    }
  }, [items, kindFilter, selectedPackageId]);

  async function handleInstall(packageId: string) {
    setInstallingPackageId(packageId);
    setError(null);
    const keepDetailOpen = selectedPackageId === packageId;
    try {
      const payload = await services.client.installMarketplacePackage({ packageId });
      const runtimeTruth = readRecord(payload?.runtime_truth);
      const openHref = readString(runtimeTruth.open_href);
      await loadMarketplacePackages(kindFilter);
      setSelectedPackageId(keepDetailOpen ? packageId : null);
      if (openHref) {
        router.push(openHref);
      }
    } catch (installError) {
      setError(installError instanceof Error ? installError.message : 'Install failed.');
    } finally {
      setInstallingPackageId(null);
    }
  }

  async function handleReviewSubmission(packageId: string, approved: boolean) {
    setReviewingPackageId(packageId);
    setError(null);
    try {
      await services.client.reviewMarketplaceAppSubmission({
        packageId,
        approved,
        reason: approved ? 'Approved for public community listing.' : 'Rejected by workspace review.',
        verificationStatus: 'unverified',
      });
      await Promise.all([
        loadMarketplacePackages(kindFilter),
        loadMarketplaceAppSubmissions(),
      ]);
    } catch (reviewError) {
      setError(reviewError instanceof Error ? reviewError.message : 'Review action failed.');
    } finally {
      setReviewingPackageId(null);
    }
  }

  async function handleComposerSubmit() {
    setComposerError(null);
    setComposerStatus(null);
    setSubmittingComposer(true);
    try {
      if (composerKind === 'app') {
        const payload = {
          label: appDraft.label.trim(),
          description: appDraft.description.trim(),
          category: appDraft.category.trim() || 'Applications',
          publisher: {
            label: appDraft.publisherLabel.trim() || undefined,
            website: appDraft.publisherWebsite.trim() || undefined,
            docs_url: appDraft.docsUrl.trim() || undefined,
            support_url: appDraft.supportUrl.trim() || undefined,
          },
          onboarding: {
            docs_url: appDraft.docsUrl.trim() || undefined,
            support_url: appDraft.supportUrl.trim() || undefined,
            privacy_url: appDraft.privacyUrl.trim() || undefined,
            terms_url: appDraft.termsUrl.trim() || undefined,
          },
          app: {
            app_id: appDraft.appId.trim(),
            hosted_url: appDraft.hostedUrl.trim(),
            icon_url: appDraft.iconUrl.trim(),
            runtime_type: 'community',
            support_url: appDraft.supportUrl.trim() || undefined,
            privacy_url: appDraft.privacyUrl.trim() || undefined,
            terms_url: appDraft.termsUrl.trim() || undefined,
            version: appDraft.version.trim() || '1.0.0',
            permissions: splitCsvTokens(appDraft.permissions),
            allowed_origins: splitCsvTokens(appDraft.allowedOrigins).length
              ? splitCsvTokens(appDraft.allowedOrigins)
              : inferOrigin(appDraft.hostedUrl),
            bridge_contracts: parseBridgeContractsInput(appDraft.bridgeContracts),
          },
        };
        await services.client.registerMarketplaceApp(payload);
        await Promise.all([
          loadMarketplacePackages(kindFilter),
          loadMarketplaceAppSubmissions(),
        ]);
        setSelectedPackageId(null);
        setComposerStatus('App submitted for review.');
        setAppDraft(DEFAULT_APP_DRAFT);
        return;
      }

      const models = parseProviderModelLines(providerDraft.models, providerDraft.defaultModel.trim());
      const fallbackModelId = readString(providerDraft.defaultModel, readString(models[0]?.id));
      const payload = {
        label: providerDraft.label.trim(),
        description: providerDraft.description.trim(),
        category: providerDraft.category.trim() || 'Models',
        publisher: {
          label: providerDraft.publisherLabel.trim() || undefined,
          website: providerDraft.publisherWebsite.trim() || undefined,
          publisher_domain: providerDraft.publisherDomain.trim() || undefined,
          docs_url: providerDraft.docsUrl.trim() || undefined,
          support_url: providerDraft.supportUrl.trim() || undefined,
        },
        onboarding: {
          docs_url: providerDraft.docsUrl.trim() || undefined,
          support_url: providerDraft.supportUrl.trim() || undefined,
          privacy_url: providerDraft.privacyUrl.trim() || undefined,
          terms_url: providerDraft.termsUrl.trim() || undefined,
        },
        verification_status: providerDraft.verificationStatus,
        review_state: providerDraft.reviewState,
        health_state: providerDraft.healthState,
        policy_posture: providerDraft.policyPosture,
        approval_required: providerDraft.approvalRequired,
        billing: {
          monetization_kind: providerDraft.monetizationKind,
          billing_product_id: providerDraft.billingProductId.trim() || undefined,
          settlement_provider: providerDraft.settlementProvider.trim() || undefined,
          revenue_share_bps: providerDraft.revenueShareBps.trim() ? Number(providerDraft.revenueShareBps.trim()) : 0,
          accounting_hook: {
            ledger_key: providerDraft.ledgerKey.trim() || undefined,
            hook_kind: providerDraft.hookKind.trim() || 'distribution_install',
          },
        },
        provider: {
          provider_id: providerDraft.providerId.trim(),
          default_model: fallbackModelId || undefined,
          auth_modes: splitCsvTokens(providerDraft.authModes).length
            ? splitCsvTokens(providerDraft.authModes)
            : ['api_key'],
          privacy_posture: providerDraft.privacyPosture.trim() || undefined,
          jurisdiction: providerDraft.jurisdiction.trim() || undefined,
          residency: providerDraft.residency.trim() || undefined,
          enterprise_risk_note: providerDraft.enterpriseRiskNote.trim() || undefined,
          capability_labels: splitCsvTokens(providerDraft.capabilityLabels),
          models,
        },
      };
      const response = await services.client.registerMarketplaceProvider(payload);
      const packageId = readString(response?.package_id);
      await loadMarketplacePackages(kindFilter);
      setSelectedPackageId(packageId || null);
      setComposerStatus('Governed provider package registered. Install it into workspace integrations when you are ready.');
      setProviderDraft(DEFAULT_PROVIDER_DRAFT);
    } catch (submitError) {
      setComposerError(submitError instanceof Error ? submitError.message : 'Marketplace registration failed.');
    } finally {
      setSubmittingComposer(false);
    }
  }

  const displayedItems = useMemo(
    () => items.filter((item) => marketplaceKindMatchesFilter(item, kindFilter)),
    [items, kindFilter],
  );

  const renderedCards = useMemo(
    () => displayedItems.map((item, index) => buildMarketplaceCardView(item, index)),
    [displayedItems],
  );

  const selectedPackage = useMemo(() => {
    const selected = renderedCards.find((item) => item.id === selectedPackageId);
    return selected || null;
  }, [renderedCards, selectedPackageId]);

  const selectedDetails = useMemo(() => {
    if (!selectedPackage) {
      return null;
    }
    return buildMarketplaceSelectedDetails(selectedPackage.item);
  }, [selectedPackage]);

  return (
    <WorkstationSurfaceRoot surface="marketplace">
      <section className="marketplace-pane" data-marketplace-layout="split" aria-label="Discover">
        <div className="marketplace-pane__split">
          <div className="marketplace-pane__catalog-scroll" aria-label="Discover catalog">
            <div className="marketplace-pane__catalog-stack">
              <div className="marketplace-pane__catalog-head">
                <div className="marketplace-pane__catalog-title-group">
                  <h1 className="marketplace-pane__catalog-title">Discover</h1>
                  <p className="marketplace-pane__catalog-subtitle">
                    Browse agent templates, skills, MCP servers, and bundles.
                  </p>
                </div>
              </div>
              <div className="marketplace-pane__browse-panel">
                {error && !loading ? (
                  <div className="marketplace-pane__error">
                    <div className="marketplace-pane__error-copy">
                      <strong>Discover could not refresh.</strong>
                      <span>Check the connection, then retry.</span>
                    </div>
                    <AppButton type="button" tone="secondary" onClick={() => { void loadMarketplacePackages(kindFilter); }}>
                      Retry
                    </AppButton>
                  </div>
                ) : null}

                {loading ? (
                  <MarketplaceSkeleton />
                ) : renderedCards.length === 0 ? (
                  <EmptyPanel
                  title={kindFilter === 'bundle' ? 'No bundles are available yet.' : 'No catalog items are available yet.'}
                    body={kindFilter === 'bundle'
                      ? 'Bundles will combine templates, tools, MCP connectors, setup steps, and required access into one installable package.'
                      : 'Discover will show governed capabilities that can be added to this workspace.'}
                  />
                ) : (
                  <div className="marketplace-pane__grid">
                    {renderedCards.map((card) => {
                      const installing = installingPackageId === card.id;
                      const primaryLabel = marketplacePrimaryActionLabel(card);
                      const cardDetails = buildMarketplaceSelectedDetails(card.item);
                      const PackageIcon = marketplaceIconForCard(card);
                      return (
                        <article
                          key={card.id}
                          className={joinClassNames(
                            'marketplace-pane__card',
                            selectedPackage?.id === card.id && 'marketplace-pane__card--selected',
                          )}
                          onClick={() => setSelectedPackageId(card.id)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ') {
                              event.preventDefault();
                              setSelectedPackageId(card.id);
                            }
                          }}
                          role="button"
                          tabIndex={0}
                        >
                          <div className="marketplace-pane__card-main">
                            <div className="marketplace-pane__package-icon" aria-hidden="true">
                              <PackageIcon />
                            </div>
                            <div className="marketplace-pane__card-copy">
                              <div className="marketplace-pane__card-eyebrow-row">
                                <span className={joinClassNames('marketplace-pane__kind-pill', `marketplace-pane__kind-pill--${card.kind}`)}>
                                  {packageKindLabel(card.kind)}
                                </span>
                                {card.installed ? (
                                  <span className="marketplace-pane__status-badge marketplace-pane__status-badge--installed">
                                    Installed
                                  </span>
                                ) : card.previewOnly ? (
                                  <span className="marketplace-pane__status-badge marketplace-pane__status-badge--preview">
                                    Preview
                                  </span>
                                ) : null}
                              </div>
                              <strong className="marketplace-pane__card-title">{card.name}</strong>
                              <p className="marketplace-pane__card-description">{card.description}</p>
                            </div>
                          </div>
                          <div className="marketplace-pane__card-badges">
                            {marketplaceCardBadges(card, cardDetails).map((token) => (
                              <span key={token} className="marketplace-pane__catalog-badge">
                                {token}
                              </span>
                            ))}
                          </div>
                          <div className="marketplace-pane__card-actions">
                            <button
                              type="button"
                              className={joinClassNames(
                                'marketplace-pane__link-button marketplace-pane__link-button--catalog',
                                card.installed && 'marketplace-pane__link-button--installed',
                                card.previewOnly && 'marketplace-pane__link-button--preview',
                              )}
                              disabled={card.previewOnly || installing || (!card.installed && (!card.id || !card.installEligible))}
                              onClick={(event) => {
                                event.stopPropagation();
                                if (card.previewOnly) {
                                  return;
                                }
                                if (card.installed && card.openHref) {
                                  router.push(card.openHref);
                                  return;
                                }
                                void handleInstall(card.id);
                              }}
                            >
                              {installing ? 'Installing…' : primaryLabel}
                            </button>
                          </div>
                        </article>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          </div>
          {canPublishPackages && showDeveloperRegistration ? (
            <aside className="marketplace-pane__detail-scroll marketplace-pane__detail-scroll--developer" aria-label="App submission">
              <div className="marketplace-pane__secondary-stack">

                <ListDetailPanel
                  className="marketplace-pane__composer-panel"
                  eyebrow="Developer"
                  title="Submit app"
                  subtitle="Submit a public hosted app for review. Approved community apps become visible in Discover."
                  actions={(
                    <AppButton type="button" tone="secondary" onClick={() => setShowDeveloperRegistration(false)}>
                      Hide
                    </AppButton>
                  )}
                >
                <div className="marketplace-pane__composer-tabs">
                  {COMPOSER_KINDS.map((kind) => (
                    <button
                      key={kind}
                      type="button"
                      className={joinClassNames(
                        'marketplace-pane__composer-tab',
                        composerKind === kind && 'marketplace-pane__composer-tab--active',
                      )}
                      onClick={() => {
                        setComposerKind(kind);
                        setComposerError(null);
                        setComposerStatus(null);
                      }}
                    >
                      {humanizeToken(kind)}
                    </button>
                  ))}
                </div>

                {composerError ? (
                  <div className="marketplace-pane__error">
                    <div className="marketplace-pane__error-copy">
                      <strong>Package could not be registered.</strong>
                      <span>Review the required fields, then try again.</span>
                    </div>
                  </div>
                ) : null}
                {composerStatus ? <div className="marketplace-pane__notice">{composerStatus}</div> : null}

                {composerKind === 'app' ? (
                  <div className="marketplace-pane__form">
                    <div className="marketplace-pane__field-grid">
                    <MarketplaceField label="App name">
                        <input
                          className="marketplace-pane__input"
                          value={appDraft.label}
                          onChange={(event) => setAppDraft((current) => ({ ...current, label: event.target.value }))}
                        />
                      </MarketplaceField>
                      <MarketplaceField label="Category">
                        <input
                          className="marketplace-pane__input"
                          value={appDraft.category}
                          onChange={(event) => setAppDraft((current) => ({ ...current, category: event.target.value }))}
                        />
                      </MarketplaceField>
                    </div>
                    <MarketplaceField label="Short description">
                      <textarea
                        className="marketplace-pane__textarea"
                        value={appDraft.description}
                        onChange={(event) => setAppDraft((current) => ({ ...current, description: event.target.value }))}
                      />
                    </MarketplaceField>
                    <div className="marketplace-pane__field-grid">
                      <MarketplaceField label="Publisher name">
                        <input
                          className="marketplace-pane__input"
                          value={appDraft.publisherLabel}
                          onChange={(event) => setAppDraft((current) => ({ ...current, publisherLabel: event.target.value }))}
                        />
                      </MarketplaceField>
                      <MarketplaceField label="Publisher website">
                        <input
                          className="marketplace-pane__input"
                          value={appDraft.publisherWebsite}
                          onChange={(event) => setAppDraft((current) => ({ ...current, publisherWebsite: event.target.value }))}
                        />
                      </MarketplaceField>
                    </div>
                    <div className="marketplace-pane__field-grid">
                      <MarketplaceField label="Icon URL" hint="Required. Must be HTTPS.">
                        <input
                          className="marketplace-pane__input"
                          value={appDraft.iconUrl}
                          onChange={(event) => setAppDraft((current) => ({ ...current, iconUrl: event.target.value }))}
                        />
                      </MarketplaceField>
                    </div>
                    <div className="marketplace-pane__field-grid">
                      <MarketplaceField label="Docs URL">
                        <input
                          className="marketplace-pane__input"
                          value={appDraft.docsUrl}
                          onChange={(event) => setAppDraft((current) => ({ ...current, docsUrl: event.target.value }))}
                        />
                      </MarketplaceField>
                      <MarketplaceField label="Hosted URL" hint="Required. Must be public HTTPS.">
                        <input
                          className="marketplace-pane__input"
                          value={appDraft.hostedUrl}
                          onChange={(event) => setAppDraft((current) => ({ ...current, hostedUrl: event.target.value }))}
                        />
                      </MarketplaceField>
                    </div>
                    <div className="marketplace-pane__field-grid marketplace-pane__field-grid--triple">
                      <MarketplaceField label="Support URL">
                        <input
                          className="marketplace-pane__input"
                          value={appDraft.supportUrl}
                          onChange={(event) => setAppDraft((current) => ({ ...current, supportUrl: event.target.value }))}
                        />
                      </MarketplaceField>
                      <MarketplaceField label="Privacy URL">
                        <input
                          className="marketplace-pane__input"
                          value={appDraft.privacyUrl}
                          onChange={(event) => setAppDraft((current) => ({ ...current, privacyUrl: event.target.value }))}
                        />
                      </MarketplaceField>
                      <MarketplaceField label="Terms URL">
                        <input
                          className="marketplace-pane__input"
                          value={appDraft.termsUrl}
                          onChange={(event) => setAppDraft((current) => ({ ...current, termsUrl: event.target.value }))}
                        />
                      </MarketplaceField>
                    </div>
                    <div className="marketplace-pane__field-grid">
                      <MarketplaceField label="App ID" hint="Optional stable slug. Generated from the name if blank.">
                        <input
                          className="marketplace-pane__input"
                          value={appDraft.appId}
                          onChange={(event) => setAppDraft((current) => ({ ...current, appId: event.target.value }))}
                        />
                      </MarketplaceField>
                      <MarketplaceField label="Version">
                        <input
                          className="marketplace-pane__input"
                          value={appDraft.version}
                          onChange={(event) => setAppDraft((current) => ({ ...current, version: event.target.value }))}
                        />
                      </MarketplaceField>
                    </div>
                    <MarketplaceField label="Permissions" hint="Comma separated. Example: app_bridge.read, app_bridge.write">
                      <input
                        className="marketplace-pane__input"
                        value={appDraft.permissions}
                        onChange={(event) => setAppDraft((current) => ({ ...current, permissions: event.target.value }))}
                      />
                    </MarketplaceField>
                    <MarketplaceField label="Allowed origins" hint="Comma separated. Leave blank to infer from the hosted URL origin.">
                      <input
                        className="marketplace-pane__input"
                        value={appDraft.allowedOrigins}
                        onChange={(event) => setAppDraft((current) => ({ ...current, allowedOrigins: event.target.value }))}
                      />
                    </MarketplaceField>
                    <MarketplaceField label="Bridge contracts" hint="One per line: app_to_sage: summary_request, search">
                      <textarea
                        className="marketplace-pane__textarea"
                        value={appDraft.bridgeContracts}
                        onChange={(event) => setAppDraft((current) => ({ ...current, bridgeContracts: event.target.value }))}
                      />
                    </MarketplaceField>
                  </div>
                ) : (
                  <div className="marketplace-pane__form">
                    <div className="marketplace-pane__field-grid">
                      <MarketplaceField label="Label">
                        <input
                          className="marketplace-pane__input"
                          value={providerDraft.label}
                          onChange={(event) => setProviderDraft((current) => ({ ...current, label: event.target.value }))}
                        />
                      </MarketplaceField>
                      <MarketplaceField label="Category">
                        <input
                          className="marketplace-pane__input"
                          value={providerDraft.category}
                          onChange={(event) => setProviderDraft((current) => ({ ...current, category: event.target.value }))}
                        />
                      </MarketplaceField>
                    </div>
                    <MarketplaceField label="Description">
                      <textarea
                        className="marketplace-pane__textarea"
                        value={providerDraft.description}
                        onChange={(event) => setProviderDraft((current) => ({ ...current, description: event.target.value }))}
                      />
                    </MarketplaceField>
                    <div className="marketplace-pane__field-grid">
                      <MarketplaceField label="Publisher label">
                        <input
                          className="marketplace-pane__input"
                          value={providerDraft.publisherLabel}
                          onChange={(event) => setProviderDraft((current) => ({ ...current, publisherLabel: event.target.value }))}
                        />
                      </MarketplaceField>
                      <MarketplaceField label="Publisher website">
                        <input
                          className="marketplace-pane__input"
                          value={providerDraft.publisherWebsite}
                          onChange={(event) => setProviderDraft((current) => ({ ...current, publisherWebsite: event.target.value }))}
                        />
                      </MarketplaceField>
                    </div>
                    <div className="marketplace-pane__field-grid">
                      <MarketplaceField label="Docs URL">
                        <input
                          className="marketplace-pane__input"
                          value={providerDraft.docsUrl}
                          onChange={(event) => setProviderDraft((current) => ({ ...current, docsUrl: event.target.value }))}
                        />
                      </MarketplaceField>
                      <MarketplaceField label="Provider ID">
                        <input
                          className="marketplace-pane__input"
                          value={providerDraft.providerId}
                          onChange={(event) => setProviderDraft((current) => ({ ...current, providerId: event.target.value }))}
                        />
                      </MarketplaceField>
                    </div>
                    <div className="marketplace-pane__field-grid">
                      <MarketplaceField label="Default model">
                        <input
                          className="marketplace-pane__input"
                          value={providerDraft.defaultModel}
                          onChange={(event) => setProviderDraft((current) => ({ ...current, defaultModel: event.target.value }))}
                        />
                      </MarketplaceField>
                      <MarketplaceField label="Auth modes" hint="Comma separated. Example: api_key, oauth">
                        <input
                          className="marketplace-pane__input"
                          value={providerDraft.authModes}
                          onChange={(event) => setProviderDraft((current) => ({ ...current, authModes: event.target.value }))}
                        />
                      </MarketplaceField>
                    </div>
                    <div className="marketplace-pane__field-grid marketplace-pane__field-grid--triple">
                      <MarketplaceField label="Verification">
                        <select
                          className="marketplace-pane__input"
                          value={providerDraft.verificationStatus}
                          onChange={(event) => setProviderDraft((current) => ({ ...current, verificationStatus: event.target.value }))}
                        >
                          {VERIFICATION_OPTIONS.map((option) => <option key={option} value={option}>{humanizeToken(option)}</option>)}
                        </select>
                      </MarketplaceField>
                      <MarketplaceField label="Review">
                        <select
                          className="marketplace-pane__input"
                          value={providerDraft.reviewState}
                          onChange={(event) => setProviderDraft((current) => ({ ...current, reviewState: event.target.value }))}
                        >
                          {REVIEW_OPTIONS.map((option) => <option key={option} value={option}>{humanizeToken(option)}</option>)}
                        </select>
                      </MarketplaceField>
                      <MarketplaceField label="Health">
                        <select
                          className="marketplace-pane__input"
                          value={providerDraft.healthState}
                          onChange={(event) => setProviderDraft((current) => ({ ...current, healthState: event.target.value }))}
                        >
                          {HEALTH_OPTIONS.map((option) => <option key={option} value={option}>{humanizeToken(option)}</option>)}
                        </select>
                      </MarketplaceField>
                    </div>
                    <div className="marketplace-pane__field-grid marketplace-pane__field-grid--triple">
                      <MarketplaceField label="Policy posture">
                        <select
                          className="marketplace-pane__input"
                          value={providerDraft.policyPosture}
                          onChange={(event) => setProviderDraft((current) => ({ ...current, policyPosture: event.target.value }))}
                        >
                          {POLICY_OPTIONS.map((option) => <option key={option} value={option}>{humanizeToken(option)}</option>)}
                        </select>
                      </MarketplaceField>
                      <MarketplaceField label="Billing" hint="Metadata only in the pilot; this does not start payment processing.">
                        <select
                          className="marketplace-pane__input"
                          value={providerDraft.monetizationKind}
                          onChange={(event) => setProviderDraft((current) => ({ ...current, monetizationKind: event.target.value }))}
                        >
                          {MONETIZATION_OPTIONS.map((option) => <option key={option} value={option}>{humanizeToken(option)}</option>)}
                        </select>
                      </MarketplaceField>
                      <MarketplaceField label="Approval gate">
                        <select
                          className="marketplace-pane__input"
                          value={providerDraft.approvalRequired ? 'required' : 'not_required'}
                          onChange={(event) => setProviderDraft((current) => ({ ...current, approvalRequired: event.target.value === 'required' }))}
                        >
                          <option value="not_required">Not required</option>
                          <option value="required">Required</option>
                        </select>
                      </MarketplaceField>
                    </div>
                    <div className="marketplace-pane__field-grid">
                      <MarketplaceField label="Billing product ID" hint="Reference only; payment/subscription processing is not live from this field.">
                        <input
                          className="marketplace-pane__input"
                          value={providerDraft.billingProductId}
                          onChange={(event) => setProviderDraft((current) => ({ ...current, billingProductId: event.target.value }))}
                        />
                      </MarketplaceField>
                      <MarketplaceField label="Revenue share (bps)">
                        <input
                          className="marketplace-pane__input"
                          value={providerDraft.revenueShareBps}
                          onChange={(event) => setProviderDraft((current) => ({ ...current, revenueShareBps: event.target.value }))}
                        />
                      </MarketplaceField>
                    </div>
                    <div className="marketplace-pane__field-grid">
                      <MarketplaceField label="Accounting ledger key">
                        <input
                          className="marketplace-pane__input"
                          value={providerDraft.ledgerKey}
                          onChange={(event) => setProviderDraft((current) => ({ ...current, ledgerKey: event.target.value }))}
                        />
                      </MarketplaceField>
                      <MarketplaceField label="Accounting hook kind">
                        <input
                          className="marketplace-pane__input"
                          value={providerDraft.hookKind}
                          onChange={(event) => setProviderDraft((current) => ({ ...current, hookKind: event.target.value }))}
                        />
                      </MarketplaceField>
                    </div>
                    <MarketplaceField label="Privacy posture">
                      <textarea
                        className="marketplace-pane__textarea"
                        value={providerDraft.privacyPosture}
                        onChange={(event) => setProviderDraft((current) => ({ ...current, privacyPosture: event.target.value }))}
                      />
                    </MarketplaceField>
                    <div className="marketplace-pane__field-grid">
                      <MarketplaceField label="Jurisdiction">
                        <input
                          className="marketplace-pane__input"
                          value={providerDraft.jurisdiction}
                          onChange={(event) => setProviderDraft((current) => ({ ...current, jurisdiction: event.target.value }))}
                        />
                      </MarketplaceField>
                      <MarketplaceField label="Residency">
                        <input
                          className="marketplace-pane__input"
                          value={providerDraft.residency}
                          onChange={(event) => setProviderDraft((current) => ({ ...current, residency: event.target.value }))}
                        />
                      </MarketplaceField>
                    </div>
                    <MarketplaceField label="Capability labels" hint="Comma separated. Example: reasoning, hosted api">
                      <input
                        className="marketplace-pane__input"
                        value={providerDraft.capabilityLabels}
                        onChange={(event) => setProviderDraft((current) => ({ ...current, capabilityLabels: event.target.value }))}
                      />
                    </MarketplaceField>
                    <MarketplaceField label="Model roster" hint="One per line: model_id | Label. If blank, the default model becomes the only model entry.">
                      <textarea
                        className="marketplace-pane__textarea"
                        value={providerDraft.models}
                        onChange={(event) => setProviderDraft((current) => ({ ...current, models: event.target.value }))}
                      />
                    </MarketplaceField>
                  </div>
                )}

                <div className="marketplace-pane__form-actions">
                  <AppButton
                    type="button"
                    tone="primary"
                    onClick={() => {
                      void handleComposerSubmit();
                    }}
                    disabled={submittingComposer}
                  >
                    {submittingComposer
                      ? 'Submitting…'
                      : composerKind === 'app'
                        ? 'Submit app'
                        : 'Register provider package'}
                  </AppButton>
                </div>
              </ListDetailPanel>
              <ListDetailPanel
                className="marketplace-pane__composer-panel"
                eyebrow="Review queue"
                title="Pending apps"
                subtitle="Workspace admins approve community apps before they appear in Discover."
              >
                {submissionItems.length ? (
                  <div className="marketplace-pane__detail-checklist">
                    {submissionItems.map((item) => {
                      const packageId = readString(item.package_id);
                      const packagePayload = readRecord(item.package);
                      const hostedUrl = readString(packagePayload.hosted_url);
                      const busy = reviewingPackageId === packageId;
                      return (
                        <div key={packageId} className="marketplace-pane__detail-checklist-item">
                          <Check aria-hidden="true" />
                          <span>
                            {readString(item.label, 'Untitled app')}
                            {hostedUrl ? ` · ${hostedUrl}` : ''}
                          </span>
                          <button
                            type="button"
                            className="marketplace-pane__bookmark-link"
                            disabled={busy}
                            onClick={() => { void handleReviewSubmission(packageId, true); }}
                          >
                            Approve
                          </button>
                          <button
                            type="button"
                            className="marketplace-pane__bookmark-link"
                            disabled={busy}
                            onClick={() => { void handleReviewSubmission(packageId, false); }}
                          >
                            Reject
                          </button>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <p className="marketplace-pane__panel-copy">No pending app submissions.</p>
                )}
              </ListDetailPanel>
              </div>
            </aside>
          ) : null}
        </div>
        <AppModal
          open={Boolean(selectedPackage && selectedDetails)}
          title={selectedPackage?.name ?? 'Discover item'}
          description="Review what it does, setup needed, access, and cost."
          className="marketplace-pane__detail-modal"
          onOpenChange={(open) => {
            if (!open) {
              setSelectedPackageId(null);
            }
          }}
        >
          {selectedPackage && selectedDetails ? (
            <div className="marketplace-pane__detail-panel marketplace-pane__detail-panel--modal">
              <MarketplacePackageDetail
                card={selectedPackage}
                details={selectedDetails}
                installingPackageId={installingPackageId}
                onInstall={(packageId) => { void handleInstall(packageId); }}
                onOpenHref={(href) => router.push(href)}
              />
            </div>
          ) : null}
        </AppModal>
      </section>
    </WorkstationSurfaceRoot>
  );
}
