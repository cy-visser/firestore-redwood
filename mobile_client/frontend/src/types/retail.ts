export interface CatalogItem {
  sku: string;
  name: string;
  category: string;
  unitPrice: number;
  cost: number;
  description?: string;
  inStock?: boolean;
}

export interface CartItem {
  sku: string;
  name: string;
  category: string;
  unitPrice: number;
  quantity: number;
  allocatedWarehouse?: string;
}

export interface ShippingCity {
  city: string;
  countryCode: string;
  postalCode: string;
  province: string;
}

export interface PrincipalProfile {
  iamPrincipal: string;
  /** The seeded customer this principal acts as, e.g. cust_demo1. */
  customerId: string;
  displayName: string;
  customerSegment: string;
  loyaltyTier: string;
  isLoyaltyMember: number;
  discountRate: number;
  accountAgeDays: number;
  defaultAddress: {
    streetAddress: string;
    city: string;
    province: string;
    postalCode: string;
    countryCode: string;
  };
  defaultCarrier: string;
  defaultWarehouse: string;
  historicalMetrics: {
    totalSpend90d: number;
    lifetimeSpend: number;
    avgOrderValue: number;
    purchaseFrequencyMonthly: number;
    daysSinceLastPurchase: number;
    ordersCountLast12m: number;
  };
  engagementMetrics: {
    loginFrequencyMonthly: number;
    avgSessionDurationMinutes: number;
    appEngagementScore: number;
    appSessionsLast30d: number;
    cartAbandonmentCount: number;
    abandonedCartValue90d: number;
  };
  supportMetrics: {
    supportTicketsCount: number;
    openSupportTicketsCount: number;
    complaintsCount: number;
    returnFrequency: number;
    returnRatePercent: number;
  };
}

export interface LineItem {
  sku: string;
  name: string;
  category: string;
  quantity: number;
  unitPrice: number;
  totalPrice: number;
  allocatedWarehouse: string;
}

export interface Financials {
  subtotal: number;
  taxAmount: number;
  shippingFee: number;
  discountTotal: number;
  grandTotal: number;
  profitMargin: number;
}

export interface TransactionalMetrics {
  totalSpend90d: number;
  lifetimeSpend: number;
  avgOrderValue: number;
  purchaseFrequencyMonthly: number;
  daysSinceLastPurchase: number;
  ordersCountLast12m: number;
}

export interface EngagementMetrics {
  loginFrequencyMonthly: number;
  avgSessionDurationMinutes: number;
  appEngagementScore: number;
  appSessionsLast30d: number;
  cartAbandonmentCount: number;
  abandonedCartValue90d: number;
}

export interface SupportMetrics {
  supportTicketsCount: number;
  openSupportTicketsCount: number;
  complaintsCount: number;
  returnFrequency: number;
  returnRatePercent: number;
  sentimentScore: number;
  hasActiveComplaint: boolean;
  primaryComplaintReason?: string | null;
}

export interface AccountState {
  loyaltyTier: string;
  isLoyaltyMember: number;
  accountAgeDays: number;
  customerSegment: string;
}

export interface Logistics {
  carrierCode: string;
  serviceLevel: string;
  originHub: string;
  totalWeightKg: number;
  requireSignature: boolean;
}

export interface ShippingAddress {
  streetAddress: string;
  city: string;
  province: string;
  postalCode: string;
  countryCode: string;
}

export interface CustomerFeedback {
  feedbackText: string;
  rating: number;
  sentimentScore: number;
  channel: string;
  hasActiveComplaint: boolean;
  primaryComplaintReason?: string | null;
  feedbackTimestamp: string;
}

export interface OrderMetadata {
  apiVersion: string;
  sourcePlatform: string;
  clientIpAddress: string;
  retryCount: number;
}

/**
 * Why the order cost what it cost.
 *
 * `financials.discountTotal` is the amount; this says which agent offer
 * produced it, or records that there was none. The only field on the order
 * that the dataset seeder does not also write, because the seeder has no
 * retention agent.
 */
