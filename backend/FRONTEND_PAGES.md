# Frontend Pages

## Page Map

```
┌─────────────────────────────────────────────────────────┐
│                     LANDING PAGE                         │
│              /                                           │
│   Hero banner + Login buttons (Admin / Guest)            │
└────────────┬──────────────────────┬──────────────────────┘
             │                      │
     ┌───────▼───────┐      ┌───────▼───────┐
     │  ADMIN LOGIN  │      │  GUEST LOGIN  │
     │  /login/admin │      │  /login/guest │
     └───────┬───────┘      └───────┬───────┘
             │                      │
             │               ┌──────▼──────┐
             │               │  REGISTER   │
             │               │  /register  │
             │               └──────┬──────┘
             │                      │
     ┌───────▼──────────┐   ┌───────▼──────────┐
     │ ADMIN DASHBOARD  │   │ GUEST DASHBOARD   │
     │ /admin           │   │ /guest            │
     └──┬──┬──┬──┬──────┘   └──┬──┬──┬──┬──────┘
        │  │  │  │              │  │  │  │
        ▼  ▼  ▼  ▼              ▼  ▼  ▼  ▼
     (sub-pages below)       (sub-pages below)
```

---

## All Pages

### Public Pages (No Auth)

| #  | Route             | Page Name          | Description                                           |
|----|-------------------|--------------------|-------------------------------------------------------|
| 1  | `/`               | Landing Page       | Hero section, feature highlights, Login/Register CTAs  |
| 2  | `/login/admin`    | Admin Login        | Username + password form for admin                     |
| 3  | `/login/guest`    | Guest Login        | Username + password form for guest                     |
| 4  | `/register`       | Guest Registration | Registration form + face capture step                  |

---

### Guest Pages (Auth: guest role)

| #  | Route                    | Page Name              | Description                                                        |
|----|--------------------------|------------------------|--------------------------------------------------------------------|
| 5  | `/guest`                 | Guest Dashboard        | Overview: lounge status, tokens, flight info                       |
| 6  | `/guest/face-register`   | Face Registration      | Camera feed for face data capture (uses register_face logic)       |
| 7  | `/guest/lounge-register` | Lounge Registration    | Select lounge, enter details, proceed to payment                   |
| 8  | `/guest/credit-check`    | Credit Card Check      | Enter card details, check if eligible for complimentary access     |
| 9  | `/guest/payment`         | Payment Page           | Payment form, amount display, confirm/cancel                       |
| 10 | `/guest/confirmation`    | Payment Confirmation   | Success/failure screen, access pass display                        |
| 11 | `/guest/lounge-access`   | Lounge Access Status   | Current access status, QR code (optional), entry time              |
| 12 | `/guest/dining`          | Dining Tokens          | View tokens, redeemed/remaining, token QR codes                    |
| 13 | `/guest/boarding`        | Boarding Pass          | Flight info, departure time, gate info                             |
| 14 | `/guest/history`         | Visit History          | Past lounge visits with entry/exit times and durations             |

---

### Admin Pages (Auth: admin role)

| #  | Route                    | Page Name              | Description                                                        |
|----|--------------------------|------------------------|--------------------------------------------------------------------|
| 15 | `/admin`                 | Admin Dashboard        | Live overview: occupancy, agent mode, recent events                |
| 16 | `/admin/camera`          | Live Camera Feed       | Real-time camera view with agent overlays (faces, boxes, labels)   |
| 17 | `/admin/modes`           | Mode Control           | Switch agent modes (Gate/Reception/Entry/Exit), see live status    |
| 18 | `/admin/attendance`      | Attendance Management  | Today's attendance table, search, export CSV                       |
| 19 | `/admin/analytics`       | Analytics Dashboard    | Charts: hourly traffic, avg stay, peak hours, weekly trends        |
| 20 | `/admin/guests`          | Guest Management       | List all registered guests, view/edit profiles, delete face data   |
| 21 | `/admin/permits`         | Access Permits         | Grant/revoke lounge access, manage registrations                   |
| 22 | `/admin/dining`          | Dining Token Mgmt      | Issue tokens, view redeemed, stats per guest                       |
| 23 | `/admin/events`          | Agent Event Log        | Scrollable log of all agent events (access granted/denied/alerts)  |
| 24 | `/admin/settings`        | System Settings        | Camera config, detection thresholds, mode defaults                 |

---

## Page Details

### Page 1 — Landing Page (`/`)
**Components:**
- Hero section with airport lounge imagery
- "Premium Lounge Access — Hands-Free Verification" headline
- Three feature cards: Face Recognition, Instant Access, Dining Tokens
- Two CTA buttons: "Admin Login" → `/login/admin`, "Guest Login" → `/login/guest`
- "New Guest? Register" link → `/register`

