"""
Pydantic data schemas for Redwood Retail Loyalty Offer Agent.
Aligns strictly with SDD Level 3 JSON Data Contracts.
"""

from datetime import datetime
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field


class DeviceInfo(BaseModel):
    deviceType: str = "MOBILE"
    platform: str = "IOS"
    osVersion: Optional[str] = None
    appVersion: Optional[str] = None
    ipAddress: Optional[str] = None


class CustomerSession(BaseModel):
    sessionId: str
    customerId: str
    deviceInfo: Optional[Dict[str, Any]] = None
    loginTimestamp: str
    status: str = "ACTIVE"
    agentProcessingStatus: str = "PENDING"  # PENDING, PROCESSING, PROCESSED, SKIPPED
    processedAt: Optional[str] = None
    agentWorkerId: Optional[str] = None
    offerId: Optional[str] = None
    activeOfferId: Optional[str] = None
    skipReason: Optional[str] = None
    expireAt: Optional[str] = None


class CustomerProfile(BaseModel):
    customerId: str
    customerName: Optional[str] = None
    customerEmail: Optional[str] = None
    customerSegment: str = "STANDARD_LOYALTY"
    accountAgeDays: int = 365
    daysSinceLastPurchase: int = 15
    complaintsCount: int = 0
    primaryComplaintReason: Optional[str] = None
    totalSpend90d: float = 0.0
    sentimentScore: float = 0.5


class ChurnAssessment(BaseModel):
    customerId: str
    churnProbability: float
    churnRiskTier: str  # LOW, MODERATE, HIGH, CRITICAL
    evaluationSource: str  # BQML_PREDICT, HEURISTIC_FALLBACK
    evaluatedAt: str


class LoyaltyOffer(BaseModel):
    offerId: str
    customerId: str
    sessionId: str
    churnProbability: float
    churnScore: Optional[float] = None
    churnRiskTier: str
    churnTier: Optional[str] = None
    title: str
    headline: Optional[str] = None
    description: str
    messageBody: Optional[str] = None
    promoCode: str
    voucherCode: Optional[str] = None
    discountPercent: int
    discountPercentage: Optional[int] = None
    freeExpressShipping: bool = False
    perks: List[str] = Field(default_factory=list)
    personalizedApology: Optional[str] = None
    generationSource: str = "GEMINI_AI"  # GEMINI_AI, DETERMINISTIC_RULES
    baselineChurnRisk: Optional[float] = None
    evaluationSource: Optional[str] = None
    status: str = "ACTIVE"  # ACTIVE, REDEEMED, EXPIRED
    createdAt: str
    validUntil: str
    expiresAt: Optional[str] = None
    cooldownUntil: str
    claimedAt: Optional[str] = None
    ttlExpiryAt: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
