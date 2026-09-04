"""
FastAPI Server for Redwood Retail Mobile App Client.
Connects to Google Cloud Firestore Enterprise Native (database: redwood, collection: retail)
and serves catalog, profiles, order preview, order submission, and order tracking.
"""

import os
import sys
import logging
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# Set up paths
PARENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from firestore_auth import get_firestore_native_client
from retail_catalog import (
    CATALOG_ITEMS, SHIPPING_CITIES, WAREHOUSES, CARRIERS,
    COMPLAINT_REASONS, LOYALTY_TIERS
)
from mobile_client.backend.order_engine import (
    create_order_from_cart, DEMO_PRINCIPALS, CATALOG_BY_SKU
)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("redwood-mobile-api")

# Environment
PROJECT_ID = os.getenv("GCP_PROJECT_ID", "elevate-cyvisser")
DATABASE_ID = os.getenv("FIRESTORE_DATABASE_ID", "redwood")
COLLECTION_NAME = os.getenv("FIRESTORE_COLLECTION", "retail")

# Lazy-loaded Firestore client
_firestore_client = None


def get_db():
    global _firestore_client
    if _firestore_client is None:
        try:
            _firestore_client = get_firestore_native_client(PROJECT_ID, DATABASE_ID)
        except Exception as e:
            logger.error(f"Failed to initialize Firestore client: {e}")
            raise
    return _firestore_client


app = FastAPI(
    title="Redwood Retail Mobile API",
    description="Backend API connecting the mobile retail app to Firestore Enterprise Native",
    version="2.0.0"
)

# Enable CORS for Vite and mobile access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request Models
class CartItem(BaseModel):
    sku: str
    quantity: int = Field(gt=0, default=1)
    name: Optional[str] = None
    category: Optional[str] = None
    unitPrice: Optional[float] = None
    allocatedWarehouse: Optional[str] = None


class OrderRequest(BaseModel):
    principalId: str = "demo1"
    items: List[CartItem]
    shippingAddress: Optional[Dict[str, str]] = None
    paymentMethod: str = "INVOICE_NET30"
    carrierCode: Optional[str] = None
    serviceLevel: str = "NEXT_DAY_AIR"
    feedbackRating: int = Field(ge=1, le=5, default=5)
    feedbackText: Optional[str] = None
    complaintReason: Optional[str] = None
    dryRun: bool = False


@app.get("/api/health")
def health_check():
    """Checks service health and Firestore database connection."""
    firestore_status = "unknown"
    doc_count_sample = 0
    try:
        db = get_db()
        # Verify collection access with a lightweight query
        docs = list(db.collection(COLLECTION_NAME).limit(1).stream())
        firestore_status = "connected"
        doc_count_sample = len(docs)
    except Exception as e:
        firestore_status = f"error: {str(e)[:100]}"

    return {
        "status": "healthy",
        "projectId": PROJECT_ID,
        "databaseId": DATABASE_ID,
        "collection": COLLECTION_NAME,
        "firestoreConnection": firestore_status,
        "sampleDocAvailable": doc_count_sample > 0
    }


@app.get("/api/principals")
def get_principals():
    """Returns metadata and profiles for demo1 and demo2 IAM principals."""
    return {
        "activePrincipals": ["demo1", "demo2"],
        "profiles": DEMO_PRINCIPALS
    }


@app.get("/api/catalog")
def get_catalog():
    """Returns the industrial hardware product catalog grouped by category."""
    categories = sorted(list({item["category"] for item in CATALOG_ITEMS}))
    return {
        "items": CATALOG_ITEMS,
        "categories": categories,
        "warehouses": WAREHOUSES,
        "carriers": CARRIERS,
        "cities": SHIPPING_CITIES,
        "complaintReasons": COMPLAINT_REASONS,
        "loyaltyTiers": LOYALTY_TIERS
    }


