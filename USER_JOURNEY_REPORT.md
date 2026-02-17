# USER JOURNEY REPORT — IP Watch AI

> Investigation Date: 2026-02-11
> Domain: ipwatchai.com | Local: http://localhost:8000

---

## 1. Site Architecture Overview

### Type: Server-rendered SPA hybrid
- **Backend**: FastAPI (Python) with Jinja2 templates
- **Frontend Framework**: Alpine.js 3.13.3 (reactive data binding via `x-data`, `x-text`, `x-show`, etc.)
- **CSS Framework**: Tailwind CSS (loaded via CDN `cdn.tailwindcss.com`)
- **Design System**: Custom CSS variables in `static/css/tokens.css` for theming (light/dark mode)
- **Charts**: Chart.js (CDN)
- **Fonts**: Inter (sans-serif), JetBrains Mono (monospace)
- **PWA**: `manifest.json` + `sw.js` registered for offline/installability

### Template Structure
```
templates/
  dashboard.html          # Main SPA shell (134 lines, includes all partials)
  admin.html              # Superadmin panel (standalone page)
  pricing.html            # Plans & pricing page (standalone page)
  partials/
    _navbar.html           # Top nav + mobile drawer + bottom tab bar
    _search_panel.html     # Search input + filters + tab navigation
    _results_panel.html    # Overview tab: KPIs, alerts, deadlines, watchlist
    _leads_panel.html      # Opposition Radar tab (PRO feature)
    _ai_studio_panel.html  # AI Studio tab: Name Lab + Logo Studio
    _reports_panel.html    # Reports tab: generate & list
    _modals.html           # All modals: alert detail, opposition, lead detail,
                           # agentic search, upgrade, credits, quick-add watchlist,
                           # lightbox, entity portfolio, report generation
```

### Key JavaScript Files
```
static/js/
  utils/i18n.js         # Internationalization: locale loading, t() function
  utils/helpers.js       # Utility functions (escapeHtml, date formatting, etc.)
  utils/auth.js          # Auth token mgmt, plan detection, usage badges
  utils/toast.js         # Toast notification system
  components/
    score-badge.js        # Risk score color/badge rendering
    opposition-timeline.js # Opposition deadline timeline
    result-card.js        # Search result card HTML generator
    lead-card.js          # Lead card HTML generator
    studio-card.js        # AI Studio result card generator
  api.js                 # All fetch/API calls (AppAPI namespace)
  app.js                 # Alpine.js dashboard() component + UI functions
```

### CSS Files
```
static/css/
  tokens.css              # Design tokens (CSS variables for theming)
  (Tailwind loaded via CDN — no local CSS build)
```

---

## 2. Page Map

### HTML Pages (Server-rendered)

| URL | Type | Auth Required? | What It Shows |
|-----|------|---------------|---------------|
| `/` | Redirect | No | Nginx redirects to `/dashboard`. Without nginx, returns JSON API info (no `frontend/dist/index.html`) |
| `/dashboard` | HTML page | Client-side | Main SPA dashboard with all tabs |
| `/admin` | HTML page | Client-side (superadmin) | Superadmin management panel |
| `/pricing` | HTML page | No | Plans & pricing (Free, Starter, Pro, Enterprise) |
| `/health` | JSON | No | Health check (DB, Redis, GPU status) |
| `/docs` | Swagger UI | Debug only | OpenAPI interactive docs |
| `/redoc` | ReDoc UI | Debug only | Alternative API docs |

### API Endpoints

#### Authentication (`/api/v1/auth/`)
| Method | Path | Auth? | Description |
|--------|------|-------|-------------|
| POST | `/api/v1/auth/register` | No | Register new user + organization |
| POST | `/api/v1/auth/login` | No | Login, returns JWT token pair |
| POST | `/api/v1/auth/refresh` | No | Refresh access token |
| POST | `/api/v1/auth/change-password` | Yes | Change password |
| GET | `/api/v1/auth/me` | Yes | Get current user profile |

