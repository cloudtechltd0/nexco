# main.py — Core FastAPI application: routes, middleware, and startup logic.
#
# Run locally with:
#   pip install fastapi uvicorn sqlalchemy aiosqlite httpx python-dotenv
#   uvicorn main:app --reload --port 8000

import asyncio
import logging
import os
import uuid
from datetime import datetime
from typing import List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db, init_db
from models import Package, PackageType, Transaction, TransactionStatus, User

# ─── Environment ───────────────────────────────────────────────────────────────

# Load variables from backend/.env into os.environ.
# The file must sit next to main.py and contain:
#   PALPLUSS_AUTH=Basic YOUR_TOKEN_HERE
#   PALPLUSS_URL=https://api.palpluss.com/v1/payments/stk
#   CALLBACK_URL=https://netwave-gateway.slclub8.workers.dev/mpesa/callback
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

PALPLUSS_AUTH = os.environ["PALPLUSS_AUTH"]       # e.g. "Basic abc123=="
PALPLUSS_URL  = os.environ["PALPLUSS_URL"]        # STK push endpoint
CALLBACK_URL  = os.environ["CALLBACK_URL"]        # your public webhook URL

# ─── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(levelname)s │ %(message)s")
log = logging.getLogger(__name__)

# ─── Application ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Telecom Dashboard API",
    description="High-performance async API for data, voice, SMS, and combo bundle purchasing.",
    version="1.0.0",
)

# Allow the HTML/JS frontend (served from any origin during dev, tighten in prod)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://nexcowireless.vercel.app"],          # restrict to your domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Pydantic Schemas ──────────────────────────────────────────────────────────

class PackageOut(BaseModel):
    id:            int
    name:          str
    type:          PackageType
    data_gb:       Optional[float]
    minutes:       Optional[int]
    sms:           Optional[int]
    price:         float
    validity_days: int
    description:   Optional[str]
    is_active:     bool

    class Config:
        from_attributes = True


class BuyRequest(BaseModel):
    phone_number: str  = Field(..., example="+254712345678")
    package_id:   int  = Field(..., example=1)


class BuyResponse(BaseModel):
    transaction_id: int
    reference_code: str
    status:         TransactionStatus
    message:        str


class PalplussTransaction(BaseModel):
    """
    Inner 'transaction' block that Palpluss wraps the result in.
    All fields use aliases matching the Worker's extraction logic:

        target.id || target.transactionId || target.transaction_id
        target.status || target.state
        target.amount
        target.phone_number || target.phone || target.msisdn
        target.mpesa_receipt || target.mpesaReceipt || target.reference
        target.external_reference || target.accountReference || target.account_reference
        target.result_desc || target.description || target.remarks
    """
    # ── Identity ──────────────────────────────────────────────────────────────
    id:                 Optional[str]   = Field(None, alias="id")
    transaction_id:     Optional[str]   = Field(None, alias="transactionId")
    transaction_id_ul:  Optional[str]   = Field(None, alias="transaction_id")

    # ── State ─────────────────────────────────────────────────────────────────
    status:             Optional[str]   = Field(None)   # "SUCCESS" | "FAILED" | …
    state:              Optional[str]   = Field(None)

    # ── Financials ────────────────────────────────────────────────────────────
    amount:             Optional[float] = Field(None)

    # ── Subscriber ────────────────────────────────────────────────────────────
    phone_number:       Optional[str]   = Field(None)
    phone:              Optional[str]   = Field(None)
    msisdn:             Optional[str]   = Field(None)

    # ── Receipt / Reference ───────────────────────────────────────────────────
    mpesa_receipt:      Optional[str]   = Field(None)
    mpesaReceipt:       Optional[str]   = Field(None)
    reference:          Optional[str]   = Field(None)   # echoed TXN-XXXXXXXXXX

    external_reference: Optional[str]   = Field(None)
    accountReference:   Optional[str]   = Field(None)
    account_reference:  Optional[str]   = Field(None)

    # ── Human-readable outcome ────────────────────────────────────────────────
    result_desc:        Optional[str]   = Field(None)
    description:        Optional[str]   = Field(None)
    remarks:            Optional[str]   = Field(None)

    class Config:
        populate_by_name = True   # accept both alias and field name

    # ── Resolved helpers ──────────────────────────────────────────────────────

    @property
    def resolved_status(self) -> Optional[str]:
        """First non-null of status | state, upper-cased."""
        raw = self.status or self.state
        return raw.upper() if raw else None

    @property
    def resolved_reference(self) -> Optional[str]:
        """
        The value we stored as reference_code in the DB when we called /api/buy.
        Palpluss echoes it back in external_reference / accountReference / account_reference.
        Falls back to mpesa_receipt / mpesaReceipt / reference as a last resort.
        """
        return (
            self.external_reference
            or self.accountReference
            or self.account_reference
            or self.mpesa_receipt
            or self.mpesaReceipt
            or self.reference
        )

    @property
    def resolved_gateway_id(self) -> Optional[str]:
        """Palpluss internal transaction ID."""
        return self.id or self.transaction_id or self.transaction_id_ul


