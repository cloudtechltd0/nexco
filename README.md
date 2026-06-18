# DataHub — Telecom Bundle Dashboard

A production-ready starter for a telecom reseller web dashboard.  
Users can browse, filter, and purchase data, voice, SMS, and combo bundles,  
with async backend processing and M-Pesa-style payment webhook handling.

---

## Project Structure

```
telecom-dashboard/
├── backend/
│   ├── main.py            ← FastAPI app: routes, CORS, startup seeding
│   ├── models.py          ← SQLAlchemy ORM: User, Package, Transaction
│   ├── database.py        ← Async engine, session factory, get_db dependency
│   └── requirements.txt   ← Python dependencies
│
└── frontend/
    └── public/
        ├── index.html     ← Single-page dashboard (Tailwind via CDN)
        └── js/
            └── app.js     ← Modular vanilla JS: fetch, render, filter, purchase
```

---

## Quick Start

### Backend

```bash
cd backend

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Start the API server (auto-reloads on code change)
uvicorn main:app --reload --port 8000
```

The API will be available at **http://localhost:8000**  
Interactive Swagger docs: **http://localhost:8000/docs**

On first run the server creates `telecom.db` (SQLite) and seeds 13 sample packages automatically.

---

### Frontend

The frontend is pure HTML + JS — no build step required.

**Option 1 — Python simple server (from the frontend/public directory):**
```bash
cd frontend/public
python -m http.server 3000
# Open http://localhost:3000
```

**Option 2 — VS Code Live Server extension:** right-click `index.html` → Open with Live Server.

**Option 3 — Direct file open:** open `frontend/public/index.html` in your browser.  
(Note: browser `file://` security restrictions may block fetch calls. Use a local server.)

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`  | `/api/packages` | List all active bundles. Optional `?type=data\|minutes\|sms\|combo` filter. |
| `POST` | `/api/buy` | Initiate a purchase. Returns `reference_code` for tracking. |
| `POST` | `/api/payment/webhook` | Simulate gateway callback. Transitions transaction to `completed` or `failed`. |
| `GET`  | `/health` | Health check — returns `{"status":"ok"}`. |

### POST /api/buy — Request body
```json
{
  "phone_number": "+254712345678",
  "package_id": 3
}
```

### POST /api/payment/webhook — Request body
```json
{
  "reference_code": "TXN-A3F9B21C08",
  "gateway_status": "SUCCESS",
  "amount": 200
}
```

---

## Production Checklist

- [ ] Replace `DATABASE_URL` in `database.py` with PostgreSQL DSN  
      (`postgresql+asyncpg://user:pass@host/dbname`)
- [ ] Set `CORS allow_origins` in `main.py` to your frontend domain
- [ ] Store `DATABASE_URL` and secrets in environment variables, not source code
- [ ] Replace the mock `provision_bundle()` function with your carrier BSS/OSS API call
- [ ] Replace `simulateWebhookConfirmation()` in `app.js` with real M-Pesa Daraja integration
- [ ] Add Alembic for database migrations
- [ ] Add JWT authentication to protect `/api/buy` and user-specific endpoints
- [ ] Set up a process manager (Gunicorn + Uvicorn workers) for multi-core deployment

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | HTML5, Tailwind CSS (CDN), Vanilla ES6 JS |
| Backend | Python 3.11+, FastAPI, Uvicorn |
| ORM | SQLAlchemy 2.0 (async) |
| Database | SQLite (dev) → PostgreSQL/MySQL (prod) |
| Payments | M-Pesa STK Push / webhook pattern (mockable) |