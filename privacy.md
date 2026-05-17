# Privacy Policy — Sethko Coaching

_Last updated: May 17, 2026_

Sethko Coaching ("the app") is a personal fitness coaching application currently in private development. This policy describes what data the app collects, how it is used, and how it is stored.

## Who this policy applies to

The app is currently operated for a small number of named users known personally to the developer. It is not available to the general public and is not listed on the App Store. If and when the app is made publicly available, this policy will be updated and users will be notified.

## What data the app collects

The app collects and processes the following categories of data, all of which the user explicitly authorizes:

**From the Whoop Developer API** (with user consent via OAuth):
- Recovery score, heart rate variability (HRV), and resting heart rate
- Sleep duration, stages, and quality scores
- Daily strain scores and workout sessions
- Basic profile information (name, email)

**From Apple HealthKit** (with user consent granted on first launch):
- Body weight readings from connected Bluetooth scales
- Workout sessions and their metadata (type, duration, heart rate, calories)
- HRV readings (when not available from Whoop)

**Entered directly by the user**:
- Food log entries (food name, portion size, macronutrients)
- Dietary modality and goal preferences

## How the data is used

The data is used exclusively to generate personalized coaching feedback for the user via the Anthropic Claude API. Specifically:

- A daily summary of the user's data is sent to the Claude API to generate a coaching brief
- Coaching outputs are stored alongside the user's data so the user can review previous days
- Trends across body weight, nutrition, and recovery are calculated locally to surface patterns to the user

The data is **not** used for advertising, sold to third parties, or shared with anyone other than the API providers listed below.

## Third-party services

The app relies on the following third-party services, each of which receives a portion of user data necessary to provide their service:

- **Whoop, Inc.** — source of recovery, sleep, and strain data. See [Whoop's privacy policy](https://www.whoop.com/privacy).
- **Apple Inc. (HealthKit)** — data stays on the user's device and is read by the app with permission. See [Apple's privacy policy](https://www.apple.com/legal/privacy/).
- **Anthropic PBC (Claude API)** — receives daily data summaries to generate coaching output. See [Anthropic's privacy policy](https://www.anthropic.com/legal/privacy).
- **Backend hosting provider** (currently Railway or Render) — stores user data in a private PostgreSQL database.

## Where data is stored

User data is stored in a private PostgreSQL database operated by the developer. Access is restricted to the developer. Data is not encrypted at rest beyond the database provider's default encryption.

Whoop access and refresh tokens are stored securely and used only to pull data on the user's behalf.

## How long data is retained

Data is retained for as long as the user remains an active user of the app. Users may request deletion of their data at any time by contacting the developer.

## Data the app does NOT collect

- The app does not access photos, contacts, calendar, location, or any other device data outside of HealthKit.
- The app does not use analytics, advertising SDKs, or any third-party tracking.
- The app does not collect financial or payment information.

## User rights

Users have the right to:
- Request a copy of all data the app holds about them
- Request correction of any inaccurate data
- Request deletion of all their data and revocation of API access
- Revoke the app's access to their Whoop account at any time via Whoop's settings
- Revoke the app's HealthKit permissions at any time via iOS Settings

## Children

The app is not directed at children under 16 and does not knowingly collect data from them.

## Changes to this policy

This policy may be updated as the app evolves. Material changes will be communicated to users directly. The "Last updated" date at the top of this document indicates the most recent revision.

## Contact

For questions about this policy or to exercise any of the rights listed above, contact the developer at: [your email here]
