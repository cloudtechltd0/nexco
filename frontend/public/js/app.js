/**
 * app.js — Telecom Dashboard frontend logic
 *
 * Responsibilities:
 *   1. Fetch active packages from the backend on page load.
 *   2. Render package cards into the grid.
 *   3. Filter displayed cards when a category tab is clicked.
 *   4. Drive the purchase modal: open, submit, loading state, success/error feedback.
 *
 * No framework dependencies — pure ES6 modules.
 * Payment confirmation is handled server-side via Palpluss → CALLBACK_URL webhook.
 */

// ─── Config ───────────────────────────────────────────────────────────────────

const API_BASE = "https://nexco-tno9.onrender.com";   // swap to your deployed URL in prod

// ─── State ────────────────────────────────────────────────────────────────────

let allPackages  = [];     // master list fetched from API
let activeFilter = "all";  // currently selected tab
let selectedPkg  = null;   // package the user clicked "Buy" on

// ─── DOM References ───────────────────────────────────────────────────────────

const grid       = document.getElementById("package-grid");
const tabs       = document.querySelectorAll("[data-filter]");
const modal      = document.getElementById("purchase-modal");
const modalName  = document.getElementById("modal-pkg-name");
const modalPrice = document.getElementById("modal-pkg-price");
const modalDesc  = document.getElementById("modal-pkg-desc");
const phoneInput = document.getElementById("phone-input");
const buyBtn     = document.getElementById("btn-buy");
const closeBtn   = document.getElementById("btn-close-modal");
const toastEl    = document.getElementById("toast");
const skeletonEl = document.getElementById("skeleton-loader");

// ─── Utilities ────────────────────────────────────────────────────────────────

/**
 * Format a price in KES with no decimal for whole shillings,
 * one decimal for cents (e.g. 20 → "KES 20", 99.5 → "KES 99.50").
 */
function formatPrice(kes) {
    return `KES ${Number.isInteger(kes) ? kes : kes.toFixed(2)}`;
}

/**
 * Derive a concise "what's included" summary string from a package object.
 */
function buildBundleSummary(pkg) {
    const parts = [];
    if (pkg.data_gb)  parts.push(pkg.data_gb >= 1 ? `${pkg.data_gb}GB` : `${pkg.data_gb * 1000}MB`);
    if (pkg.minutes)  parts.push(pkg.minutes >= 9000 ? "Unlimited calls" : `${pkg.minutes} mins`);
    if (pkg.sms)      parts.push(`${pkg.sms} SMS`);
    return parts.join(" · ") || "Bundle";
}

/**
 * Return the Tailwind accent classes for each package type.
 */
function typeAccent(type) {
    const map = {
        data:    { badge: "bg-sky-100 text-sky-700",        icon: "fa fa-signal",        ring: "hover:ring-sky-400" },
        minutes: { badge: "bg-violet-100 text-violet-700",  icon: "fa fa-phone",         ring: "hover:ring-violet-400" },
        sms:     { badge: "bg-amber-100 text-amber-700",    icon: "fa fa-comment",       ring: "hover:ring-amber-400" },
        combo:   { badge: "bg-emerald-100 text-emerald-700",icon: "fa fa-bolt",          ring: "hover:ring-emerald-400" },
    };
    return map[type] || map.data;
}

/**
 * Show a transient toast notification at the bottom of the screen.
 * variant: "success" | "error"
 */
function showToast(message, variant = "success") {
    toastEl.textContent = message;
    toastEl.className = [
        "fixed bottom-6 left-1/2 -translate-x-1/2 z-50 px-5 py-3 rounded-xl",
        "text-sm font-medium shadow-lg transition-all duration-300",
        variant === "success"
            ? "bg-emerald-600 text-white"
            : "bg-red-600 text-white",
    ].join(" ");
    toastEl.classList.remove("opacity-0", "pointer-events-none");

    setTimeout(() => {
        toastEl.classList.add("opacity-0", "pointer-events-none");
    }, 4000);
}

// ─── Data Fetching ────────────────────────────────────────────────────────────

async function fetchPackages() {
    skeletonEl.classList.remove("hidden");
    grid.innerHTML = "";

    try {
        const res = await fetch(`${API_BASE}/api/packages`);

        if (!res.ok) {
            throw new Error(`Server returned ${res.status}`);
        }

        allPackages = await res.json();
        renderPackages(allPackages);

    } catch (err) {
        grid.innerHTML = `
            <div class="col-span-full flex flex-col items-center gap-3 py-16 text-slate-400">
                <span class="text-4xl">⚠️</span>
                <p class="font-medium">Could not load packages</p>
                <p class="text-xs">${err.message}</p>
                <button onclick="fetchPackages()"
                    class="mt-2 px-4 py-2 text-xs bg-slate-800 text-white rounded-lg hover:bg-slate-700">
                    Retry
                </button>
            </div>`;
    } finally {
        skeletonEl.classList.add("hidden");
    }
}

// ─── Rendering ────────────────────────────────────────────────────────────────

function renderPackages(packages) {
    if (!packages.length) {
        grid.innerHTML = `
            <div class="col-span-full py-16 text-center text-slate-400">
                <span class="text-4xl block mb-3">📭</span>
                <p class="font-medium">No packages in this category</p>
            </div>`;
        return;
    }

    grid.innerHTML = packages.map(pkg => buildCard(pkg)).join("");

    // Attach buy-button listeners after DOM insertion
    grid.querySelectorAll("[data-pkg-id]").forEach(btn => {
        btn.addEventListener("click", () => openModal(Number(btn.dataset.pkgId)));
    });
}