export interface OrderLoyaltyOffer {
  offerApplied: boolean;
  offerId: string | null;
  promoCode: string | null;
  discountPercent: number;
  freeExpressShipping: boolean;
  /** Only on a submit response: whether the offer was successfully marked spent. */
  claimed?: boolean;
}

export interface OrderDocument {
  _id: string;
  orderId: string;
  customerId: string;
  customerName: string;
  customerEmail: string;
  customerSegment: string;
  orderStatus: string;
  paymentStatus: string;
  paymentMethod: string;
  currency: string;
  financials: Financials;
  loyaltyOffer?: OrderLoyaltyOffer;
  transactionalMetrics: TransactionalMetrics;
  engagement: EngagementMetrics;
  supportMetrics: SupportMetrics;
  accountState: AccountState;
  logistics: Logistics;
  shippingAddress: ShippingAddress;
  lineItems: LineItem[];
  customerFeedback: CustomerFeedback;
  metadata: OrderMetadata;
  createdAt: string;
  updatedAt: string;
  estimatedDeliveryDate: string;
}

// ---------------------------------------------------------------------------
// Retention loop
// ---------------------------------------------------------------------------

/**
 * Statuses the agent moves a session through. PENDING and PROCESSING are
 * transient; the rest are terminal, and the mobile app stops waiting on any
 * of them.
 */
export type AgentProcessingStatus =
  | "PENDING"
  | "PROCESSING"
  | "PROCESSED"
  | "SKIPPED"
  | "ERROR";

export const TERMINAL_STATUSES: AgentProcessingStatus[] = [
  "PROCESSED",
  "SKIPPED",
  "ERROR",
];

export interface CustomerSession {
  sessionId: string;
  customerId: string;
  principalId?: string;
  customerName?: string;
  iamPrincipal?: string;
  agentProcessingStatus: AgentProcessingStatus;
  status?: string;
  skipReason?: string | null;
  /**
   * One sentence explaining the skip: the escalation judge's own words where
   * a judgement was made, the gate's where none was. Absent when the customer
   * simply has not complained and there is nothing to explain.
   */
  skipDetail?: string | null;
  /** Which escalation branch ran. See policy.GATE_* on the agent. */
  escalationGate?: string | null;
  offerId?: string | null;
  activeOfferId?: string | null;
  agentWorkerId?: string | null;
  channel?: string;
  loginTimestamp?: string;
  loginAt?: string;
  createdAt?: string;
  processingStartedAt?: string | null;
  processedAt?: string | null;
  errorMessage?: string | null;
}

export interface LoyaltyOffer {
  offerId: string;
  customerId: string;
  sessionId: string;
  title: string;
  description: string;
  promoCode: string;
  discountPercent: number;
  freeExpressShipping: boolean;
  perks: string[];
  personalizedApology?: string | null;
  /** What BigQuery scored. Never adjusted by the agent. */
  churnProbability: number;
  /** The tier BigQuery assigned to that probability. */
  churnRiskTier: string;
  /**
   * The tier the agent acted on. Equal to churnRiskTier unless the LLM judge
   * escalated an unseen complaint, which is the only way the two can differ.
   */
  eligibilityTier?: string;
  escalated?: boolean;
  /** The judge's own words. This is the line worth reading out on stage. */
  escalationReason?: string | null;
  /** The evidence behind the judgement: order, rating, reason, timestamps. */
  escalationTrigger?: {
    orderId?: string | null;
    rating?: number | null;
    reason?: string | null;
    submittedAt?: string | null;
    scoredAt?: string | null;
  } | null;

  /** 1 is the first offer in a cooldown window, 2 the follow-up after it was redeemed. */
  offerSequence?: number;
  supersedesOfferId?: string | null;
  discountCeilingApplied?: number | null;
  status: "ACTIVE" | "REDEEMED" | "EXPIRED";
  createdAt: string;
  validUntil?: string;
  cooldownUntil?: string;
  claimedAt?: string | null;
  orderId?: string | null;

}

export interface ChurnScore {
  customerId: string;
  churnProbability: number | null;
  churnRiskTier: string | null;
  customerSegment: string | null;
  totalSpend90d: number | null;
  sentimentScore: number | null;
  calculatedAt: string | null;
}

