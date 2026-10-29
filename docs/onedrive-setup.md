# Connecting the warehouse to OneDrive / SharePoint

CBX-Online keeps every file on the server under `data/warehouse/`. When OneDrive sync is switched
on, the same folder tree (`CBX Warehouse/Shared library/…`, `CBX Warehouse/Teams/<team>/<person>/…`)
is created in a OneDrive or SharePoint document library and every file is copied there as it
arrives. The local copy is always the source of truth: if Microsoft is unreachable, nothing stops
working — the `onedrive-sync` routine simply retries every two minutes.

The app signs in as itself (an "app registration" with a client secret), not as a person, so it
keeps working when staff change.

## 1. Register the app in Azure

1. Go to <https://portal.azure.com> → **Microsoft Entra ID** → **App registrations** → **New registration**.
2. Name: `CBX-Online warehouse`. Supported account types: **Accounts in this organizational directory only**.
   Leave Redirect URI empty. Click **Register**.
3. On the app's **Overview** page copy:
   - **Application (client) ID** → `CBX_GRAPH_CLIENT_ID`
   - **Directory (tenant) ID** → `CBX_GRAPH_TENANT_ID`
4. **Certificates & secrets** → **New client secret** → description `cbx`, expiry 24 months → **Add**.
   Copy the **Value** immediately (it is shown once) → `CBX_GRAPH_CLIENT_SECRET`.
   Put a reminder in Doc Watch for the expiry date — when it lapses, sync stops until a new secret is set.

## 2. Give it permission to write files

**API permissions** → **Add a permission** → **Microsoft Graph** → **Application permissions** (not Delegated):

- Simple option: `Files.ReadWrite.All` — the app can write to any drive in the tenant. Fine for a
  single-purpose app on a trusted server.
- Tighter option: `Sites.Selected` — the app can only touch the SharePoint sites you name. After
  adding it, an admin must grant the app `write` on the one site (via Graph:
  `POST /sites/{site-id}/permissions` with `roles: ["write"]` and the app's id/display name, or with the
  PnP PowerShell `Grant-PnPAzureADAppSitePermission`).

Then click **Grant admin consent for <org>** — the status column must show a green tick. Without
consent the token is issued but every Graph call returns 403.

## 3. Find the drive id

Every OneDrive and every SharePoint document library is a "drive". Use **Graph Explorer**
(<https://developer.microsoft.com/graph/graph-explorer>, sign in as an admin):

- SharePoint library (recommended — team-owned, survives staff changes):
  1. `GET https://graph.microsoft.com/v1.0/sites/<tenant>.sharepoint.com:/sites/<SiteName>` → copy `id` (the site id).
  2. `GET https://graph.microsoft.com/v1.0/sites/<site-id>/drives` → pick the library ("Documents" usually) and copy its `id`.
- A person's OneDrive: `GET https://graph.microsoft.com/v1.0/users/<upn>/drive` → `id`.

That value is `CBX_GRAPH_DRIVE_ID` (a long string starting `b!`).

### For the Digital & Transformation site

The site is <https://epsgroupcoza.sharepoint.com/teams/DigitalTransformation> (a Teams site, so the
path is `/teams/…`, not `/sites/…`). The two calls, exactly:

1. `GET https://graph.microsoft.com/v1.0/sites/epsgroupcoza.sharepoint.com:/teams/DigitalTransformation` → `id`
2. `GET https://graph.microsoft.com/v1.0/sites/<that id>/drives` → the library to use → `id` → `CBX_GRAPH_DRIVE_ID`

With `Sites.Selected`, IT grants the app `write` on this one site (step 2 above) and on nothing else.
Two things to settle with IT before the first file lands there: **who is a member of that site** (the
mirror will hold vendor account numbers and compliance certificates — a private site or a dedicated
library with its own permissions, not a public team), and **which library** — a new one called
`CBX-Connect` keeps the mirror apart from the team's own documents and makes the permission grant
easy to read.

## 4. Configure CBX-Online

Add to `.env` next to `run.bat` (never commit this file):

```
CBX_ONEDRIVE_ENABLED=1
CBX_GRAPH_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
CBX_GRAPH_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
CBX_GRAPH_CLIENT_SECRET=the-secret-value
CBX_GRAPH_DRIVE_ID=b!....
CBX_ONEDRIVE_ROOT=CBX Warehouse
```

Restart the app. Then, signed in as the owner, open **Data warehouse** → **Test OneDrive**. It tries
to get a token, reach the drive and list the root folder, and tells you in plain English what is
wrong if anything is. The same check is `POST /api/warehouse/onedrive/test`.

The header pill shows `OneDrive · synced HH:MM` once the first file has gone up, and folder paths in
the warehouse view read `OneDrive/CBX Warehouse/…`. `GET /api/warehouse/onedrive/status` returns
`{enabled, configured, connected, last_sync, pending}` for monitoring.

## What the app actually does

- Folders: `POST /drives/{drive}/root:/{path}:/children` with `"@microsoft.graph.conflictBehavior": "fail"`;
  a 409 means it already exists and is ignored.
- Files under 4 MB: `PUT /drives/{drive}/root:/{path}:/content`.
- Larger files: `createUploadSession` and 5 MB chunks.
- Only writes. It never deletes or renames anything remotely; deletions are done by hand in SharePoint.

## Troubleshooting

| Message from **Test OneDrive** | Fix |
| --- | --- |
| "Microsoft sign-in refused the app (401/400)" | Tenant id, client id or secret is wrong, or the secret expired. |
| "not allowed to see the drive" (403) | Permission not added or admin consent not granted; with `Sites.Selected`, the site grant is missing. |
| "no drive has that id" (404) | Wrong `CBX_GRAPH_DRIVE_ID` — re-do step 3. |
| "Could not reach Microsoft sign-in" | The server has no route to the internet or a proxy blocks `login.microsoftonline.com` / `graph.microsoft.com`. |