class WebhookPayload(BaseModel):
    """
    Top-level envelope that Palpluss POSTs to CALLBACK_URL.
    The Cloudflare Worker resolves: payload.transaction || payload.data || payload
    so we accept all three shapes here and normalise inside the endpoint.
    """
    transaction: Optional[PalplussTransaction] = None
    data:        Optional[PalplussTransaction] = None

    # Flat payloads: Palpluss may also POST the fields at the root level
    # (i.e. no wrapper object). We embed PalplussTransaction fields inline
    # via model_validator so a flat body still deserialises cleanly.
    id:                 Optional[str]   = None
    transaction_id:     Optional[str]   = Field(None, alias="transactionId")
    transaction_id_ul:  Optional[str]   = Field(None, alias="transaction_id")
    status:             Optional[str]   = None
    state:              Optional[str]   = None
    amount:             Optional[float] = None
    phone_number:       Optional[str]   = None
    phone:              Optional[str]   = None
    msisdn:             Optional[str]   = None
    mpesa_receipt:      Optional[str]   = None
    mpesaReceipt:       Optional[str]   = None
    reference:          Optional[str]   = None
    external_reference: Optional[str]   = None
    accountReference:   Optional[str]   = None
    account_reference:  Optional[str]   = None
    result_desc:        Optional[str]   = None
    description:        Optional[str]   = None
    remarks:            Optional[str]   = None

    class Config:
        populate_by_name = True

    def resolve(self) -> PalplussTransaction:
        """
        Mirror the Worker's extraction logic:
            target = payload.transaction || payload.data || payload
        Returns a PalplussTransaction regardless of which envelope shape arrived.
        """
        if self.transaction:
            return self.transaction
        if self.data:
            return self.data
        # Flat root-level payload — reconstruct as PalplussTransaction
        return PalplussTransaction(
            id                 = self.id,
            transactionId      = self.transaction_id,
            transaction_id     = self.transaction_id_ul,
            status             = self.status,
            state              = self.state,
            amount             = self.amount,
            phone_number       = self.phone_number,
            phone              = self.phone,
            msisdn             = self.msisdn,
            mpesa_receipt      = self.mpesa_receipt,
            mpesaReceipt       = self.mpesaReceipt,
            reference          = self.reference,
            external_reference = self.external_reference,
            accountReference   = self.accountReference,
            account_reference  = self.account_reference,
            result_desc        = self.result_desc,
            description        = self.description,
            remarks            = self.remarks,
        )


class WebhookResponse(BaseModel):
    transaction_id: int
    new_status:     TransactionStatus
    message:        str


# ─── Startup / Shutdown ────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    """Initialise DB tables and seed sample packages if the table is empty."""
    await init_db()
    await seed_packages()
    log.info("🚀 Telecom API ready.")