#### Search
| Method | Path | Auth? | Description |
|--------|------|-------|-------------|
| POST | `/api/search` | Yes | Enhanced text search with auto class suggestion |
| POST | `/api/search-by-image` | Rate-limited | Image similarity search (CLIP + DINOv2) |
| GET | `/api/v1/search/quick` | Yes | Quick DB-only text search |
| GET | `/api/v1/search/intelligent` | Yes | Live/agentic search (PRO) |
| POST | `/api/v1/search/intelligent` | Yes | Live search with image upload |
| POST | `/api/v1/search/search` | Yes | Agentic combined search |
| POST | `/api/suggest-classes` | No | AI Nice class suggestion |
| GET | `/api/nice-classes` | No | List all 45 Nice classes |
| POST | `/api/validate-classes` | No | Validate Nice class input |
| GET | `/api/v1/search/status` | Yes | Search service status |
| GET | `/api/v1/search/credits` | Yes | Search credits remaining |

#### Dashboard & Usage
| Method | Path | Auth? | Description |
|--------|------|-------|-------------|
| GET | `/api/v1/dashboard/stats` | Yes | KPI stats (watchlist, risks, deadlines) |
| GET | `/api/v1/usage/summary` | Yes | Plan usage summary (credits, limits) |

#### Watchlist
| Method | Path | Auth? | Description |
|--------|------|-------|-------------|
| GET | `/api/v1/watchlist` | Yes | List watchlist items |
| POST | `/api/v1/watchlist` | Yes | Add item to watchlist |
| POST | `/api/v1/watchlist/bulk` | Yes | Bulk import watchlist |
| POST | `/api/v1/watchlist/upload` | Yes | Upload Excel/CSV watchlist |
| POST | `/api/v1/watchlist/scan-all` | Yes | Trigger scan of all items |
| GET | `/api/v1/watchlist/scan-status` | Yes | Get scan progress |
| GET/PUT/DELETE | `/api/v1/watchlist/{item_id}` | Yes | CRUD single item |
| POST | `/api/v1/watchlist/{item_id}/scan` | Yes | Scan single item |
| POST | `/api/v1/watchlist/{item_id}/logo` | Yes | Upload logo for item |
| POST | `/api/v1/watchlist/rescan` | Yes | Rescan all items |

#### Alerts
| Method | Path | Auth? | Description |
|--------|------|-------|-------------|
| GET | `/api/v1/alerts` | Yes | List alerts (paginated) |
| GET | `/api/v1/alerts/summary` | Yes | Severity breakdown |
| GET | `/api/v1/alerts/{id}` | Yes | Alert detail |
| POST | `/api/v1/alerts/{id}/acknowledge` | Yes | Mark alert acknowledged |
| POST | `/api/v1/alerts/{id}/resolve` | Yes | Mark alert resolved |
| POST | `/api/v1/alerts/{id}/dismiss` | Yes | Dismiss alert |

#### Opposition Radar / Leads (`/api/v1/leads/`)
| Method | Path | Auth? | Description |
|--------|------|-------|-------------|
| GET | `/api/v1/leads/feed` | Yes | Lead feed (paginated) |
| GET | `/api/v1/leads/stats` | Yes | Lead statistics |
| GET | `/api/v1/leads/credits` | Yes | Lead view credits |
| GET | `/api/v1/leads/export/csv` | Yes | CSV export (Enterprise) |
| GET | `/api/v1/leads/{id}` | Yes | Lead detail |
| POST | `/api/v1/leads/{id}/contact` | Yes | Mark lead contacted |
| POST | `/api/v1/leads/{id}/convert` | Yes | Mark lead converted |
| POST | `/api/v1/leads/{id}/dismiss` | Yes | Dismiss lead |

#### Reports (`/api/v1/reports/`)
| Method | Path | Auth? | Description |
|--------|------|-------|-------------|
| POST | `/api/v1/reports/generate` | Yes | Generate new report |
| GET | `/api/v1/reports` | Yes | List reports |
| GET | `/api/v1/reports/{id}` | Yes | Report detail |
| GET | `/api/v1/reports/{id}/download` | Yes | Download report file |

