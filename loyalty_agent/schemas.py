"""Pydantic data schemas for Redwood Retail Loyalty Offer Agent."""

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
    # Why, in words, and which branch produced them. skipDetail is the judge's
    # own sentence where a judgement was made, and the gate's where none was;
    # escalationGate names the branch, including the one that writes no
    # sentence because the customer never complained.
    skipDetail: Optional[str] = None
    escalationGate: Optional[str] = None
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


class LoyaltyOffer(BaseModel):
    offerId: str
    customerId: str
    sessionId: str

    # What BigQuery said. Never adjusted by the agent: an earlier version
    # added 0.25 for a complaint and saturated this to 1.0, which is a
    # certainty no model asserted.
    churnProbability: float
    churnRiskTier: str

    # What the agent acted on. Equal to churnRiskTier unless friction the
    # model had not seen was escalated, in which case the pair shows exactly
    # which part of the decision was the model's and which was the agent's.
    eligibilityTier: Optional[str] = None
    escalated: bool = False
    escalationReason: Optional[str] = None
    escalationTrigger: Optional[Dict[str, Any]] = None

    title: str
    description: str
    promoCode: str
    discountPercent: int
    freeExpressShipping: bool = False
    perks: List[str] = Field(default_factory=list)
    personalizedApology: Optional[str] = None

    # Follow-up bookkeeping. 1 is the first offer a customer receives in a
    # cooldown window; 2 is the one issued after they redeemed it.
    offerSequence: int = 1
    supersedesOfferId: Optional[str] = None
    discountCeilingApplied: Optional[int] = None

    status: str = "ACTIVE"  # ACTIVE, REDEEMED, EXPIRED
    createdAt: str
    validUntil: str
    cooldownUntil: str
    claimedAt: Optional[str] = None
    # Stored as datetime for Firestore TTL policy expiration.
    ttlExpiryAt: datetime
    metadata: Dict[str, Any] = Field(default_factory=dict)