**API calls:** None

---

### Page 4 — Guest Registration (`/register`)
**Components:**
- Step 1: Personal info form (name, email, phone, boarding pass)
- Step 2: Camera feed for face capture (progress bar, guidance text)
- Step 3: Confirmation screen

**API calls:**
- `POST /api/register_guest` (step 1)
- `POST /api/register_guest/face` (step 2)

---

### Page 5 — Guest Dashboard (`/guest`)
**Components:**
- Welcome header with guest name
- Status cards: Lounge Access (active/inactive), Dining Tokens (remaining), Flight Info
- Quick actions: "Register for Lounge", "View Tokens", "Check Boarding Pass"
- Recent activity feed

**API calls:**
- `GET /api/guest_dashboard`

---

### Page 7 — Lounge Registration (`/guest/lounge-register`)
**Components:**
- Lounge selection (name, amenities, pricing)
- Guest details summary
- "Check Credit Card Eligibility" button → redirect to `/guest/credit-check`
- "Proceed to Payment" button → redirect to `/guest/payment`

**API calls:**
- `POST /api/register_lounge`

---

### Page 8 — Credit Check (`/guest/credit-check`)
**Components:**
- Card details form (last 4 digits, card type, network)
- Result display: eligible (green) / not eligible (red)
- If eligible: "Claim Complimentary Access" button
- If not: "Proceed to Payment" button with amount

**API calls:**
- `POST /api/check_credit`

---

### Page 9 — Payment (`/guest/payment`)
**Components:**
- Amount display & breakdown
- Card payment form (simulated or Stripe integration placeholder)
- Confirm / Cancel buttons

**API calls:**
- `POST /api/payment_confirmation`

---

### Page 15 — Admin Dashboard (`/admin`)
**Components:**
- Live stats bar: Occupancy count, Today's attendance, Unknown alerts, Mode indicator
- Occupancy list (who's inside right now)
- Recent agent events feed (scrolling)
- Quick mode switching buttons
- Live camera thumbnail (links to `/admin/camera`)

**API calls:**
- `GET /api/admin_dashboard`
- `GET /api/agent/state` (polling every 2-3 sec)

---

### Page 16 — Live Camera Feed (`/admin/camera`)
**Components:**
- Full-screen camera feed with face detection overlays
- Agent mode indicator (top-left)
- Side panel: current faces, events, occupancy
- Mode switching buttons at bottom

**API calls:**
- Video stream: `GET /api/agent/video_feed` (MJPEG stream)
- `GET /api/agent/state` (polling)

---

### Page 17 — Mode Control (`/admin/modes`)
**Components:**
- Four mode cards: Gate, Reception, Entry, Exit — click to activate
- Current mode highlighted
- Entry/Exit log display
- Occupancy panel: who's in, who's out, durations

**API calls:**
- `POST /api/change_modes`
- `GET /api/agent/state`

---

### Page 19 — Analytics (`/admin/analytics`)
**Components:**
- Date picker (day/week/month)
- Charts: Hourly entry/exit traffic, Avg stay duration, Peak hours histogram
- KPI cards: Total guests, Repeat visitors, Unknown alerts, Tokens redeemed
- Attendance table with filters and CSV export

**API calls:**
- `GET /api/analytics?date=27-02-2026`
- `GET /api/analytics?range=week`

---

## Frontend Tech Stack (Recommended)

| Layer        | Technology        | Reason                              |
|--------------|-------------------|-------------------------------------|
| Framework    | React / Next.js   | Component-based, SSR support        |
| Styling      | Tailwind CSS      | Rapid UI development                |
| Charts       | Chart.js / Recharts | Analytics visualizations          |
| HTTP         | Axios             | API communication                   |
| Auth         | JWT (localStorage) | Stateless auth with Flask backend  |
| Camera       | MJPEG `<img>` tag | Simple video streaming from Flask   |
| State        | React Context     | Light global state management       |

---

## Data Flow: Guest Registration → Lounge Access

```
Guest visits /register
  │
  ├─► Fills form → POST /api/register_guest → account created
  │
  ├─► Camera opens → POST /api/register_guest/face → embeddings saved
  │
  ├─► Redirected to /guest (dashboard)
  │
  ├─► Clicks "Register for Lounge" → /guest/lounge-register
  │     │
  │     ├─► "Check Credit" → POST /api/check_credit
  │     │     ├─► Eligible → complimentary access granted
  │     │     └─► Not eligible → proceed to payment
  │     │
  │     └─► POST /api/register_lounge → registration created
  │
  ├─► Payment → POST /api/payment_confirmation → access granted
  │
  └─► Guest walks to gate → Agent auto-detects face → ACCESS GRANTED
```

---