#### Creative Suite (AI Studio)
| Method | Path | Auth? | Description |
|--------|------|-------|-------------|
| POST | `/api/creative/suggest-names` | Yes | AI name generation (Gemini) |
| POST | `/api/creative/generate-logo` | Yes | AI logo generation (Gemini) |
| GET | `/api/creative/generated-image/{id}` | Yes | Serve generated image |
| GET | `/api/creative/generation-history` | Yes | Generation history |
| GET | `/api/creative/status` | Yes | Creative suite availability |

#### Entity Lookup
| Method | Path | Auth? | Description |
|--------|------|-------|-------------|
| GET | `/api/v1/holders/{tpe_id}/trademarks` | Yes | Holder's trademark portfolio |
| GET | `/api/v1/holders/search` | Yes | Search holders |
| GET | `/api/v1/attorneys/{no}/trademarks` | Yes | Attorney's trademark portfolio |
| GET | `/api/v1/attorneys/search` | Yes | Search attorneys |

#### Admin (Superadmin only, `/api/v1/admin/`)
| Method | Path | Auth? | Description |
|--------|------|-------|-------------|
| GET | `/api/v1/admin/overview` | Superadmin | System overview stats |
| GET/PUT | `/api/v1/admin/settings` | Superadmin | App settings CRUD |
| GET | `/api/v1/admin/organizations` | Superadmin | List all orgs |
| PUT | `/api/v1/admin/organizations/{id}/plan` | Superadmin | Change org plan |
| GET | `/api/v1/admin/users` | Superadmin | List all users |
| GET | `/api/v1/admin/audit-log` | Superadmin | Audit log |
| GET/PUT | `/api/v1/admin/organizations/{id}/credits` | Superadmin | Credit management |
| CRUD | `/api/v1/admin/discount-codes` | Superadmin | Discount code management |
| GET/PUT | `/api/v1/admin/plans` | Superadmin | Plan pricing management |
| GET | `/api/v1/admin/analytics/usage` | Superadmin | Usage analytics |
| GET | `/api/v1/admin/analytics/export` | Superadmin | Export analytics |

#### Pipeline Management
| Method | Path | Auth? | Description |
|--------|------|-------|-------------|
| POST | `/api/pipeline/trigger` | Admin/Owner | Trigger data pipeline |
| POST | `/api/pipeline/trigger-step` | Admin/Owner | Trigger single step |
| GET | `/api/pipeline/status` | Admin/Owner | Pipeline status |
| GET | `/api/pipeline/runs/{id}` | Admin/Owner | Run details |

#### Other
| Method | Path | Auth? | Description |
|--------|------|-------|-------------|
| GET | `/api/v1/config` | No | Frontend config (risk thresholds) |
| GET | `/api/v1/status` | No | System stats (trademark count, etc.) |
| GET | `/api/trademark-image/{path}` | No | Serve trademark logo images |
| POST | `/api/v1/billing/validate-discount` | Yes | Validate discount code |

---

## 3. User Journey: First Visit (Unauthenticated)

### Step 1: Hit `www.ipwatchai.com`
- Request reaches **Cloudflare Tunnel** → **Nginx** container
- Nginx has `location = / { return 302 /dashboard; }` → **redirects to `/dashboard`**

### Step 2: `/dashboard` loads
- FastAPI serves `dashboard.html` via Jinja2 `TemplateResponse`
- **No server-side auth check** — the page always renders
- Auth is enforced **client-side** by JavaScript

### Step 3: What the user sees
- The full dashboard HTML loads with:
  - **Navbar** (IP Watch AI logo, dark mode toggle, language switcher)
  - **Search panel** (search input, class filter, status filter, attorney filter, search buttons)
  - **Tab bar** (Overview, Opposition Radar [PRO], AI Studio [NEW], Reports)
  - **Overview tab content** (KPI cards, alerts list, deadlines widget, watchlist)
