# Throughline — Flutter update: mood emoji + push notifications (FCM)

Delta on top of `FLUTTER_APP_SPEC.md`. Two changes, both live on `https://rapexapi.nodexdata.click`.

## 1. Friend mood is now a single compiled emoji, not a raw list

`GET /friends/<id>/mood` response shape **changed** (breaking change from the spec's old `{friend_id, entries: [...]}` shape):

```jsonc
// GET /friends/<id>/mood
{
  "friend_id": 27,
  "window_start": "2026-08-02T14:00:00+05:30",
  "window_end": "2026-08-02T16:00:00+05:30",
  "mood_label": "stressed",   // or null if nothing logged yet today
  "emoji": "😰"                // or null
}
```

- One compiled value per **2-hour clock-aligned window** (00–02, 02–04, ... 22–24 local time) — the dominant mood across everyone's conversations in that window, not a per-entry timeline.
- If the current window has no data yet, it falls back to the most recent earlier window today with data. `mood_label`/`emoji` are both `null` if nothing's been logged at all today.
- `emoji` is computed server-side from a fixed vocabulary — just render it directly, don't maintain your own label→emoji map:
  `happy 😊 · stressed 😰 · calm 😌 · frustrated 😤 · excited 🤩 · neutral 😐 · sad 😢 · anxious 😟` (anything else falls back to 🙂).

**UI**: show one big emoji + the label as a caption when a friend is opened — not a scrollable list. That's the whole screen for this feature now.

## 2. Push notifications (FCM) — calls, tasks, reminder emails, friend mood

The backend now sends real FCM pushes (not just the `/ws/notify` WebSocket, which only reaches an app that's already open) for four event types. **This requires you to build the Flutter-side FCM integration** — the backend send path exists and is tested, but nothing pushes to a real device until you register a token.

### Registering your device

```
POST /devices/register
{ "token": "<the FCM token from firebase_messaging>", "platform": "android" | "ios" }
```
Call this once on launch (after login) and again every time `FirebaseMessaging.instance.onTokenRefresh` fires. It's an upsert (`ON CONFLICT (token) DO UPDATE`) — safe to call repeatedly, and correctly reassigns the token if the same device later logs into a different account.

```
POST /devices/unregister
{ "token": "<token>" }
```
Call this on logout so the signed-out device stops getting pushed to for that account.

### The four notification types

Every push has a `notification.title`/`notification.body` (for the OS tray) plus a `data` payload (all values stringified) so you can route the tap:

| Event | `data.type` | `data` fields | Notes |
|---|---|---|---|
| Incoming call | `incoming_call` | `call_id`, `room_name`, `caller_id`, `caller_name` | See the PushKit/CallKit caveat below — this is the one that needs real platform-specific work to feel like WhatsApp/Telegram. |
| Task created | `task_created` | `task_id` | Fires from both the live-session pipeline and `/analyze` (record-then-upload flow) — covers every path that can create a task. |
| Reminder email sent | `reminder_email_sent` | `task_id` | Fires the moment the backend's existing email-reminder worker actually sends the email — same trigger, parallel channel, not dependent on the user checking their inbox. |
| Friend's mood updated | `friend_mood_update` | `friend_id`, `friend_name`, `mood_label`, `emoji` | Pushed to a user's friends once per 2-hour window (see §1) when a new window's compiled mood first becomes available — not on every single conversation, so this doesn't spam. |

### ⚠️ Incoming calls: FCM alone isn't enough for a real call UI on iOS

For Android, a high-priority FCM data message (already what the backend sends) is enough to wake your app in the background and show a full-screen incoming-call notification via a background message handler.

**iOS is different.** Apple requires **PushKit (VoIP push) + CallKit** for an app to reliably wake from a killed/backgrounded state and show a native-feeling incoming call screen — a regular FCM/APNs alert push can be delayed or dropped by iOS for this use case, which is exactly the failure mode "exactly like WhatsApp/Telegram" is trying to avoid. This is a real, separate integration:
- Requires an **additional APNs key/certificate configured for VoIP** in your Apple Developer account (different from the standard push certificate).
- Requires native iOS code (or a plugin like `flutter_callkit_incoming` combined with `flutter_voip_push_notification` or similar) to receive the VoIP push and present the CallKit UI.
- The backend's role doesn't change either way — it already sends the FCM message with `apns-priority: 10`; whether you route that through standard APNs or wire up a separate VoIP push path is entirely a Flutter/iOS-side decision.

Don't build the iOS call UI assuming plain FCM will behave like a real call notification — it won't reliably wake the app from killed state. Decide explicitly whether to invest in PushKit/CallKit now or ship Android-quality calling first and treat iOS call notifications as a known gap.

## Before any of this actually delivers a push: Firebase project setup (not done yet)

The backend's FCM send path is deployed and tested (it correctly no-ops right now since no Firebase project is wired up yet — confirmed via a live test that registered a device and triggered all four trigger points with zero errors). To make it actually send:

1. **Create a Firebase project** at [console.firebase.google.com](https://console.firebase.google.com) (or use an existing one).
2. **Add your Flutter app to it** (Android package name + iOS bundle ID) — this is also how you get the `google-services.json` / `GoogleService-Info.plist` your Flutter app needs for the `firebase_messaging` package itself.
3. **Generate a service account key** for the backend: Firebase Console → Project settings → **Service accounts** tab → **Generate new private key** → downloads a JSON file.
4. **Get that JSON file onto the EC2 server** (it must never be committed to git):
   ```
   scp path\to\your-service-account.json rapex-ec2:~/throughline/firebase-service-account.json
   ```
5. **Point the backend at it** — add to `~/throughline/.env` on the server:
   ```
   FIREBASE_CREDENTIALS_PATH=/home/ec2-user/throughline/firebase-service-account.json
   ```
6. **Restart the service**: `ssh rapex-ec2 "sudo systemctl restart throughline"`.

Once that's done, tell me and I'll run a real end-to-end test (register a throwaway device token isn't enough for a real push — I'd need either your real device's token for one test, or we confirm via Firebase's delivery logs) to confirm delivery before you build against it.

## Nothing else changed

All other endpoints, data models, and WebSocket events from `FLUTTER_APP_SPEC.md` and the earlier tags/chat-formatting update are unaffected.