function buildCard(pkg) {
    const accent   = typeAccent(pkg.type);
    const summary  = buildBundleSummary(pkg);
    const validity = pkg.validity_days === 1
        ? "24 hours"
        : pkg.validity_days < 30
            ? `${pkg.validity_days} days`
            : "30 days";

    return `
    <article class="relative bg-white/90 backdrop-blur-md rounded-2xl p-5 shadow-xl
                border border-white/30 ring-2 ring-transparent ${accent.ring}
                transition-all duration-200 flex flex-col gap-4">

        <!-- Type badge -->
        <div class="flex items-center justify-between">
            <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full
                         text-xs font-semibold uppercase tracking-wide ${accent.badge}">
                <i class="${accent.icon}"></i> ${pkg.type}
            </span>
            <span class="text-xs text-slate-400">${validity}</span>
        </div>

        <!-- Name & summary -->
        <div>
            <h3 class="font-bold text-slate-800 text-base leading-snug">${pkg.name}</h3>
            <p class="text-sm text-slate-500 mt-0.5">${summary}</p>
        </div>

        <!-- Description -->
        ${pkg.description
            ? `<p class="text-xs text-slate-400 leading-relaxed">${pkg.description}</p>`
            : ""}

        <!-- Price + CTA -->
        <div class="flex items-center justify-between mt-auto">
            <span class="text-2xl font-extrabold text-slate-900">${formatPrice(pkg.price)}</span>
            <button data-pkg-id="${pkg.id}"
                    class="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 active:scale-95
                           text-white text-sm font-semibold rounded-xl transition-all duration-150">
                Buy now
            </button>
        </div>
    </article>`;
}

// ─── Filtering ────────────────────────────────────────────────────────────────

function applyFilter(filter) {
    activeFilter = filter;

    // Update tab highlight styles
    tabs.forEach(tab => {
        const isActive = tab.dataset.filter === filter;
        tab.classList.toggle("bg-indigo-600",  isActive);
        tab.classList.toggle("text-white",     isActive);
        tab.classList.toggle("shadow-sm",      isActive);
        tab.classList.toggle("text-slate-500", !isActive);
        tab.classList.toggle("bg-white",       !isActive);
    });

    const filtered = filter === "all"
        ? allPackages
        : allPackages.filter(p => p.type === filter);

    renderPackages(filtered);
}

// ─── Modal ────────────────────────────────────────────────────────────────────

function openModal(pkgId) {
    selectedPkg = allPackages.find(p => p.id === pkgId);
    if (!selectedPkg) return;

    modalName.textContent  = selectedPkg.name;
    modalPrice.textContent = formatPrice(selectedPkg.price);
    modalDesc.textContent  = buildBundleSummary(selectedPkg);
    phoneInput.value       = "";
    resetBuyButton();

    modal.classList.remove("hidden");
    modal.classList.add("flex");
    phoneInput.focus();
}

function closeModal() {
    modal.classList.add("hidden");
    modal.classList.remove("flex");
    selectedPkg = null;
}

function resetBuyButton() {
    buyBtn.disabled    = false;
    buyBtn.textContent = "Confirm Purchase";
    buyBtn.classList.remove("opacity-60", "cursor-not-allowed");
}

function setLoadingState() {
    buyBtn.disabled    = true;
    buyBtn.textContent = "Processing…";
    buyBtn.classList.add("opacity-60", "cursor-not-allowed");
}

// ─── Purchase Submission ──────────────────────────────────────────────────────

async function submitPurchase() {
    const phone = phoneInput.value.trim();

    if (!phone) {
        phoneInput.classList.add("border-red-400", "ring-red-200");
        phoneInput.focus();
        return;
    }
    phoneInput.classList.remove("border-red-400", "ring-red-200");

    // Snapshot selectedPkg immediately — closeModal() nulls it and any
    // async await below would otherwise lose the reference mid-flight.
    const pkg = selectedPkg;
    if (!pkg) return;

    setLoadingState();

    try {
        const res = await fetch(`${API_BASE}/api/buy`, {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({
                phone_number: phone,
                package_id:   pkg.id,
            }),
        });

        const data = await res.json();

        if (!res.ok) {
            // Surface the gateway or validation error returned by the backend
            throw new Error(data.detail || `Error ${res.status}`);
        }

        closeModal();

        // The Palpluss gateway will now send an STK Push prompt to the user's phone.
        // Once they approve it, Palpluss posts the result to CALLBACK_URL which calls
        // /api/payment/webhook — no further action needed from the frontend.
        showToast(
            `📲 M-Pesa prompt sent to ${phone}. Enter your PIN to activate ${pkg.name}.`,
            "success"
        );

    } catch (err) {
        showToast(`Purchase failed: ${err.message}`, "error");
        resetBuyButton();
    }
}

// ─── Event Wiring ─────────────────────────────────────────────────────────────

tabs.forEach(tab => {
    tab.addEventListener("click", () => applyFilter(tab.dataset.filter));
});

closeBtn.addEventListener("click", closeModal);
buyBtn.addEventListener("click", submitPurchase);

// Close modal when clicking the dark backdrop
modal.addEventListener("click", e => {
    if (e.target === modal) closeModal();
});

// Submit on Enter key inside the phone field
phoneInput.addEventListener("keydown", e => {
    if (e.key === "Enter") submitPurchase();
});

// ─── Boot ─────────────────────────────────────────────────────────────────────

(async () => {
    await fetchPackages();
    applyFilter("all");   // initialise tab highlight state
})();
