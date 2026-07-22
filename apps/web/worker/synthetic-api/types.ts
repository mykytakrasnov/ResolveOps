export interface PublicCase {
  case_id: string;
  split: string;
  category: string;
  difficulty: string;
  curated: boolean;
  subject: string;
  body: string;
  customer_reference: string;
  created_at: string;
  attachments: readonly Record<string, string>[];
}

export interface CrmAccount {
  account_id: string;
  customer_reference: string;
  name: string;
  primary_email: string;
  region: string;
  status: string;
  created_at: string;
}

export interface Subscription {
  subscription_id: string;
  account_id: string;
  plan: string;
  status: string;
  amount_cents: number;
  currency: string;
  current_period_start: string;
  current_period_end: string;
  plan_limit_units: number;
  usage_units: number;
  previous_plan: string | null;
  upgraded_at: string | null;
  canceled_at: string | null;
}

export interface Invoice {
  invoice_id: string;
  account_id: string;
  subscription_id: string;
  period_start: string;
  period_end: string;
  amount_cents: number;
  currency: string;
  status: string;
  issued_at: string;
}

export interface PaymentAttempt {
  payment_attempt_id: string;
  account_id: string;
  invoice_id: string;
  amount_cents: number;
  currency: string;
  status: string;
  processor_reference: string;
  attempted_at: string;
}

export interface PolicyDocument {
  policy_id: string;
  policy_key: string;
  version: string;
  action_type: string;
  maximum_amount_cents: number | null;
  approval_required: boolean;
  effective_at: string;
  body: string;
}

export interface SyntheticStatus {
  status: "available";
  dataset_version: string;
  generated_at: string;
  entity_counts: Readonly<Record<string, number>>;
}

export interface Page<T> {
  items: readonly T[];
  page: {
    limit: number;
    next_cursor: string | null;
  };
}