async def seed_packages():
    """
    Populate the packages table with representative KES bundles on first run.
    Idempotent: does nothing if packages already exist.
    """
    from database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Package).limit(1))
        if result.scalars().first():
            return   # already seeded

        samples = [
            # ── Data bundles ───────────────────────────────────────────────
            Package(name="Starter 200MB", type=PackageType.data,
                    data_gb=0.2, price=20, validity_days=1,
                    description="Quick top-up for light browsing."),
            Package(name="Daily 1GB", type=PackageType.data,
                    data_gb=1, price=50, validity_days=1,
                    description="Stream, browse, scroll — all day."),
            Package(name="Weekly 5GB", type=PackageType.data,
                    data_gb=5, price=200, validity_days=7,
                    description="Stay connected all week."),
            Package(name="Monthly 15GB", type=PackageType.data,
                    data_gb=15, price=500, validity_days=30,
                    description="Heavy users & remote workers."),
            Package(name="Power 30GB", type=PackageType.data,
                    data_gb=30, price=900, validity_days=30,
                    description="Stream 4K, upload large files."),

            # ── Voice / Minutes bundles ─────────────────────────────────────
            Package(name="Talk 30", type=PackageType.minutes,
                    minutes=30, price=30, validity_days=7,
                    description="30 on-net minutes for light callers."),
            Package(name="Talk 100", type=PackageType.minutes,
                    minutes=100, price=80, validity_days=30,
                    description="100 on-net minutes per month."),
            Package(name="Unlimited Voice 7D", type=PackageType.minutes,
                    minutes=9999, price=150, validity_days=7,
                    description="Call as much as you want for 7 days."),

            # ── SMS bundles ────────────────────────────────────────────────
            Package(name="SMS 50", type=PackageType.sms,
                    sms=50, price=10, validity_days=7,
                    description="50 texts to any network."),
            Package(name="SMS 200", type=PackageType.sms,
                    sms=200, price=30, validity_days=30,
                    description="Bulk texts for a full month."),

            # ── Combo bundles ──────────────────────────────────────────────
            Package(name="Value Pack", type=PackageType.combo,
                    data_gb=1, minutes=50, sms=100, price=99, validity_days=7,
                    description="Data + voice + SMS in one weekly pack."),
            Package(name="Family Combo", type=PackageType.combo,
                    data_gb=5, minutes=200, sms=500, price=350, validity_days=30,
                    description="Enough for the whole household."),
            Package(name="Business Bundle", type=PackageType.combo,
                    data_gb=20, minutes=500, sms=1000, price=999, validity_days=30,
                    description="Enterprise-grade connectivity."),
        ]

        db.add_all(samples)
        await db.commit()
        log.info(f"✅ Seeded {len(samples)} sample packages.")


# ─── Palpluss STK Push ────────────────────────────────────────────────────────

