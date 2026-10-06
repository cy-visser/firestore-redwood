# Redwood Retail: Deployment and Demo Flow

### Purpose of this demo

This demo shows how retail customers can use standard Google Cloud service to perform churn analysis for their customers and proactively try to retain customers with high churn risk.

Once an user is classified by BigQuery’s churn analysis ML model as high risk, for the next user logon session to the mobile app an Agent is triggered automatically and calculates a loyalty offer. This offer is near real time synchronized to the mobile app of the user.

### Selling Points

This demo uses only standard out of the box features from managed services in Google Cloud:

1. The Mobile App is running in Cloud Run and uses the Firestore SDK to realtime synchronize data to and from Firestore. This continues to work even in situations where there is no network connection.
2. Firestore Enterprise is used to store logon sessions, orders and customers data.
3. A Cloud Run Function automatically receives near realtime document creates, updates and deletes from Firestore through EventArc and writes theses documents to BigQuery
4. BigQuery holds orders and customer dataset. The out of the box churn analysis model runs daily across this dataset and calculates the churn risk for each customer.
5. When a customer logs in the mobile app, a Firestore event is automatically triggered and published through EventArc. A Cloud Run will call the Agent deployed in Agent Runtime in case the churn risk score is high.
6. The Agent creates a loyalty offer and stores this in the Firestore database. This is automatically synchronized to the mobile using the Firestore SDK.

The power of Firestore in this use case is its native capability of synchronizing server side events (like the loyalty offer) directly to the client using the on\_snapshot method in the Firestore SDK. This makes it every easy to directly inform a customer with a single line of code.

---

## 1\. Deploying

1. Install [`gcloud`](https://cloud.google.com/sdk/docs/install), [`terraform`](https://developer.hashicorp.com/terraform/install) (1.5 or later) and [`python3`](https://www.python.org/downloads/).

2. Clone the repository:

   ```sh
   git clone git@github.com:cy-visser/firestore-redwood.git
   cd firestore-redwood
   ```

3. Create your own GCP project and link a billing account:

   ```sh
   gcloud projects create <PROJECT_ID>
   gcloud billing accounts list
   gcloud billing projects link <PROJECT_ID> --billing-account=<BILLING_ACCOUNT_ID>
   ```

4. Sign in and select the project:

   ```sh
   gcloud auth login
   gcloud auth application-default login
   gcloud config set project <PROJECT_ID>
   gcloud auth application-default set-quota-project <PROJECT_ID>
   ```

5. Create `.env` and set your project and region (`deploy.sh` passes these to Terraform automatically):

   ```sh
   cp .env.example .env
   ```

   Edit `.env`:

   ```sh
   GCP_PROJECT_ID=<PROJECT_ID>
   GCP_REGION=europe-west4
   ```

6. Deploy:

   ```sh
   ./deploy.sh
   ```

7. Open the **Mobile App** and **Redwood Console** URLs printed at the end (`http://localhost:<port>/` and `http://localhost:<port>/console`). Keep the terminal open; `Ctrl+C` closes the tunnel (run `./deploy.sh --proxy` to reopen it later).

### Other commands

| Command | Use |
| :---- | :---- |
| `./deploy.sh --proxy` | Reopen the local tunnel to `redwood-app` without redeploying |
| `./deploy.sh --dry-run` | Show the Terraform plan without changing anything |
| `./deploy.sh --skip-image-build` | Redeploy without rebuilding the container images |
| `./deploy.sh --skip-seed` | Redeploy without reseeding Firestore |
| `./deploy.sh --skip-bqml` | Redeploy without retraining the churn model |
| `./deploy.sh --skip-agent-deploy` | Redeploy without redeploying the agent |
| `./deploy.sh --seed-count 1000` | Seed 1,000 customers instead of 400 |
| `./deploy.sh --no-proxy` | Deploy without opening the local tunnel |
| `./deploy.sh --teardown` | Delete all deployed resources (the project is kept) |

> [!IMPORTANT]
> To deploy into a different project from the same checkout, run `./deploy.sh --teardown` first.

---

## 2\. The two demo customers

The seeder creates two personas with deliberately opposite histories. Churn risk runs once a day, but for demo purposes you can manually trigger it from the console.

---

## 3\. Demo churn risk and loyalty offer

A suggested order, roughly ten minutes:

1. **Press Reset Demo** in the console. `customer_sessions` and `loyalty_offers` are empty, and the agent is warm.
2. **Log in as `demo1`.** Nothing is issued. Point at `skipReason: LOW_CHURN_RISK` on the session document: BigQuery called this customer LOW, so the agent declined to spend margin.
3. **Place an order as `demo1`.** Full price. There is no tier discount in the system at all — the only thing that can discount an order is an offer the agent decided to issue.
4. **Switch to `demo2` and log in.** An offer appears in `loyalty_offers` within seconds and reaches the phone over SSE (Server-Sent Events), carrying the churn probability and tier that justified it. 
5. **Place an order as `demo2` with 5 star rating.** The same cart is now 15% cheaper with free shipping, and the ledger line names the offer and its promo code. 
6. **Go to Console and press Recalculate Churn.** The purchase and the rating move `cust_demo2` improved, but still HIGH. The customer is recovering, not recovered.
7. **Log in as `demo2` once more.** The agent issues a *follow-up* at a stepped-down 10%, carrying `offerSequence: 2` and `supersedesOfferId` pointing at the offer just redeemed. The discount tracks the risk down.
8. **Go to Console and press Recalculate Churn again.** The purchase and the rating move `cust_demo2` improved, but still HIGH
9. **Log in once more.** No offer issued. In console you will see `skipReason: FOLLOW_UP_LIMIT_REACHED`. One follow-up is the cap, so the agent cannot discount its way into a spiral. Had the first offer still been ACTIVE rather than redeemed, the reason would instead read `ACTIVE_OFFER_ALREADY_EXISTS` — the guardrails hold on both paths.
10. **Go to Console and press Recalculate Churn again.** The churn rating for `cust_demo2` is now LOW risk.,

---

## 4\. Demo bad review

An offer carries two tiers side by side: `churnRiskTier` is the model's, and `eligibilityTier` is the one the agent acted on. On a normal run they agree. They can only differ through the escalation path, which is the secondary demo flow worth showing on `cust_demo1`:

1. `cust_demo1` is healthy, so BigQuery scores it below the gate (`LOW`) and the agent would normally skip it.
2. **Place an order as `demo1`, rate that order 1 star, and pick a complaint reason.** That writes `customerFeedback.feedbackTimestamp` onto the order.
3. **Go to Console and press Recalculate Churn.** The purchase and the rating move `cust_demo1` declined, but is still LOW risk.
4. **Log in as `demo1` again.** Customer is LOW Risk, however the Agent will judge whether or not create a loyalty offer. Read `escalationReason` and `escalationTrigger` in the Loyalty offer document in the console. This is the Agent reasoning whether to give an offer or not.

**Press Reset demo** in the console to start over