- However, all API calls fail with **401 Unauthorized** because no token exists
- The UI shows empty states: "0" for all KPIs, "No threats detected", "Loading..." for watchlist

### Step 4: Auth modal / Login prompt
- **There is NO separate login page or login modal in the current templates**
- The `auth.js` script calls `fetchUserPlan()` on load, which hits `/api/v1/auth/me`
- On 401, it silently fails — **no redirect to login**
- **The login/register flow must happen outside the dashboard** (API-only)
- **No visible login form exists in the HTML templates**

### Critical Finding: Missing Login UI
The application has login/register API endpoints but **no login page or form** in the templates. Authentication is API-based only:
- Login: `POST /api/v1/auth/login` with `{"email": "...", "password": "..."}`
- The token must be stored in `localStorage` as `auth_token` manually
- The `/docs` Swagger UI can be used for login during development

---

## 4. User Journey: Registration

### Registration API
**Endpoint**: `POST /api/v1/auth/register`

**Required Fields**:
| Field | Type | Required | Validation |
|-------|------|----------|------------|
| `email` | EmailStr | Yes | Valid email format |
| `password` | string | Yes | Min 8 chars, uppercase, lowercase, digit required |
| `first_name` | string | Yes | 1-100 chars |
| `last_name` | string | Yes | 1-100 chars |
| `organization_name` | string | One of these | Creates new org, user becomes OWNER |
| `organization_slug` | string | required | Joins existing org, user becomes MEMBER |

### What happens on register:
1. Email uniqueness check (400 if exists)
2. Organization is created (if `organization_name`) or joined (if `organization_slug`)
3. User record created with hashed password
4. **No email verification** — account is immediately active
5. JWT token pair returned (`access_token` + `refresh_token`)
6. Default plan: **free** (set at organization level)

### Post-registration:
- User gets `free` plan with limits defined in `PLAN_FEATURES`
- No onboarding flow exists
- User must navigate to `/dashboard` and use the token

### Missing UI:
- **No registration form** in any HTML template
- Registration is API-only

---

## 5. User Journey: Login

### Login API
**Endpoint**: `POST /api/v1/auth/login`

**Request**: `{"email": "...", "password": "..."}`

