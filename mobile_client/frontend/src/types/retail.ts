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