async def push_stk(
    phone: str,
    amount: float,
    reference_code: str,
    package_name: str,
) -> dict:
    """
    Fire STK Push via Palpluss gateway.
    """

    # ── Normalize phone ─────────────────────────────────────────────
    normalised_phone = phone.lstrip("+").lstrip("0")
    if not normalised_phone.startswith("254"):
        normalised_phone = f"254{normalised_phone}"

    payload = {
        "phone": normalised_phone,
        "amount": int(amount),
        "reference": reference_code,

        # IMPORTANT: keep both fields (some gateways use either)
        "description": f"DataHub – {package_name}",
        "transaction_desc": f"DataHub – {package_name}",

        "callback_url": CALLBACK_URL,
    }

    headers = {
        "Authorization": PALPLUSS_AUTH,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(PALPLUSS_URL, json=payload, headers=headers)

            # ── Parse response safely ───────────────────────────────
            try:
                data = resp.json()
            except Exception:
                log.error(f"❌ Invalid JSON from gateway: {resp.text}")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Invalid response from payment gateway.",
                )

            # ── SUCCESS CONDITION (VERY IMPORTANT FIX) ──────────────
            is_success = (
                resp.status_code in [200, 201]
                and isinstance(data, dict)
                and data.get("success") is True
            )

            if not is_success:
                log.error(f"❌ STK FAILED {resp.status_code}")
                log.error(f"Response: {data}")
                log.error(f"Payload: {payload}")

                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="STK push rejected by payment gateway.",
                )

            # ── EXTRACT TRACKING IDS (CRITICAL FOR WEBHOOK MATCHING) ─
            gateway_tx = (
                data.get("data", {}).get("transactionId")
                or data.get("data", {}).get("providerCheckoutId")
                or data.get("requestId")
                or data.get("data", {}).get("id")
            )

            checkout_id = data.get("data", {}).get("providerCheckoutId")
            status_code = data.get("data", {}).get("status")

            log.info(
                f"📲 STK SUCCESS → ref={reference_code} "
                f"phone={normalised_phone} "
                f"gateway_tx={gateway_tx} "
                f"checkout_id={checkout_id} "
                f"status={status_code}"
            )

            return data

    except httpx.HTTPStatusError as exc:
        log.error(f"❌ Palpluss HTTP error {exc.response.status_code}: {exc.response.text}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Payment gateway error.",
        )

    except httpx.RequestError as exc:
        log.error(f"❌ Palpluss connection error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach payment gateway.",
        )

# ─── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/packages", response_model=List[PackageOut], tags=["Packages"])
async def list_packages(
    type: Optional[PackageType] = None,
    db:   AsyncSession           = Depends(get_db),
):
    """
    Return all active packages.  Optionally filter by type query-string param:
        GET /api/packages?type=data
    """
    query = select(Package).where(Package.is_active == True)
    if type:
        query = query.where(Package.type == type)
    query = query.order_by(Package.price)

    result = await db.execute(query)
    packages = result.scalars().all()
    return packages


@app.post("/api/buy", response_model=BuyResponse, status_code=status.HTTP_202_ACCEPTED,
          tags=["Purchases"])
async def buy_package(
    payload: BuyRequest,
    db:      AsyncSession = Depends(get_db),
):
    """
    Initiate a bundle purchase:
    1. Resolve or create the subscriber by phone number.
    2. Look up the requested package.
    3. Create a PENDING transaction with a unique reference code.
    4. Return the reference so the frontend can poll or display it.

    The transaction stays pending until the payment gateway posts a
    confirmation to /api/payment/webhook.
    """
    # 1. Resolve subscriber (auto-create if first purchase)
    user_result = await db.execute(
        select(User).where(User.phone_number == payload.phone_number)
    )
    user = user_result.scalars().first()

    if not user:
        user = User(
            username=f"user_{uuid.uuid4().hex[:8]}"
            phone_number=payload.phone_number,
            balance=0.0,
        )
        db.add(user)
        await db.flush()   # get the generated ID without full commit

    # 2. Validate the package
    pkg_result = await db.execute(
        select(Package).where(Package.id == payload.package_id, Package.is_active == True)
    )
    package = pkg_result.scalars().first()

    if not package:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Package {payload.package_id} not found or inactive.",
        )

    # 3. Create PENDING transaction
    ref = f"TXN-{uuid.uuid4().hex[:10].upper()}"   # e.g. TXN-A3F9B21C08
    transaction = Transaction(
        user_id        = user.id,
        package_id     = package.id,
        amount         = package.price,
        status         = TransactionStatus.pending,
        reference_code = ref,
    )
    db.add(transaction)
    await db.commit()
    await db.refresh(transaction)

    log.info(f"🛒 Purchase initiated → ref={ref} phone={payload.phone_number} pkg={package.name}")

    # 4. Trigger the real STK Push via Palpluss.
    #    If the gateway call fails (502) the exception propagates and the
    #    pending transaction is left in the DB for reconciliation / retry.
    await push_stk(
        phone          = payload.phone_number,
        amount         = package.price,
        reference_code = ref,
        package_name   = package.name,
    )

    return BuyResponse(
        transaction_id = transaction.id,
        reference_code = ref,
        status         = TransactionStatus.pending,
        message        = (
            f"Payment request sent to {payload.phone_number}. "
            f"Approve the KES {package.price:.0f} prompt to activate your {package.name}."
        ),
    )