**Response**:
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer"
}
```

### Auth Mechanism
- **JWT-based** authentication (not session/cookie)
- Access tokens have an expiry (configured in settings)
- Refresh tokens allow renewal via `POST /api/v1/auth/refresh`

### Token Storage (Frontend)
- Stored in `localStorage` under key `auth_token`
- Fallback check in `sessionStorage`
- Retrieved via `window.AppAuth.getAuthToken()`
- Sent as `Authorization: Bearer <token>` header on all API calls

### After Login
- `auth.js` auto-calls `fetchUserPlan()` which hits `/api/v1/auth/me`
- Returns user profile including: email, first_name, role, organization.plan, is_superadmin
- Sets global variables: `currentUserPlan`, `currentUserRole`, `currentUserName`
- Calls `loadPortfolio()` to fetch watchlist
- Calls `fetchUsageSummary()` to show credit badges

### Token Expiry
- On 401 response, `api.js` shows toast: "Session expired"
- **No automatic redirect to login** (there's no login page to redirect to)
- User must re-authenticate via API

### Missing UI:
- **No login form** in any HTML template
- No "remember me" checkbox
- No "forgot password" flow (there is a change-password endpoint but no reset flow)

---

## 6. User Journey: Dashboard

After authentication (token in localStorage), the dashboard fully functions:

### 6.1 Overview Tab (default)

**Visible sections:**

#### KPI Cards (4 cards across top)
- **Portfolio Size** — count of watched brands (`/api/v1/dashboard/stats`)
- **Critical Risks** — count of high-risk alerts
- **Active Deadlines** — opposition deadlines requiring action
- **7-Day Activity** — recent alert activity count

#### Plan Usage Row (3-6 cards)
- Quick Search credits (used/limit with progress bar)
- Live Search credits (used/limit)
- Watchlist usage (used/limit)
- AI Name Generation credits (if plan supports)
- AI Logo credits (if plan supports)
- System stats (total trademarks in DB, next scan time, plan badge)

#### Pre-publication Warning Banner
- Shows if any alerts are in `pre_publication` status

#### Risk Distribution Chart (Chart.js)
- Doughnut/bar chart showing alert severity breakdown

#### Recent Alerts List
- Shows latest 10 alerts from `/api/v1/alerts?page=1&page_size=10`
- Each alert card shows:
  - Risk score badge (color-coded: critical/high/medium/low)
  - Conflicting brand name + logo thumbnail
  - Score breakdown badges (text, visual, translation similarity)
  - Deadline status badge (days remaining)
  - "Analyze" button → opens **Alert Detail Modal**

#### Deadlines Widget (right column)
- Sorted by urgency (days remaining)
- Red border for <10 days, orange for others
- "Opposition Filing" button → opens **Opposition Modal**

#### Watchlist / Portfolio (right column)
- List of watched brands from `/api/v1/watchlist`
- Shows brand name, logo, application number
- "Quick Add" functionality from search results

#### Pipeline Status (admin/owner only)
- 5-step progress: Download → Extract → Metadata → Embeddings → Ingest
- Run/skip buttons for pipeline management

**API calls powering Overview tab:**
- `GET /api/v1/dashboard/stats`
- `GET /api/v1/alerts?page=1&page_size=10`
- `GET /api/v1/alerts/summary`
- `GET /api/v1/usage/summary`
- `GET /api/v1/watchlist`

---

### 6.2 Search (always visible at top, across all tabs)

**Search bar components:**
1. **Text input** — trademark name query
2. **Nice Class multi-select** — filter by Nice classes (1-45)
3. **Status filter** — dropdown: All/Published/Registered/Applied/Renewed/Opposed/Withdrawn/Refused
4. **Attorney filter** — autocomplete input with attorney number/name
5. **Search button** — triggers Quick Search (DB-only)
6. **Live Search button** — triggers Agentic Search (PRO, scrapes TurkPatent live)
7. **Class Finder** — collapsible AI-powered class suggestion tool
8. **Logo Upload** — drag-and-drop zone for image search

**Search Types:**
| Type | Button | Endpoint | Description |
|------|--------|----------|-------------|
| Quick | "Ara" (Search) | `GET /api/v1/search/quick` | DB-only, fast, uses daily credits |
| Live (PRO) | "Canli Arama" | `GET/POST /api/v1/search/intelligent` | Scrapes TurkPatent portal, monthly credits |
| Image | Upload logo | `POST /api/search-by-image` | CLIP + DINOv2 visual similarity |
| Combined | Text + image | `POST /api/search-by-image` with name | Text + visual combined scoring |

**Search Results:**
- Rendered by `result-card.js` below the search panel
- Each result shows: logo thumbnail, brand name, application number, status, Nice classes, similarity %, risk level badge
- Actions per result:
  - Click logo → **Lightbox Modal**
  - Click holder name → **Entity Portfolio Modal** (holder's full trademark list)
  - Click attorney name → **Entity Portfolio Modal** (attorney's clients)
  - "Add to Watchlist" → **Quick Add Watchlist Modal**
  - "Opposition" button → **Opposition Filing Modal**

---

### 6.3 Opposition Radar Tab (PRO)

**Access**: Tab labeled "Opposition Radar" with PRO badge

**Content:**
- **Header**: Title + daily credits display
- **5 stat cards**: Critical (≤7 days), Urgent (≤14 days), Total Leads, Converted, Upcoming
- **Workflow stage segments** bar
- **Lead feed**: Paginated list of potential opposition opportunities
- Each lead card (`lead-card.js`) shows:
  - Conflicting trademark info (name, logo, application number)
  - Risk score + similarity breakdown
  - Deadline urgency
  - Opposition timeline (`opposition-timeline.js`)
  - Actions: Contact, Convert, Dismiss
- **CSV Export** button (Enterprise only)

**API calls:**
- `GET /api/v1/leads/feed`
- `GET /api/v1/leads/stats`
- `GET /api/v1/leads/credits`
- `GET /api/v1/leads/{id}`
- `POST /api/v1/leads/{id}/contact|convert|dismiss`

---

### 6.4 AI Studio Tab (NEW / BETA)

**Access**: Tab with "NEW" badge

**Two sub-modes** (toggled via buttons):

#### Name Lab
- Input fields:
  - Brand concept/name (required)
  - Nice class (optional, multi-select)
  - Industry/sector description
  - Style: Modern/Classic/Playful/Technical
- "Generate Safe Alternative" button
- Results: AI-generated brand name suggestions (`studio-card.js`)
  - Each suggestion shows: name, availability risk, reasoning
  - Option to search any suggestion in the DB

**API**: `POST /api/creative/suggest-names`

#### Logo Studio
- Input for brand name + style parameters
- AI-generated logo concepts (Gemini-powered)
- Results show generated images with download option

**API**: `POST /api/creative/generate-logo`

**Credits**: Separate credit pools for name generation and logo generation

---

### 6.5 Reports Tab

**Content:**
- Header with remaining report credits
- "Generate Report" button → **Report Generation Modal**
- Report types:
  - Weekly Summary (`watchlist_summary`)
  - Monthly Summary (`alert_digest`)
  - Portfolio Status (`portfolio_status`)
  - Single Brand Report (`risk_assessment`)
  - Full Portfolio Report (`competitor_analysis`)
- Formats: PDF, Excel
- Date range selector
- Reports list with download links
- Upgrade prompt for ineligible plans

**API calls:**
- `POST /api/v1/reports/generate`
- `GET /api/v1/reports`
- `GET /api/v1/reports/{id}/download`

---

### 6.6 Settings/Profile

**No dedicated settings tab exists in the dashboard.**

Profile management is available via:
- `GET /api/v1/auth/me` — view profile
- `PUT /api/v1/user/profile` — update profile (name, phone, title, department, linkedin, avatar)
- `POST /api/v1/auth/change-password` — change password
- Organization settings via API: risk threshold, email notifications, weekly report

**No visible UI for profile/settings in the current dashboard templates.**

---

### 6.7 Admin Panel (`/admin`)

**Separate standalone page** — only visible to superadmin users.

**Sidebar navigation with tabs:**
- Overview — system stats (users, orgs, trademarks, alerts)
- Organizations — list, edit plan, toggle status
- Users — list, change roles, toggle superadmin
- Settings — app_settings key/value management
- Discount Codes — CRUD for promo codes
- Plans & Pricing — edit plan pricing and limits
- Credits — bulk credit allocation
- Audit Log — activity tracking
- Usage Analytics — usage charts and export

---

## 7. Navigation Flow Diagram

```
www.ipwatchai.com
    │
    ├── / ─────────────────── 302 → /dashboard
    │
    ├── /dashboard ──────────── Main SPA (no server auth gate)
    │   │
    │   ├── [Search Bar] ──── Always visible
    │   │   ├── Quick Search → Results inline
    │   │   ├── Live Search (PRO) → Agentic Loading Modal → Results
    │   │   └── Image Upload → Image Search → Results
    │   │
    │   ├── [Overview Tab] ── Default
    │   │   ├── KPI Cards
    │   │   ├── Plan Usage Credits
    │   │   ├── Risk Chart
    │   │   ├── Recent Alerts ─── Click → Alert Detail Modal
    │   │   │                         ├── Acknowledge
    │   │   │                         ├── Resolve
    │   │   │                         └── Dismiss
    │   │   ├── Deadlines Widget ── Click → Opposition Filing Modal
    │   │   │                           ├── Link to TurkPatent portal
    │   │   │                           └── Email to lawyer
    │   │   └── Watchlist ─────── Quick-add from search results
    │   │
    │   ├── [Opposition Radar] ── PRO only
    │   │   ├── Lead Stats (5 cards)
    │   │   ├── Lead Feed (paginated)
    │   │   └── Lead Actions → Contact / Convert / Dismiss
    │   │
    │   ├── [AI Studio] ──────── BETA
    │   │   ├── Name Lab → Generate Names → View Suggestions
    │   │   └── Logo Studio → Generate Logo → Download
    │   │
    │   └── [Reports] ──────────
    │       ├── Generate Report Modal (type, title, format, dates)
    │       ├── Reports List
    │       └── Download (PDF/Excel)
    │
    ├── /admin ──────────────── Superadmin Panel
    │   ├── Overview / Organizations / Users / Settings
    │   ├── Discount Codes / Plans & Pricing
    │   └── Credits / Audit Log / Analytics
    │
    ├── /pricing ────────────── Plans page (4 tiers)
    │
    └── /health ─────────────── Health check endpoint

