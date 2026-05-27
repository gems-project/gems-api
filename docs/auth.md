# Dashboard Authentication and Email Verification

The GEMS dashboard uses Auth0 through Azure App Service Authentication / Easy Auth.

## Production custom domains

| App | Custom domain | Azure Web App |
|-----|---------------|---------------|
| Dashboard | `https://gems.bovi-analytics.org` | `gems-dashboard` |
| API | `https://gems-api.bovi-analytics.org` | `GEMS-API` |

Azure **Custom domains** and TLS are already configured on both apps. DNS is managed outside this repo (your `bovi-analytics.org` team).

### Azure Portal (if you need to verify or re-add)

For each Web App (`gems-dashboard`, `GEMS-API`):

1. **Settings → Custom domains** — confirm `gems.bovi-analytics.org` / `gems-api.bovi-analytics.org` show **Verified** with a valid certificate.
2. **Settings → Environment variables** — on `gems-dashboard`, set:
   - `GEMS_API_BASE_URL` = `https://gems-api.bovi-analytics.org`
3. Optional: mark the custom hostname as the **default** so links and redirects prefer it over `*.azurewebsites.net`.

No API code change is required for the domain swap; the API has no hardcoded public URL.

### Auth0 Application (dashboard) — required manual updates

Open Auth0 → **Applications** → the dashboard app (**Client ID** `MHYamdXz76XbRnzxr3ZVLbvL48Sboej2`, tenant `dev-1bd3bttgj2px61zz.us.auth0.com`).

Paste the comma-separated values from `tools/auth0_dashboard_domains.txt` (or copy below). **Keep both** `gems.bovi-analytics.org` and the `azurewebsites.net` host during transition:

**Allowed Callback URLs**

```text
https://gems.bovi-analytics.org/.auth/login/auth0/callback,https://gems-dashboard-aed0h2fufpd8byf6.eastus-01.azurewebsites.net/.auth/login/auth0/callback
```

**Allowed Logout URLs**

```text
https://gems.bovi-analytics.org,https://gems.bovi-analytics.org/.auth/logout,https://gems.bovi-analytics.org/.auth/logout/complete,https://gems-dashboard-aed0h2fufpd8byf6.eastus-01.azurewebsites.net,https://gems-dashboard-aed0h2fufpd8byf6.eastus-01.azurewebsites.net/.auth/logout,https://gems-dashboard-aed0h2fufpd8byf6.eastus-01.azurewebsites.net/.auth/logout/complete
```

**Important:** Azure Easy Auth sends `post_logout_redirect_uri` to `/.auth/logout/complete`. If that exact URL is missing from **Allowed Logout URLs**, Auth0 shows “Oops!, something went wrong” on sign-out.

**Allowed Web Origins**

```text
https://gems.bovi-analytics.org,https://gems-dashboard-aed0h2fufpd8byf6.eastus-01.azurewebsites.net
```

If your Easy Auth provider name is not `auth0`, replace `auth0` in the callback path with the provider name shown under **Authentication → Identity providers**.

**Azure (already applied):** `gems-dashboard` Easy Auth **allowed external redirect URLs** includes the custom domain and logout paths above.

Do **not** change `AUTH0_DOMAIN` in Azure — that stays your Auth0 tenant hostname (e.g. `dev-....us.auth0.com`), not `gems.bovi-analytics.org`.

### What does not need Auth0 changes

- **GEMS-API** (`gems-api.bovi-analytics.org`) — API keys and `/authz/*` use `X-API-Key` or dashboard server-to-server secrets, not browser OAuth callbacks.
- **Databricks**, **Table Storage**, and **API_KEY_PEPPER** — unchanged.

### After changing Auth0 URLs

1. Save the Auth0 Application.
2. Sign out of the dashboard (`/.auth/logout`) and sign in again at `https://gems.bovi-analytics.org`.
3. On the **API Access** page, confirm example scripts show `https://gems-api.bovi-analytics.org` as the base URL.
4. Run a quick API check: `GET https://gems-api.bovi-analytics.org/health`.

Access has two tiers:

- **Public tier:** any signed-in user can see the landing page.
- **Elevated/data tier:** a user can access Explore, Modeling, Chat, and API Access only when:
  - their email is listed in `ALLOWED_USERS`, and
  - Auth0 reports `email_verified == true`.

Operational rule: `gems-dashboard.ALLOWED_USERS` controls dashboard pages. `GEMS-API.ALLOWED_API_USERS` controls API-tier access. A user needs to be in both lists to see the dashboard API Access page and generate/use API keys.

The API Access page has one extra gate: verified dashboard users can only generate API keys when `GEMS-API` confirms their email is in `ALLOWED_API_USERS`.

If a user is listed in `ALLOWED_USERS` but has not verified their email, the dashboard treats them as public-tier and shows:

```text
Please verify your email — check your inbox for a verification link from Auth0. Refresh after clicking.
```

## Auth0 configuration

Code now reads `email_verified` from the Easy Auth `X-MS-CLIENT-PRINCIPAL` claim payload. The application-side gate is implemented in `dashboard/gems_auth.py`.

Recommended Auth0 tenant settings:

1. Open the Auth0 Dashboard.
2. Go to **Authentication**.
3. Open **Database**.
4. Select the database connection used by the dashboard.
5. Open **Settings**.
6. Enable email verification / require verified email for that database connection.

For stronger enforcement, add an Auth0 **Action** in the Login flow that blocks unverified users before the dashboard session is created.

Example Action:

```javascript
exports.onExecutePostLogin = async (event, api) => {
  if (!event.user.email_verified) {
    api.access.deny(
      "Please verify your email before accessing GEMS data features."
    );
  }
};
```

This Action is recommended but not required for the dashboard code gate. The dashboard still downgrades unverified allowlisted users to public-tier.

## Resend verification email

The dashboard includes a **Resend verification email** button when a signed-in allowlisted user is not verified.

The button uses the Auth0 Management API endpoint:

```text
POST /api/v2/jobs/verification-email
```

To enable it, configure either:

```text
AUTH0_DOMAIN=<your-auth0-domain>
AUTH0_MANAGEMENT_API_TOKEN=<management-api-token-with-create:users/update:users-or-verification-job-permission>
```

or configure a Machine-to-Machine Auth0 application:

```text
AUTH0_DOMAIN=<your-auth0-domain>
AUTH0_MANAGEMENT_CLIENT_ID=<client-id>
AUTH0_MANAGEMENT_CLIENT_SECRET=<client-secret>
```

The Machine-to-Machine app must be authorized for the Auth0 Management API with permission to create verification email jobs.

If these settings are not configured, the button shows a clear TODO message and no email is sent.

## Manual test note

Manual unverified path test:

1. Add a test email to `ALLOWED_USERS`.
2. Sign in with that Auth0 user before clicking the verification email.
3. Confirm the Home page is visible.
4. Confirm Explore, Modeling, Chat, and API Access are blocked.
5. Confirm the email verification banner appears.
6. Click the Auth0 verification link.
7. Refresh the dashboard.
8. Confirm the same allowlisted user now gets elevated/data access.