/**
 * Timings measured end to end in the browser, against one clock.
 *
 * Deliberately separate from anything derived from Firestore commit
 * timestamps. Four clocks are involved in this system, and a number produced
 * by subtracting one from another looks precise and means nothing.
 */
export interface ClientTelemetry {
  sessionId: string;
  /** Sign in tapped until the login POST returned. */
  clientAckMs?: number;
  /** Sign in tapped until the outcome rendered. */
  offerVisibleMs?: number;
  outcome?: string;
  recordedAt?: string;
}

/**
 * Per-step spans recorded to pipeline_traces/{sessionId}.
 *
 * Each duration is measured on a single clock by the component that owns the
 * step, with Eventarc delivery marked as approximate (ce-time vs bridge receive).
 *
 * Written by three parties into one document: the bridge on receipt, the agent
 * as it runs, and the bridge again when the agent returns. A trace therefore
 * arrives in pieces and fields appear progressively.
 */
export interface PipelineTrace {
  kind?: "SESSION";
  sessionId: string;
  customerId?: string;
  eventId?: string;
  eventTime?: string;
  bridgeReceivedAt?: string;
  bridgeRecordedAt?: string;
  eventarcDeliveryMs?: number;
  bridgeOverheadMs?: number;
  agentCallMs?: number;
  agentClaimMs?: number;
  churnLookupMs?: number;
  /**
   * The escalation judge alone. Present when the model scored the customer in
   * a candidate tier and their latest order carries a complaint, whatever its
   * age -- the judge is the thing that decides whether the score already
   * accounts for it. Absent is still the normal case.
   */
  llmEscalationMs?: number;
  escalationOutcome?: "OK" | "FAILED";
  escalationVerdict?: "ESCALATE" | "DECLINE";
  /**
   * Which escalation branch ran, including the ones that never reach the
   * judge. Always present once the agent has evaluated the tier gate, so the
   * pipeline can label the node on runs that produce no judge span.
   */
  escalationGate?: string;
  /** The Gemini call alone: request issued until the final response object. */
  llmReasoningMs?: number;
  llmModel?: string;
  llmOutcome?: "OK" | "FAILED";
  offerGenerationMs?: number;
  offerWriteMs?: number;
  agentOutcome?: string;
  /** One line of context for agentOutcome, e.g. why a lookup was skipped. */
  agentDetail?: string | null;

  /**
   * DISPATCHED is written before the agent is called, so the console can light
   * up the delivery hop without waiting out a cold start.
   */
  bridgeStatus?: "DISPATCHED" | "COMPLETE" | "AGENT_ERROR";
  bridgeAction?: string;
  bridgeError?: string;
  recordedAt?: string;
  expireAt?: string;
}

/**
 * Order replication spans recorded to pipeline_traces/order_{orderId}.
 *
 * Written once by cdc_service when an order reaches BigQuery. Both spans are
 * measured on that service's own clock: the Eventarc delivery hop, and the
 * Storage Write API append.
 */
export interface OrderTrace {
  kind: "ORDER";
  orderId: string;
  customerId?: string;
  eventId?: string;
  eventTime?: string;
  cdcReceivedAt?: string;
  /** Approximate: ce-time against the CDC service's arrival time. */
  cdcDeliveryMs?: number;
  bqWriteMs?: number;
  bqTotalMs?: number;
  bqTables?: string[];
  operation?: string;
  recordedAt?: string;
}

/** Single-clock write duration measured in the FastAPI process. */
export interface WriteLatency {
  kind: "login" | "order";
  documentId: string;
  durationMs: number;
  at: string;
}

/** One document delivered by the SSE bridge, with its commit timestamps. */
export interface SnapshotEvent<T> {
  id: string;
  data: T;
  createTime: string | null;
  updateTime: string | null;
}

export interface ConsoleLogEntry {
  id: number;
  at: string;
  kind:
    | "session"
    | "offer"
    | "telemetry"
    | "control"
    | "stream"
    | "log"
    | "order"
    | "write";
  message: string;
}

