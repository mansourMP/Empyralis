export type EmpyralisModelTier =
  | 'light'
  | 'pro'
  | 'max'
  | 'local_ai'
  | 'my_api_key'
  | 'my_ai_account';

export type EmpyralisModelBillingSource =
  | 'empyralis_credits'
  | 'local_runtime'
  | 'user_api_key'
  | 'user_ai_account';

export type EmpyralisModelTierContract = {
  public_tier: EmpyralisModelTier;
  public_label: 'Light' | 'Pro' | 'Max' | 'Local AI' | 'My API Key' | 'My AI Account';
  internal_provider: string | null;
  internal_model: string | null;
  reasoning_effort: 'high' | 'max' | null;
  thinking_mode: 'off' | 'high' | 'max' | null;
  max_output_tier: 'standard' | 'expanded' | 'maximum' | 'runtime_defined' | 'provider_defined' | 'account_defined';
  agent_budget_tier: 'standard' | 'expanded' | 'maximum' | 'runtime_defined';
  billing_source: EmpyralisModelBillingSource;
  credit_multiplier: number;
  fallback_tier: EmpyralisModelTier | null;
  user_owned: boolean;
  expose_provider_model_to_ordinary_ui: boolean;
  ordinary_ui_subtitle: string;
};

export const EMPYRALIS_HOSTED_MODEL_TIERS = ['light', 'pro', 'max'] as const;

export const USER_OWNED_MODEL_TIERS = ['local_ai', 'my_api_key', 'my_ai_account'] as const;

export const EMPYRALIS_MODEL_TIERS = [
  ...EMPYRALIS_HOSTED_MODEL_TIERS,
  ...USER_OWNED_MODEL_TIERS,
] as const;

export function exposesProviderModelToOrdinaryUi(tier: EmpyralisModelTier): boolean {
  return USER_OWNED_MODEL_TIERS.includes(tier as (typeof USER_OWNED_MODEL_TIERS)[number]);
}
