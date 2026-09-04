# Redwood Retail: Frontend Integration Guide
## Autonomous Loyalty Offer & Retention Agent

**Target Audience:** Frontend Engineering Team (Web, iOS, Android, Flutter)  
**Backend Services:** Google Cloud Firestore Enterprise Native, Google ADK Retention Agent  
**Service Level Agreement (SLA):** Voucher delivery in < 800 ms from login authentication  

---

## 1. Architectural Overview

When a customer logs into the Redwood Retail portal or mobile application, the Autonomous Loyalty Agent evaluates their real-time churn risk and past operational grievances (e.g., shipping delays, defective components). If the customer is at elevated risk ($\ge 50\%$), a personalized retention voucher is synthesized and pushed to their device in under 800 milliseconds.

We provide integration via the **Firestore Native Real-Time SDK**: Sub-500ms reactive delivery via gRPC watch streams directly between client devices and Cloud Firestore.

---

## 2. Integration: Firestore Native Real-Time SDK

### Step 1: Write Session on Customer Login
When the customer successfully authenticates, write a new session document to the `/customer_sessions` collection:

```typescript
import { getFirestore, doc, setDoc } from "firebase/firestore";

const db = getFirestore();

async function onCustomerLogin(customerId: string) {
  const sessionId = `sess_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
  
  await setDoc(doc(db, "customer_sessions", sessionId), {
    sessionId: sessionId,
    customerId: customerId,
    deviceInfo: {
      deviceType: "MOBILE",
      platform: "IOS", // or 'ANDROID', 'WEB'
      appVersion: "4.12.0"
    },
    loginTimestamp: new Date().toISOString(),
    status: "ACTIVE",
    agentProcessingStatus: "PENDING" // Triggers backend agent
  });

  return sessionId;
}
```

### Step 2: Attach Real-Time Listener for Loyalty Offers
Immediately upon login, subscribe to active offers for this customer:

```typescript
import { collection, query, where, orderBy, limit, onSnapshot } from "firebase/firestore";

function subscribeToActiveRetentionOffer(customerId: string, onOfferReceived: (offer: any) => void) {
  const offersRef = collection(db, "loyalty_offers");
  const q = query(
    offersRef,
    where("customerId", "==", customerId),
    where("status", "==", "ACTIVE"),
    orderBy("createdAt", "desc"),
    limit(1)
  );

  const unsubscribe = onSnapshot(q, (snapshot) => {
    snapshot.docChanges().forEach((change) => {
      if (change.type === "added" || change.type === "modified") {
        const offer = change.doc.data();
        console.log("⚡ Active loyalty offer received in real time:", offer);
        onOfferReceived(offer);
      }
    });
  });

  return unsubscribe;
}
```

### Step 3: Redeem Offer during Checkout
When the customer applies the voucher at checkout:

```typescript
import { doc, updateDoc } from "firebase/firestore";

async function redeemLoyaltyOffer(offerId: string, orderId: string) {
  const offerRef = doc(db, "loyalty_offers", offerId);
  await updateDoc(offerRef, {
    status: "REDEEMED",
    claimedAt: new Date().toISOString(),
    orderId: orderId
  });
}
```

---

## 3. UI Display Guidelines

When an offer is received, the frontend should render a non-intrusive, high-converting banner or modal:
* **Headline:** Display `offer.title` prominently.
* **Apology Callout:** If `offer.personalizedApology` is present, display it as a gentle customer care banner.
* **Perks Badges:** If `offer.freeExpressShipping` is true, show a "🚀 Free Express Shipping" badge.
* **1-Click Apply Button:** Directly auto-apply `offer.promoCode` to the customer's active cart.

---

## 4. Security & Access Rules

Security rules are enforced natively in Firestore:
* **Authentication:** All requests must include a valid Firebase Auth token.
* **Isolation:** Customers can only read and query their own sessions and loyalty offers (`customerId == auth.uid`).
* **Immutability:** Frontend clients cannot forge discounts, alter `agentProcessingStatus`, or delete offer documents.