Modals (overlay on /dashboard):
    ├── Alert Detail Modal
    ├── Opposition Filing Modal
    ├── Lead Detail Modal
    ├── Agentic Search Loading Modal (terminal animation)
    ├── Upgrade Modal (shown on 403)
    ├── Credits Exhausted Modal (shown on 402)
    ├── Quick Add Watchlist Modal
    ├── Trademark Image Lightbox
    ├── Entity Portfolio Modal (holder/attorney trademarks)
    └── Report Generation Modal

Mobile Navigation:
    ├── Hamburger → Slide-over drawer (left side)
    │   ├── Overview / Opposition Radar / AI Studio / Reports
    │   └── Refresh / Admin (if superadmin)
    └── Bottom Tab Bar (4 buttons)
        ├── Search (scrolls to search input)
        ├── Radar (Opposition Radar tab)
        ├── Watchlist (scrolls to watchlist section)
        └── Alerts (scrolls to alerts section, badge for count)
```

---

## 8. Error Handling

### HTTP Error Responses
| Status | Handling |
|--------|----------|
| 401 | `api.js` shows toast "Session expired" — no redirect |
| 402 | Credits Exhausted Modal opens (with sales contact + pricing links) |
| 403 | Upgrade Modal opens (shows Professional Plan at ₺999/mo) |
| 404 | FastAPI returns JSON `{"detail": "Not found"}` |
| 429 | Rate limit toast warning with limit details |
| 500 | Global exception handler returns JSON error (debug details in dev mode) |

### Frontend Error Patterns
- `try/catch` around all `fetch()` calls in `api.js`
- Errors displayed via **toast notifications** (`showToast()`)
- Toast types: `success` (green), `error` (red), `warning` (amber), `info` (blue)
- Loading states: `animate-spin` spinner, skeleton placeholders, "Yukleniyor..." text

### Missing Error Pages
- No custom 404 HTML page
- No custom 500 HTML page
- No offline fallback page (despite PWA registration)

---

## 9. Missing Pages / Broken Flows

### Critical Missing: Login & Registration UI
1. **No login form/page exists** — The dashboard loads for everyone, but all API calls fail without a token. There is no way for a user to log in through the UI.
2. **No registration form/page exists** — Registration is API-only.
3. **No password reset/forgot password flow** — Neither UI nor API endpoint for it.

### Missing Features:
4. **No user profile/settings page** — API endpoints exist but no UI to edit profile, change password, or manage organization settings.
5. **No logout button** — There is no logout button anywhere in the navbar or drawer. No way to clear the token through UI.
6. **No onboarding flow** — After registration, user sees the same dashboard with no guidance.

### Incomplete Flows:
7. **Root URL without nginx** — Returns JSON `{"name": "...", "status": "running"}` instead of redirecting. Only nginx adds the redirect.
8. **Pricing page** — Has plan cards with "Contact Sales" but no actual upgrade/checkout flow. The "Yukselt" (Upgrade) button in the Upgrade Modal calls `redirectToUpgrade()` which likely navigates to `/pricing`.
9. **Admin link visibility** — Uses `x-if="window.AppAuth && window.AppAuth.currentUserIsSuperadmin"` which won't render until `fetchUserPlan()` completes — may flash or never appear if the API call fails.

### Potential Issues:
10. **Service Worker** registered at `/static/sw.js` — path may not correctly intercept `/dashboard` requests.
11. **Alert count badge** in mobile bottom bar is always `hidden` — only updated by JS.

---

## 10. Responsive & i18n Status

### Mobile Responsiveness

**Rating: Good**

- **Viewport**: `<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">`
- **Tailwind responsive classes**: Extensively used (`sm:`, `md:`, `lg:` breakpoints)
- **Mobile adaptations**:
  - Desktop tab bar hidden on mobile (`desktop-tabs { display: none !important }` at `max-width: 767px`)
  - Mobile bottom tab bar (4 buttons: Search, Radar, Watchlist, Alerts)
  - Hamburger menu → slide-over drawer for navigation
  - Full-screen modals on mobile (`modal-mobile-fullscreen`)
  - Body padding-bottom for bottom bar clearance
  - Min touch targets: `min-h-[44px] min-w-[44px]` on all buttons
  - Skip link for accessibility