@app.post("/api/payment/webhook", response_model=WebhookResponse, tags=["Payments"])
async def payment_webhook(
    payload: WebhookPayload,
    db:      AsyncSession = Depends(get_db),
):
    """
    Receives the POST from the Cloudflare Worker after Palpluss confirms or
    rejects an M-Pesa STK Push.

    Normalisation mirrors the Worker's extraction chain exactly:
        target = payload.transaction || payload.data || payload (flat)

    From target we read:
        reference  → external_reference | accountReference | account_reference
                     (this is the TXN-XXXXXXXXXX we generated in /api/buy)
        status     → status | state   (SUCCESS / FAILED / …)

    On SUCCESS  → transaction → completed, then provision bundle in background.
    On anything else → transaction → failed.
    """
    # ── 1. Normalise envelope ─────────────────────────────────────────────────
    target = payload.resolve()

    ref    = target.resolved_reference
    gw_status = target.resolved_status
    gw_id     = target.resolved_gateway_id

    log.info(
        f"📩 Webhook received → gw_id={gw_id} ref={ref} "
        f"status={gw_status} amount={target.amount}"
    )

    # ── 2. Validate extracted fields (mirrors Worker's sanity check) ──────────
    if not gw_status or not ref:
        log.error(
            f"❌ Webhook mapping failure — status={gw_status!r} ref={ref!r} "
            f"raw_keys={list(payload.model_dump(exclude_none=True).keys())}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error":         "Mapping extraction failure",
                "resolved_ref":  ref,
                "resolved_status": gw_status,
            },
        )

    # ── 3. Locate the pending transaction by our reference code ───────────────
    tx_result = await db.execute(
        select(Transaction).where(Transaction.reference_code == ref)
    )
    transaction = tx_result.scalars().first()

    if not transaction:
        log.warning(f"⚠️  No transaction found for ref={ref!r}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No transaction found for reference {ref!r}.",
        )

    if transaction.status != TransactionStatus.pending:
        # Idempotency guard — Palpluss may retry the callback
        log.info(
            f"ℹ️  Duplicate webhook ignored → ref={ref} "
            f"already={transaction.status}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Transaction already in terminal state: {transaction.status}.",
        )

    # ── 4. Transition state ───────────────────────────────────────────────────
    if gw_status == "SUCCESS":
        transaction.status = TransactionStatus.completed
        await db.commit()
        await db.refresh(transaction)

        # Fire provisioning without blocking the response to Palpluss/Worker.
        # The Worker expects a fast 200; a slow response may cause retries.
        asyncio.create_task(provision_bundle(transaction))

        log.info(f"✅ Transaction completed → ref={ref} gw_receipt={target.mpesa_receipt or target.mpesaReceipt}")

        return WebhookResponse(
            transaction_id = transaction.id,
            new_status     = TransactionStatus.completed,
            message        = "Payment confirmed. Bundle is being activated.",
        )

    else:
        # FAILED, CANCELLED, TIMEOUT, or any other non-SUCCESS state
        transaction.status = TransactionStatus.failed
        await db.commit()

        reason = target.result_desc or target.description or target.remarks or gw_status
        log.warning(f"❌ Transaction failed → ref={ref} reason={reason!r}")

        return WebhookResponse(
            transaction_id = transaction.id,
            new_status     = TransactionStatus.failed,
            message        = f"Payment not completed: {reason}. No bundle was activated.",
        )


# ─── Health check ──────────────────────────────────────────────────────────────

@app.get("/health", tags=["Meta"])
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
