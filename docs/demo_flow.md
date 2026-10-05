# Redwood Retail: Deployment and Demo Flow

### Purpose of this demo

This demo shows how retail customers can use standard Google Cloud service to perform churn analysis for their customers and proactively try to retain customers with high churn risk.

Once an user is classified by BigQuery’s churn analysis ML model as high risk, for the next user logon session to the mobile app an Agent is triggered automatically and calculates a loyalty offer. This offer is near real time synchronized to the mobile app of the user.

&nbsp;

### Selling Points

This demo uses only standard out of the box features from managed services in Google Cloud:

&nbsp;

1. The Mobile App is running in Cloud Run and uses the Firestore SDK to realtime synchronize data to and from Firestore. This continues to work even in situations where there is no network connection.  
   &nbsp;  
2. Firestore Enterprise is used to store logon sessions, orders and customers data.  
   &nbsp;  
3. A Cloud Run Function automatically receives near realtime document creates, updates and deletes from Firestore through EventArc and writes theses documents to BigQuery  
   &nbsp;  
4. BigQuery holds orders and customer dataset. The out of the box churn analysis model runs daily across this dataset and calculates the churn risk for each customer.&nbsp;  
   &nbsp;  
5. When a customer logs in the mobile app, a Firestore event is automatically triggered and published through EventArc. A Cloud Run will call the Agent deployed in Agent Runtime in case the churn risk score is high.  
   &nbsp;  
6. The Agent creates a loyalty offer and stores this in the Firestore database. This is automatically synchronized to the mobile using the Firestore SDK.

&nbsp;

The power of Firestore in this use case is its native capability of synchronizing server side events (like the loyalty offer) directly to the client using the on\_snapshot method in the Firestore SDK. This makes it every easy to directly inform a customer with a single line of code.

&nbsp;

&nbsp;

---

## 1\. Deploying

```sh
./deploy.sh
```

One command. Six steps, in this order and for these reasons:

| Step | What it does | Why here |
| :---- | :---- | :---- |
| 1/6 | Builds and pushes the CDC and bridge images | Terraform deploys by digest, so the images must exist first |
| 2/6 | `terraform apply` | Creates everything except the agent |
| 3/6 | Seeds Firestore, then reconciles BigQuery | Reconciliation is unconditional |
| 4/6 | Builds feature views, trains the model, scores every customer | The agent needs scores to read |
| 5/6 | Deploys the agent to Agent Engine | Stages through the bucket Terraform created |
| 6/6 | Prints the summary |  |

### Useful variations

```sh
./deploy.sh --dry-run            # Validate config and show the Terraform plan
./deploy.sh --skip-image-build   # Reuse the images already in Artifact Registry
./deploy.sh --skip-seed          # Leave Firestore as it is
./deploy.sh --skip-bqml          # Do not retrain the churn model
./deploy.sh --skip-agent-deploy  # Leave the deployed agent alone
./deploy.sh --seed-count 1000    # Seed 1,000 customers instead of 400
./deploy.sh --create-project     # Provision a new project first
```

The `--skip-*` flags matter in practice: a full run rebuilds two container images, retrains the model and redeploys the agent, and the agent deployment alone takes two to three minutes.

---

## 2\. The two demo customers

The seeder creates two personas with deliberately opposite histories, so the demo shows the agent deciding rather than always issuing.

|  | `cust_demo1` | `cust_demo2` |
| :---- | :---- | :---- |
| Behaviour | Buying regularly | Lapsed |
| Days since last purchase | 22 | 197 |
| Churn probability | 0.0327 | 0.8856 |
| Tier | LOW | CRITICAL |
| Agent decision | Skip | Issue an offer |
| Discount on an order | None | Whatever the agent's offer says |

&nbsp;

---

## 3\. Demo churn risk and loyalty offer

A suggested order, roughly ten minutes:

1. **Press Reset Demo** in the console. `customer_sessions` and `loyalty_offers` are empty, and the agent is warm.  
2. **Log in as `demo1`.** Nothing is issued. Point at `skipReason: LOW_CHURN_RISK` on the session document: BigQuery called this customer LOW, so the agent declined to spend margin.  
3. **Place an order as `demo1`.** Full price. There is no tier discount in the system at all — the only thing that can discount an order is an offer the agent decided to issue.  
4. **Switch to `demo2` and log in.** An offer appears in `loyalty_offers` within seconds and reaches the phone over SSE (Server-Sent Events), carrying the churn probability and tier that justified it. Note that `churnProbability` is BigQuery's number unchanged, and that `churnRiskTier` and `eligibilityTier` agree — no judgement was needed, because the model already said CRITICAL.  
5. **Place an order as `demo2`.** The same cart is now 15% cheaper with free shipping, and the ledger line names the offer and its promo code. This is the moment the whole chain pays off: BigQuery scored, the agent decided, Firestore delivered, the price changed.  
6. **Order again as `demo2`.** Full price. The offer is attached to the first order and cannot discount a second.  
7. **Rate the order 5 stars, then press Recalculate Churn.** The purchase and the rating move `cust_demo2` from 0.9288 to 0.6242 — better, but still HIGH. The customer is recovering, not recovered.  
8. **Log in as `demo2` once more.** The redeemed offer no longer silences the agent, but it does not repeat itself either: it issues a *follow-up* at a stepped-down 10%, carrying `offerSequence: 2` and `supersedesOfferId` pointing at the offer just redeemed. The discount tracks the risk down.  
9. **Log in once more.** `FOLLOW_UP_LIMIT_REACHED`. One follow-up is the cap, so the agent cannot discount its way into a spiral. Had the first offer still been ACTIVE rather than redeemed, the reason would instead read `ACTIVE_OFFER_ALREADY_EXISTS` — the guardrails hold on both paths.  
10. **Show the order arriving in `retail_cdc`** within seconds, with `customer_id = 'cust_demo2'` — the same identity the churn model and the agent use. The console's BigQuery node has been showing how long that leg took since the moment the order was placed.

The point worth making in step 5 is that no part of the path was polled or scheduled. The login itself caused the offer.

---

## 4\. Demo bad review

An offer carries two tiers side by side: `churnRiskTier` is the model's, and `eligibilityTier` is the one the agent acted on. On a normal run they agree. They can only differ through the escalation path, which is the secondary demo flow worth showing on `cust_demo1`:

1. `cust_demo1` is healthy, so BigQuery scores it below the gate (`LOW`) and the agent would normally skip it.  
2. **Place an order as `demo1`, rate that order 1 star, and pick a complaint reason.** That writes `customerFeedback.feedbackTimestamp` onto the order.  
3. **Log in as `demo1` again.** BigQuery still scores this customer LOW — it was scored overnight and the complaint is minutes old, so the model provably cannot have seen it. The agent notices exactly that: it compares the order's `feedbackTimestamp` against `calculation_timestamp` on the scored row.  
4. **Only because the complaint is newer does the agent ask the ADK `LlmAgent` judge a single question:** does this change the picture? Read `escalationReason` off the offer document — those are the judge's own words — and point at `judge escalate …` on the Agent Engine node, which is the cost of asking.

Declining is the default and the common case (`skipReason: ESCALATION_DECLINED`) — a customer who is fine stays fine, and a rule that always fires is not a judgement. When the judge does escalate, Gemini tends to reference the complaint in the offer copy; `SORRYDELIVERY15` was a real promo code from a live run.

&nbsp;