@app.post("/api/orders/preview")
def preview_order(req: OrderRequest):
    """
    Simulates order creation and returns the generated JSON document
    without saving to Firestore.
    """
    if not req.items:
        raise HTTPException(status_code=400, detail="Cart cannot be empty")
    
    cart_dicts = [item.model_dump() for item in req.items]
    order_doc = create_order_from_cart(
        cart_items=cart_dicts,
        principal_id=req.principalId,
        shipping_address=req.shippingAddress,
        payment_method=req.paymentMethod,
        carrier_code=req.carrierCode,
        service_level=req.serviceLevel,
        feedback_rating=req.feedbackRating,
        feedback_text=req.feedbackText,
        complaint_reason=req.complaintReason
    )
    return {
        "order": order_doc,
        "parityVerified": True,
        "sourcePlatform": "CUSTOM_MOBILE_APP"
    }


@app.post("/api/orders/submit")
def submit_order(req: OrderRequest):
    """
    Generates the retail order document and persists it directly to
    Firestore Native database 'redwood', collection 'retail'.
    """
    if not req.items:
        raise HTTPException(status_code=400, detail="Cart cannot be empty")

    cart_dicts = [item.model_dump() for item in req.items]
    order_doc = create_order_from_cart(
        cart_items=cart_dicts,
        principal_id=req.principalId,
        shipping_address=req.shippingAddress,
        payment_method=req.paymentMethod,
        carrier_code=req.carrierCode,
        service_level=req.serviceLevel,
        feedback_rating=req.feedbackRating,
        feedback_text=req.feedbackText,
        complaint_reason=req.complaintReason
    )
    order_id = order_doc["orderId"]

    if req.dryRun:
        return {
            "status": "dry_run_success",
            "orderId": order_id,
            "order": order_doc,
            "message": "Order simulated successfully without writing to Firestore"
        }

    try:
        db = get_db()
        coll = db.collection(COLLECTION_NAME)
        doc_ref = coll.document(order_id)
        doc_ref.set(order_doc)
        logger.info(f"Successfully committed order {order_id} to Firestore {COLLECTION_NAME}")
        return {
            "status": "success",
            "orderId": order_id,
            "order": order_doc,
            "message": f"Order {order_id} committed to Firestore collection '{COLLECTION_NAME}' in database '{DATABASE_ID}'"
        }
    except Exception as e:
        logger.error(f"Firestore write failed for order {order_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to write order to Firestore: {str(e)}"
        )


@app.get("/api/orders")
def list_orders(
    principal_id: Optional[str] = Query(None, alias="principalId"),
    limit: int = Query(30, ge=1, le=100)
):
    """
    Retrieves recent orders directly from Firestore collection 'retail'.
    Can optionally filter by IAM principal (demo1 or demo2).
    """
    try:
        db = get_db()
        coll = db.collection(COLLECTION_NAME)
        
        # Query recent documents
        if principal_id:
            query = coll.where("customerId", "==", principal_id).limit(limit)
        else:
            query = coll.limit(limit)

        docs = query.stream()
        orders = []
        for doc in docs:
            data = doc.to_dict()
            if data:
                orders.append(data)

        # Sort descending by createdAt
        orders.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return {
            "totalReturned": len(orders),
            "orders": orders
        }
    except Exception as e:
        logger.error(f"Failed to list orders from Firestore: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to query Firestore orders: {str(e)}"
        )


@app.get("/api/orders/{order_id}")
def get_order_by_id(order_id: str):
    """Retrieves a single order from Firestore by its orderId."""
    try:
        db = get_db()
        doc = db.collection(COLLECTION_NAME).document(order_id).get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
        return {"order": doc.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch order {order_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error reading order from Firestore: {str(e)}"
        )


# Serve compiled frontend SPA if dist/ exists
DIST_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "dist"))
if os.path.exists(DIST_DIR):
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    
    assets_dir = os.path.join(DIST_DIR, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str):
        if full_path.startswith("api"):
            raise HTTPException(status_code=404, detail="API route not found")
        target_path = os.path.join(DIST_DIR, full_path)
        if os.path.exists(target_path) and os.path.isfile(target_path):
            return FileResponse(target_path)
        return FileResponse(os.path.join(DIST_DIR, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

