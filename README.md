# PerkFi — LANsolo prototype

A Python/Flask + SQLite implementation of the core PerkFi journeys, with responsive server-rendered HTML.

## Setting up

Open Terminal > New Terminal (PowerShell), and run each command separately:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m flask --app app init-db
```

The final command creates the database and prints two randomly generated passwords. Save them privately:

- operator@perkfi.local
- merchant@perkfi.local

Re-running init-db preserves the database and does not reset staff passwords. To change a staff password use the CLI command below:

```powershell
.\.venv\Scripts\python.exe -m flask --app app reset-staff
```

## 2. Run

```powershell
.\.venv\Scripts\python.exe app.py
```

Open http://127.0.0.1:5000. Keep the terminal running. Ctrl+C to stop the server. Records persist in `instance/perkfi.db`.

## 3. Test guest access

1. Open `/`.
2. Accept the access terms and select **Start free access session**.
3. `/home` shows the two-hour portal session and community information.
4. Browse Explore, notice details and transport.
5. Select End session and confirm the state changes.

## 4. Test membership and rewards

1. Select Sign in and enter an email or phone in international format.
2. The default `OTP_MODE=console` prints the code in the terminal.
3. Enter the six digits within 10 minutes. A new member gets **120 prototype welcome points**, once.
4. Open Rewards, review the 80-point Free topping reward, and confirm.
5. Check the balance is 40 and copy the voucher code.
6. View Account for the points ledger and voucher history.
7. After one minute in an active access session, water Sprout. Watering is capped at once per Malaysian calendar day.

## 5. Test merchant workflow

Use a separate browser profile/private window to keep the member session open.

1. Visit `/staff`; sign in as merchant@perkfi.local using the generated password.
2. Enter the member voucher code and select Check voucher.
3. Read the conditions, then Confirm redemption.
4. Try it again: the app must reject reuse.
5. Refresh the member's voucher page: it shows Redeemed.
6. In the member Account view, find Member ID.
7. At the merchant dashboard, award 10 points against a unique receipt reference. Refresh the account to see the credit.
8. Reuse the same receipt reference: it must not credit twice.
9. Create another reward with cost, quantity, validity and conditions; verify it appears in Rewards.

The prototype uses voucher-code entry. Camera scanning and QR generation have been implemented for prompt entry. Points for purchases are confirmed manually by staff; there is no POS integration.

## 6. Test operator workflow

1. Sign in at `/staff` as operator@perkfi.local.
2. Create a notice with a future Malaysia-time expiry; leave Publish unchecked to save a draft.
3. Open its edit page, publish it, and check the public notice board.
4. Unpublish to remove it from the public board. Saved drafts are visible only to operators.
5. Add an amenity/event listing and verify it under Explore.
6. Inspect the seven-day heatmap and hourly session counts, then export CSV.

Analytics come from this app's sessions, not physical WiFi telemetry. A browser that closes without End session remains active until timeout. Zero means no recorded session starts, not proof of zero visitors.

## 7. APIs and configuration

Edit `.env` and restart the server after changes. Default coordinates are a Kuala Lumpur reference point; **Taman Harmoni is fictional**. SITE_NAME, SITE_LAT and SITE_LON can be replaced with chosen test site. The default feed is Prasarana rapid-bus-kl, filtered to 15 km. TRANSIT_RADIUS_KM can be increased for wider coverage.

| Integration | Implementation | Credentials |
|---|---|---|
| Malaysian GTFS Static | Downloads and parses ZIP, calendar exceptions, routes, stops and stop times; shows upcoming scheduled departures for five nearby stops | None |
| Malaysian GTFS Realtime | Decodes protobuf vehicle observations; shows coordinates, route, age and distance | None |
| Open-Meteo | Current model temperature, humidity and weather code | None for the relevant non-commercial API use |
| Maps | Opens Google Maps search/directions links | None; this is an external link, not a maps API |
| Email verification | SMTP with STARTTLS | Your email provider's SMTP settings |
| SMS verification | Twilio Messages REST endpoint | Account SID, token and sending number |
| Notices, tourism, amenities, events | Database records maintained by operator, with optional source URL | No assumed government/tourism API |

API sources:

- https://developer.data.gov.my/realtime-api/gtfs-static.en
- https://developer.data.gov.my/realtime-api/gtfs-realtime.en
- https://developer.data.gov.my/rate-limit.en
- https://open-meteo.com/en/docs
- https://www.twilio.com/docs/messaging/api/message-resource

GTFS data attribution: data.gov.my and the selected agency, CC BY 4.0. Weather attribution: Open-Meteo, CC BY 4.0.

Server-side caching: static ZIP 24 hours, schedule output 5 minutes, vehicle positions 60 seconds, weather 10 minutes. Refresh buttons respect caches. Timeouts produce unavailable/stale states rather than simulated live data.

### Enable real contact delivery (for future implementation)

For email, set `OTP_MODE=live` plus SMTP_HOST, SMTP_PORT, SMTP_FROM, SMTP_USER and SMTP_PASSWORD in `.env`, then restart. Use the provider's supported SMTP credentials; an app password may be required.

For phone numbers, live mode requires TWILIO_SID, TWILIO_TOKEN and TWILIO_FROM. Provider account restrictions and charges apply. If credentials are absent or delivery fails, the app shows a delivery error.

## 8. Run tests

Stop the app or use a second terminal:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests use temporary databases. They cover key route rendering, role permissions, voucher ownership, idempotent claims, single-use redemption, expired rewards, CSRF, session reuse, draft visibility and API failure handling.

## 9. File map

- app.py: routes, session access, authentication, database transactions and staff actions.
- integrations.py: external API clients, caching, GTFS decoding and schedule filtering.
- templates/: visitor/operator/merchant HTML pages.
- static/style.css: responsive teal/navy visual system.
- static/app.js: external-source panels, safe DOM rendering and loading/error states.
- test_app.py: regression tests.
- .env.example: non-secret configuration template.
- instance/: generated local database and session secret; excluded from Git.

## 10. Git checkpoint

After the app runs and tests pass, initialise a local repository if this folder is not already tracked:

```powershell
git init
git add .
git status
git commit -m "Build PerkFi local portal prototype" # or any suitable message
```

Confirm .env, instance/ and .venv/ are excluded before committing. No remote repository is created by these commands.

## 11. Current scope and next iterations

Implemented in source: guest sessions, optional email/phone code flow, role-based staff login, notices/drafts/expiry, local directory, live-source adapters, merchant-specific ledger, transactional claims, voucher ownership checks, camera/QR flow, single-use redemption, staff-issued purchase points, capped Sprout growth, preferences, hourly reports, heatmap and CSV export.

Still to implement: complete Bahasa Melayu translation, automatic account-deletion and data-export flows, rich directory editing/deletion, reward editing and campaign reporting filters, sponsored placements, more gamification variations, full mobile/browser verification, production deployment and gateway integration.
