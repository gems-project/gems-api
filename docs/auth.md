# Dashboard Authentication and Email Verification

The GEMS dashboard uses Auth0 through Azure App Service Authentication / Easy Auth.

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
