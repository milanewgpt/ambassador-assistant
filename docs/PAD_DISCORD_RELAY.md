# Flow A: Discord Relay — Power Automate Desktop

## Overview

This PAD flow monitors Discord for new notifications, navigates to the relevant
channel, copies the message link, and POSTs it to the backend API.

## Prerequisites

1. **Discord Desktop** installed and logged in with your **secondary** (relay) account.
2. The relay account has joined all target servers and can see announcement/mission channels.
3. **Power Automate Desktop** installed (comes with Windows 11 / Server 2022).
4. **OneDrive** — sign in at <https://onedrive.live.com> once so PAD can save flows.
5. Backend running (locally or on Linux VPS) and reachable from this machine.

## Environment Variables Needed

| Variable | Example |
|----------|---------|
| `BACKEND_URL` | `http://YOUR_LINUX_IP:8000` or `http://localhost:8000` |
| `INGEST_SHARED_SECRET` | same value as in `.env` |

---

## Build Steps (Step-by-Step in PAD Designer)

### Step 1: Create a New Flow

1. Open **Power Automate Desktop**.
2. Click **+ New flow** → name it `DiscordRelay`.
3. Click **Edit flow** to open the designer.

### Step 2: Set Variables

Add these at the top of the flow using **Set variable** actions:

| Variable | Value |
|----------|-------|
| `BackendUrl` | `http://YOUR_LINUX_IP:8000/ingest/discord` |
| `SharedSecret` | your ingest shared secret |
| `PollInterval` | `30` (seconds between checks) |
| `MaxRetries` | `2` |

### Step 3: Main Loop

1. **Add → Loops → Loop** — set to run indefinitely (or a high count like 999999).

### Step 4: Check for Discord Notifications (inside loop)

1. **Add → UI automation → Get details of window** — filter by window title containing "Discord".
2. Check if notification badge exists:
   - **Add → UI automation → Get details of a UI element in window**
   - Target the notification badge element on the Discord taskbar icon.
   - Store the result in `%HasNotification%`.

**Alternative (simpler):** Use a scheduled poll approach:
1. **Add → System → Wait** — wait `%PollInterval%` seconds.
2. Continue to Step 5 every cycle.

### Step 5: Activate Discord

1. **Add → UI automation → Focus window** — title contains "Discord".
2. **Add → System → Wait** — 1 second (let it render).

### Step 6: Open Quick Switcher (Ctrl+K)

1. **Add → Mouse and keyboard → Send keys** — send `{Control}(k)`.
2. **Add → System → Wait** — 0.5 seconds.

### Step 7: Navigate to Target Channel

1. **Add → Mouse and keyboard → Send keys** — type the channel name (e.g., `announcements`).
2. **Add → System → Wait** — 1 second (let search results appear).
3. **Add → Mouse and keyboard → Send keys** — send `{Return}` to select the first result.
4. **Add → System → Wait** — 1.5 seconds (channel loads).

### Step 8: Get Server and Channel Names

1. **Add → UI automation → Get details of a UI element in window**
   - Target the server name element in the Discord sidebar.
   - Store in `%ServerName%`.
2. **Add → UI automation → Get details of a UI element in window**
   - Target the channel name in the header bar.
   - Store in `%ChannelName%`.

### Step 9: Right-click the Latest Message → Copy Message Link

1. **Add → UI automation → Click UI element in window**
   - Target the last message in the chat area.
   - Set click type: **Right-click**.
2. **Add → System → Wait** — 0.5 seconds (context menu appears).
3. **Add → UI automation → Click UI element in window**
   - Target the "Copy Message Link" item in the context menu.
4. **Add → System → Wait** — 0.3 seconds.

### Step 10: Read Clipboard

1. **Add → Clipboard → Get clipboard text** — store in `%MessageLink%`.

### Step 11: Get Preview Text (Optional)

1. **Add → UI automation → Get details of a UI element in window**
   - Target the last message content element.
   - Get its text content into `%Preview%`.

### Step 12: Send HTTP POST to Backend

1. **Add → HTTP → Invoke web service**
   - URL: `%BackendUrl%`
   - Method: **POST**
   - Content type: `application/json`
   - Custom headers: `X-Shared-Secret: %SharedSecret%`
   - Request body:
     ```json
     {
       "server": "%ServerName%",
       "channel": "%ChannelName%",
       "preview": "%Preview%",
       "message_link": "%MessageLink%",
       "observed_at": "%CurrentDateTime%"
     }
     ```
   - Store response status in `%ResponseStatus%`.

### Step 13: Retry Logic

1. **Add → Conditionals → If** — `%ResponseStatus%` is not equal to `200`.
   - Inside: increment a retry counter.
   - If retries > `%MaxRetries%`:
     - **Add → HTTP → Invoke web service** — POST to Telegram API:
       ```
       https://api.telegram.org/bot<TOKEN>/sendMessage
       ```
       Body: `{"chat_id":"<CHAT_ID>","text":"❌ Discord relay failed for %ChannelName%"}`
   - Else: **Add → System → Wait** 5 seconds, then go back to Step 12.

### Step 14: Wait Before Next Poll

1. **Add → System → Wait** — `%PollInterval%` seconds.
2. End of loop → goes back to Step 4.

---

## Testing

1. Run the flow manually in PAD.
2. Trigger a message in a test Discord channel.
3. Check:
   - Backend logs for the POST request.
   - Supabase `signals` table for the new row.
   - Telegram for the forwarded alert.

## Troubleshooting

- **Discord not focusing:** Ensure Discord is not minimized to tray. Keep it on the taskbar.
- **UI elements not found:** Use PAD's UI element picker (recorder) to re-capture elements after Discord updates.
- **Copy Message Link not in context menu:** Ensure you right-click the message bubble, not the username or avatar.
- **HTTP POST fails:** Check that the backend is running and firewall allows the connection.