### Dark Mode

**Status: Implemented**

- Toggle button in navbar
- Uses CSS class `dark` on `<html>` element
- Persisted in `localStorage` as `theme`
- Respects `prefers-color-scheme: dark` system preference
- CSS variables in `tokens.css` provide all colors

### Internationalization (i18n)

**Status: Implemented — 3 languages**

| Language | File | Direction |
|----------|------|-----------|
| Turkish (tr) | `static/locales/tr.json` | LTR |
| English (en) | `static/locales/en.json` | LTR |
| Arabic (ar) | `static/locales/ar.json` | RTL |

**Implementation:**
- `static/js/utils/i18n.js` — locale loading system
- `window.AppI18n.t(key, params)` — translation function with interpolation
- Language switcher in navbar (dropdown with TR/EN/AR options)
- Locale stored in localStorage
- Alpine.js reactive: `x-text="t('key')"` re-renders on locale change
- `locale-changed` custom event triggers full re-render
- RTL support via CSS rules for Arabic (direction, margins, paddings, borders)

**Hardcoded text found:**
- Some Turkish text in pricing.html (`Planlar ve Fiyatlandirma`, `Dashboard'a Don`)
- Admin panel is English-only (no i18n)
- Some error messages in Python are Turkish (`Gecersiz dosya turu`, `Dosya cok buyuk`)
- Nice class names hardcoded in both Turkish and English in `main.py`

