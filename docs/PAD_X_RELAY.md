# Flow B: X (Twitter) Relay — Power Automate Desktop

## Overview

This PAD flow captures new posts from your main X account **without using the
official API**. It works by using a secondary X account (subscribed to your main
account with notifications ON) and a dedicated Chrome profile.

## Prerequisites

1. **Google Chrome** installed with a **separate profile** logged into the secondary X account.
   - To create a profile: Chrome → top-right avatar → **Add** → sign in.
   - Name it something like "XRelay".
2. The secondary account follows your main handle with **notifications turned ON** (bell icon → All posts).
3. **Power Automate Desktop** installed.
4. **OneDrive** signed in at <https://onedrive.live.com>.
5. Backend API running and reachable.

## Chrome Profile Launch

To launch Chrome with the specific profile from PAD or command line:

```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --profile-directory="Profile 2"
```

Find your profile directory name at `chrome://version` → "Profile Path" → last folder name.

---

## Build Steps (Step-by-Step in PAD Designer)

### Step 1: Create a New Flow

1. Open **Power Automate Desktop**.
2. Click **+ New flow** → name it `XRelay`.
3. Click **Edit flow**.

### Step 2: Set Variables

| Variable | Value |
|----------|-------|
| `BackendUrl` | `http://YOUR_LINUX_IP:8000/ingest/x` |
| `SharedSecret` | your ingest shared secret |
| `MainHandle` | your main X handle (without @) |
| `ChromeProfile` | `Profile 2` (your relay profile directory) |
| `PollInterval` | `90` (seconds between checks) |
| `MaxRetries` | `2` |

### Step 3: Main Loop

1. **Add → Loops → Loop** — run indefinitely.

### Step 4: Launch/Activate Chrome with Correct Profile

1. **Add → System → Run application**
   - Application path: `C:\Program Files\Google\Chrome\Application\chrome.exe`
   - Arguments: `--profile-directory="%ChromeProfile%" https://x.com/%MainHandle%`
   - Wait for application to load: **Yes**
2. **Add → System → Wait** — 3 seconds (page loads).

**OR** if Chrome is already open:

1. **Add → Web automation → Launch new Chrome** or **Attach to running Chrome**.
   - Note: For web automation actions, you need the PAD browser extension installed.

### Step 5: Navigate to Profile Page

1. **Add → Web automation → Go to web page**
   - URL: `https://x.com/%MainHandle%`
2. **Add → System → Wait** — 3 seconds.

### Step 6: Click the First (Newest) Tweet

1. **Add → Web automation → Click element on web page**
   - Use CSS selector for the first tweet article:
     ```css
     article[data-testid="tweet"]:first-of-type
     ```
   - Or use the PAD recorder to capture the first tweet element.
2. **Add → System → Wait** — 2 seconds (tweet detail view loads).

### Step 7: Copy the URL from Address Bar

1. **Add → Web automation → Get details of web page**
   - Get: **URL** of the current page.
   - Store in `%TweetUrl%`.

**Alternative (keyboard method):**
1. **Add → Mouse and keyboard → Send keys** — `{Control}(l)` (focus address bar).
2. **Add → Mouse and keyboard → Send keys** — `{Control}(c)` (copy URL).
3. **Add → Clipboard → Get clipboard text** — store in `%TweetUrl%`.

### Step 8: Validate URL

1. **Add → Conditionals → If** — `%TweetUrl%` contains `/status/`.
   - If **No**: skip this cycle (not a valid tweet URL).

### Step 9: Send HTTP POST to Backend

1. **Add → HTTP → Invoke web service**
   - URL: `%BackendUrl%`
   - Method: **POST**
   - Content type: `application/json`
   - Custom headers: `X-Shared-Secret: %SharedSecret%`
   - Request body:
     ```json
     {
       "url": "%TweetUrl%",
       "observed_at": "%CurrentDateTime%"
     }
     ```
   - Store response status in `%ResponseStatus%`.

### Step 10: Handle Response

1. **Add → Conditionals → If** — `%ResponseStatus%` equals `200`.
   - Parse response body. If `detail` is `"duplicate"`, log and skip.
   - If `detail` is `"stored"`, new post was captured.

### Step 11: Retry Logic

1. If `%ResponseStatus%` is not `200`:
   - Increment retry counter.
   - If retries > `%MaxRetries%`:
     - POST to Telegram API:
       ```
       {"chat_id":"<CHAT_ID>","text":"❌ X relay failed — could not capture new post"}
       ```
   - Else: wait 5 seconds and retry Step 9.

### Step 12: Navigate Back to Profile

1. **Add → Web automation → Go to web page** — `https://x.com/%MainHandle%`
   - This resets the view for the next poll cycle.

### Step 13: Wait Before Next Poll

1. **Add → System → Wait** — `%PollInterval%` seconds.
2. End of loop → back to Step 4.

---

## Notification-Based Trigger (Alternative)

Instead of polling, you can trigger on Chrome desktop notifications:

1. Ensure Chrome notifications are enabled for x.com.
2. In the secondary account, turn on notifications for your main account.
3. Use PAD's **Wait for window** action to detect a Chrome notification toast.
4. When detected, proceed from Step 5 onwards.

This is more responsive but less reliable (notifications can be missed). The
polling approach (Steps 3-13) is recommended as the default, with notification
detection as an optional enhancement.

---

## Testing

1. Post a test tweet from your main account.
2. Run the XRelay flow manually.
3. Verify:
   - Chrome navigates to your profile and opens the latest tweet.
   - Backend receives the POST (check logs).
   - `posts` table in Supabase has the new row with `source='x_relay'`.
   - `score_jobs` table has a job scheduled for `created_at + 48h`.

## Troubleshooting

- **Chrome opens wrong profile:** Double-check the `--profile-directory` value at `chrome://version`.
- **Tweet selector fails:** X frequently changes its DOM. Re-record the UI element in PAD.
- **Address bar URL doesn't contain `/status/`:** The click didn't navigate to tweet detail. Add a longer wait or use the web automation `Get details of web page` → URL.
- **Backend returns 401:** Verify `X-Shared-Secret` header matches `INGEST_SHARED_SECRET` in `.env`.
- **Duplicate responses:** This is expected and harmless — backend deduplicates by URL.