### PWA Support

- `manifest.json` — app installable
- `sw.js` — service worker registered
- Apple touch icon configured
- Theme color: `#4f46e5` (indigo)

---

## Summary of Key Architecture Decisions

1. **Single-page feel, server-rendered**: One Jinja2 template (`dashboard.html`) acts as an SPA shell. Alpine.js handles all interactivity. Navigation between "pages" is tab-switching (showing/hiding `<div>` sections), not URL-based routing.

2. **No client-side router**: No `history.pushState`, no hash routing. All navigation is in-page tab switching via `showDashboardTab()`. Only `/dashboard`, `/admin`, `/pricing` are separate HTML pages.

3. **Auth is API-based, UI is missing**: JWT tokens stored in localStorage, but no login/register/logout UI exists in the templates. This is the most significant gap for end-user experience.

4. **Multi-tenant**: Organizations contain users. Plans (free/starter/pro/enterprise) are set per-organization. Credits tracked per-user.

5. **Two search engines**: Quick (DB-only, fast) and Intelligent/Live (scrapes TurkPatent portal, slow but comprehensive). Both route through unified scoring via `score_pair()`.

6. **Nginx as entry point**: In production, Cloudflare Tunnel → Nginx → FastAPI. Nginx handles rate limiting, CORS headers, and the `/` → `/dashboard` redirect.
