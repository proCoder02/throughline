# Media Storage (R2): Profile Pictures + Chat Attachments

## Implementation status: DONE (except real R2 credentials)

Backend, React, and Flutter are all implemented and verified (`py_compile`,
live smoke tests against a local Postgres with two real registered/friended
test users, `npm run build`, `flutter analyze`, `flutter build apk`). The
feature is inert until an operator sets real Cloudflare credentials --
`R2_STORAGE_ENABLED=False` in `.env` right now, so `/uploads/status` reports
`enabled: false` and both clients hide every upload affordance.

**Decisions made while implementing, resolving what this doc originally
flagged as open:**
- **Size-cap caveat**: went with option (b) -- `storage.py`'s
  `confirm_upload()` does a real `HEAD` request and deletes + rejects
  anything over the purpose's `MAX_BYTES`, rather than switching to
  presigned POST.
- **Flutter document/file sending**: originally scoped out because
  `file_picker@8.x`'s `flutter_plugin_android_lifecycle` dependency requires
  `compileSdk 36` while this project's `android/app/build.gradle.kts` was
  pinned to Flutter's own default (34), and bumping that project-wide
  looked like a real infra change with its own regression risk. Resolved
  properly instead: `compileSdk`/`targetSdk` bumped to 36 in
  `build.gradle.kts`, and `file_picker` bumped to `^10.3.3` (the first
  release to read `flutter.compileSdkVersion` rather than hardcoding 34
  in its own `android/build.gradle`). Verified via a real
  `flutter build apk --debug` -- no other plugin (`livekit_client`,
  `flutter_webrtc`, `firebase_*`, `record`, etc.) broke from the bump.
  Flutter now sends image/video/document, same three kinds as React.
- **Video on Flutter**: uploads and sends correctly, but renders as a
  tappable "Video" card (opens externally via `url_launcher`) rather than
  inline playback or a real thumbnail -- inline playback/thumbnails need
  `video_player`/`video_thumbnail`, not yet dependencies of this app.
- Every avatar in both apps (`Avatar.jsx` / `InitialAvatar`) now shows a
  real profile picture when one exists, falling back to the original
  initial-letter circle otherwise -- one shared widget per platform, so
  this covers every screen at once, not screen-by-screen.

## Context

The app currently has no permanent media storage. `/chat/global/image` (app.py)
already accepts an image upload, but only to extract text via a vision LLM —
the bytes are discarded (`_prepare_image_for_vision`'s temp file is deleted in
a `finally`). Profile pictures, and video/image/file sharing in chats and DMs,
need bytes to actually persist somewhere. Cloudflare R2 was chosen over S3
specifically because **egress is free with no limit**, on the free tier and
beyond — the cost that would otherwise scale with every time a shared photo
or video gets viewed, not just uploaded.

**Free tier**: 10 GB-month storage, 1M Class A ops/month (writes), 10M Class B
ops/month (reads), unlimited free egress. Beyond that: $0.015/GB-month,
$4.50/M writes, $0.36/M reads — still $0 egress. Video is what's most likely
to outgrow the free 10GB; images/profile pictures alone would last a long
time.

**Architecture**: presigned-URL direct upload. The Flask backend never
receives file bytes for large media — it only ever hands out a short-lived
presigned PUT URL, and the client (React or Flutter) uploads directly to R2.
This keeps large video uploads off the Flask process entirely (no memory
spike proxying a 50MB file through a single-threaded dev server) and matches
R2's actual intended usage pattern.

```
Client                         Flask backend                    R2
  │ 1. POST /uploads/presign        │                              │
  │    {purpose, content_type} ───► │                              │
  │                                 │ 2. generate presigned PUT URL │
  │ ◄─── {upload_url, object_key} ──│    (boto3, S3-compatible)     │
  │                                 │                              │
  │ 3. PUT upload_url (raw bytes) ─────────────────────────────────►│
  │                                 │                              │
  │ 4. POST /uploads/confirm        │                              │
  │    {object_key, ...} ─────────► │ 5. INSERT/UPDATE row with URL │
  │ ◄─── {ok, public_url} ──────────│                              │
```

Nothing here changes any *existing* route's behavior — every piece below is
additive (new columns via `ADD COLUMN IF NOT EXISTS`, new routes, new files).

**Two ideas borrowed from how WhatsApp actually handles media**, without
adopting WhatsApp's end-to-end encryption (which would work against this
app's whole premise of server-side LLM processing of content):

- **Inline thumbnail preview** — a small, low-res preview generated
  client-side and sent *inline in the message row itself* (not a second R2
  object, not a second network request), so the message renders an image
  instantly instead of waiting on the full-resolution file to load. This is
  also why it's stored in Postgres rather than R2: once the full-res object
  eventually expires off R2 (see the lifecycle rule below), the thumbnail
  keeps working forever, since it was never subject to R2's lifecycle at
  all — an old message degrades to "small blurry preview" instead of a
  broken image icon.
- **Expiry instead of forever** — R2's Object Lifecycle Rules auto-delete
  chat media after N days (bucket-level config, no application code),
  keeping actual usage further under the free 10GB rather than accumulating
  indefinitely. Profile pictures are explicitly excluded from this rule —
  they're identity, not ephemeral chat content, and should persist until
  replaced.

---

## 1. Cloudflare setup (manual, one-time)

1. Create an R2 bucket (e.g. `throughline-media`).
2. Create an R2 API token (Account → R2 → Manage API Tokens) scoped to that
   bucket, with Object Read & Write permission. Note the **Access Key ID**,
   **Secret Access Key**, and **Account ID**.
3. Decide public-read vs presigned-GET for *serving* files:
   - **Public bucket + custom domain** (simplest): enable the bucket's public
     access, point a subdomain (e.g. `media.yourdomain.com`) at it via
     Cloudflare DNS. Anyone with the URL can view the file — fine for chat
     media/profile pictures in this app's threat model (friends-only sharing,
     nothing here is meant to be secret), and it's what makes the "unlimited
     free egress" number simple to reason about.
   - Alternative: keep the bucket private and generate presigned GET URLs
     per-request (more backend calls, short-lived links). Not recommended
     here — adds complexity for a case (friend photos) that doesn't need it.
   - **This plan assumes the public-bucket approach.**
4. **Object Lifecycle Rule** (Bucket → Settings → Object Lifecycle Rules —
   dashboard config, not code): add a rule scoped to the `chat-media/`
   prefix only, "delete objects after N days" (pick N — 30 to mirror
   WhatsApp's own rough window is a reasonable default). Do **not** create a
   rule matching `profile-pictures/` — those should live until explicitly
   replaced (see `/profile/picture`'s old-object cleanup below), not expire
   on a timer. Since the key prefixes already separate `profile-pictures/`
   from `chat-media/images|videos|files/` (see `storage.py`'s `_PURPOSES`
   below), one prefix-scoped rule is enough — no per-object tagging needed.

## 2. Backend (`app.py` + new `storage.py`) — shared by both clients

### New dependency
`boto3` (R2's API is S3-compatible; boto3 talks to it by pointing at R2's
endpoint instead of AWS's).

### New env vars (`.env`, local and prod)
```
R2_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET_NAME=throughline-media
R2_PUBLIC_URL_BASE=https://media.yourdomain.com   # or the bucket's r2.dev URL for testing
```

### New file `storage.py`
```python
import boto3
import os
import uuid

def _client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )

# purpose -> key prefix, and an allowlist of content-types so
# /uploads/presign can't be used to stage arbitrary file types.
_PURPOSES = {
    "profile_picture": {"prefix": "profile-pictures", "types": {"image/jpeg", "image/png", "image/webp"}},
    "chat_image": {"prefix": "chat-media/images", "types": {"image/jpeg", "image/png", "image/webp", "image/gif"}},
    "chat_video": {"prefix": "chat-media/videos", "types": {"video/mp4", "video/quicktime", "video/webm"}},
    "chat_file": {"prefix": "chat-media/files", "types": None},  # None = any type allowed
}

def presign_upload(purpose, content_type, user_id, max_bytes):
    spec = _PURPOSES[purpose]  # raises KeyError -> route returns 400 for an unknown purpose
    if spec["types"] is not None and content_type not in spec["types"]:
        raise ValueError(f"content_type {content_type} not allowed for {purpose}")
    ext = content_type.split("/")[-1]
    key = f"{spec['prefix']}/{user_id}/{uuid.uuid4().hex}.{ext}"
    url = _client().generate_presigned_url(
        "put_object",
        Params={
            "Bucket": os.environ["R2_BUCKET_NAME"],
            "Key": key,
            "ContentType": content_type,
            "ContentLengthRange": (0, max_bytes),  # note: see caveat below
        },
        ExpiresIn=600,  # 10 minutes to actually perform the PUT
    )
    return key, url

def public_url(key):
    return f"{os.environ['R2_PUBLIC_URL_BASE']}/{key}"

def delete_object(key):
    _client().delete_object(Bucket=os.environ["R2_BUCKET_NAME"], Key=key)
```

**Caveat to verify at implementation time**: S3's `generate_presigned_url`
doesn't actually enforce `ContentLengthRange` the way a presigned POST policy
does — that constraint is a presigned-POST feature, not presigned-PUT. For a
real max-size guarantee, either (a) switch to `generate_presigned_post`
(returns form fields instead of a single URL — small client-side difference:
a multipart form POST instead of a raw PUT), or (b) accept that the
enforcement happens only in `/uploads/confirm` (reject + delete the object
if R11's `HeadObject` reports it over the limit after the fact). Pick one
before shipping; don't assume the PUT-based snippet above enforces size.

### New routes in `app.py`

```
POST /uploads/presign
  body: {purpose: 'profile_picture'|'chat_image'|'chat_video'|'chat_file', content_type}
  -> {upload_url, object_key}
  @login_required. Validates purpose+content_type via storage.py's allowlist.
  Rejects (400) unknown purpose/type before calling R2 at all.

POST /uploads/confirm
  body: {object_key, purpose}
  -> {ok, public_url}
  @login_required. Optionally HEADs the object to confirm it actually exists
  and check size, then just returns the public URL -- this route does NOT
  write to any table itself. The caller (profile-picture save, or
  send-message-with-attachment) is what persists the URL, since where it
  gets stored depends on what it's for.

POST /profile/picture
  body: {object_key}
  -> {ok, profile_picture_url}
  @login_required. Confirms the object (HEAD), computes public_url, does
  UPDATE users SET profile_picture_url = %s WHERE id = %s, and if the user
  already had one, deletes the old object (storage.delete_object) so
  uploads don't accumulate forever.
```

### Extend two existing routes (additive fields only, nothing removed)

- `POST /friends/<friend_id>/messages` (app.py:3908, `send_direct_message`):
  accept optional `attachment_url`/`attachment_type`/`thumbnail_data_url` in
  the request body, include them in the `INSERT INTO direct_messages` and
  the `RETURNING` clause. Existing callers that never send these fields are
  unaffected (`data.get("attachment_url")` defaults to `None`). Worth a
  server-side length cap on `thumbnail_data_url` (e.g. reject over ~50KB)
  so a client bug that skips downscaling can't silently bloat every row.
- `POST /chat/global` and the per-conversation `/chat` route: same additive
  pattern for `chat_messages`, if image/video/file sharing should also work
  in the AI-chat surfaces, not just DMs. (Lower priority than DMs — confirm
  scope before implementing both.)
- `GET /me` (app.py:1007) and `GET /settings` (app.py:1022): add
  `profile_picture_url` to the response so both clients can render it
  without a second request.

### Schema (`schema.sql`, additive migrations only — same pattern already
used in this codebase for `friends_since`/`payment_ref`)
```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_picture_url TEXT;
ALTER TABLE direct_messages ADD COLUMN IF NOT EXISTS attachment_url TEXT;
ALTER TABLE direct_messages ADD COLUMN IF NOT EXISTS attachment_type TEXT;
-- Inline base64 data: URL (a few KB at most -- a ~160px-wide JPEG), NOT an
-- R2 reference. Lives in Postgres specifically so it survives the parent
-- R2 object's lifecycle-rule expiry (see Cloudflare setup, step 4).
ALTER TABLE direct_messages ADD COLUMN IF NOT EXISTS thumbnail_data_url TEXT;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS attachment_url TEXT;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS attachment_type TEXT;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS thumbnail_data_url TEXT;
```

**Size sanity check**: a 160px-wide JPEG at low quality is typically
5-15KB, which base64-inflates to ~7-20KB per message. That's trivial for
Postgres row storage at any realistic message volume, but don't skip
downscaling client-side before encoding — sending a full-res image through
this column instead of the R2 upload path would defeat the entire point.

---

## 3. React (`frontend/src`)

### New `frontend/src/lib/uploads.js`
```javascript
import { post } from '../api.js';

export async function uploadFile(file, purpose, onProgress) {
  const { upload_url, object_key } = await post('/uploads/presign', {
    purpose, content_type: file.type,
  });
  await new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', upload_url);
    xhr.setRequestHeader('Content-Type', file.type);
    xhr.upload.onprogress = (e) => onProgress?.(e.loaded / e.total);
    xhr.onload = () => (xhr.status < 300 ? resolve() : reject(new Error('Upload failed')));
    xhr.onerror = () => reject(new Error('Upload failed'));
    xhr.send(file);
  });
  return object_key;
  // XHR (not fetch) specifically for upload progress events -- fetch has
  // no upload-progress API as of this writing.
}

// Downscaled, low-quality JPEG as a base64 data: URL -- sent inline with
// the message itself (see the thumbnail_data_url schema column), never
// uploaded to R2. For video, pass a captured frame (e.g. via a hidden
// <video>+<canvas> seeked to 0s) as the `source` instead of the raw file.
export async function makeThumbnailDataUrl(source, maxWidth = 160) {
  const bitmap = await createImageBitmap(source);
  const scale = Math.min(1, maxWidth / bitmap.width);
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg', 0.5); // quality 0.5 -- a preview, not the delivered image
}
```

### Profile picture
- `SettingsSection.jsx`: add an "Profile picture" card — a file `<input
  type="file" accept="image/*">` (hidden, triggered by clicking the avatar,
  same pattern `Composer.jsx`'s attach button already uses), on selection:
  `uploadFile(file, 'profile_picture')` → `post('/profile/picture',
  {object_key})` → update local user state so the avatar refreshes
  immediately.
- `IconRail.jsx`'s avatar-btn and every other `<span className="avatar">`
  usage: render `<img src={profile_picture_url}>` instead of the initial
  letter when a `profile_picture_url` exists, falling back to the initial
  otherwise. This touches several files (IconRail, MessageRow, ListPane
  rows, InfoPanel hero) — the safest approach is a small shared
  `<Avatar url={...} initial={...} size=".../>` component replacing the
  raw `<span className="avatar">` pattern everywhere, so this is one
  component change instead of N inconsistent ones.

### Chat/DM attachments
- `Composer.jsx`: already has an image-attach button (`onImageSelected`)
  used today only for the vision-extraction flow. Add a *second*,
  general-purpose attach button (or extend the existing `+` menu per the
  earlier Discord-reference discussion) offering image/video/file, using
  `uploadFile()` above instead of the vision-extraction path.
- `ImageComposePreview.jsx`: already the right shape (preview + confirm
  send) — generalize it to `MediaComposePreview.jsx` handling
  image/video/file preview, not just images, with an upload-progress bar
  driven by `uploadFile`'s `onProgress`. Call `makeThumbnailDataUrl()`
  here, on the same file the user just picked, before/alongside the real
  upload — both can run concurrently since the thumbnail never touches R2.
- `MessageRow.jsx`: render `thumbnail_data_url` immediately if present
  (shows instantly, no network wait) with a subtle low-quality look — for
  images, swap to the full `attachment_url` once it's loaded (a plain
  `<img>` `onLoad` swap is enough, no need for a loading library); for
  video, keep the thumbnail as a poster frame with a play affordance rather
  than autoloading the full file. Generic files (no thumbnail) render a
  download-link card as before. If `attachment_url` 404s (expired off R2
  per the lifecycle rule) fall back to just the thumbnail with a small
  "photo no longer available" label instead of a broken-image icon.
- `DirectMessageThread.jsx`'s `send()`: extend to accept an optional
  attachment, pass `attachment_url`/`attachment_type`/`thumbnail_data_url`
  through to `POST /friends/:id/messages`. The existing
  optimistic-send/offline-outbox flow (see the DM optimistic-send work
  already shipped) needs to store all three in the IndexedDB outbox row
  too, so a retry after a failed send doesn't lose the attachment or its
  preview.

---

## 4. Flutter (`throughline/lib`)

### New dependency
`http` (already likely present) for the presigned PUT; `image_picker`
(already added earlier for the image-chat feature) for picking
images/video; `file_picker` (new) for generic file selection; `image` (new)
for the thumbnail resize/re-encode; `video_thumbnail` (new) for grabbing a
frame from a picked video to thumbnail the same way.

### New `lib/services/upload_service.dart`
```dart
import 'dart:convert';
import 'dart:io';
import 'package:dio/dio.dart';
import 'package:image/image.dart' as img;

class UploadService {
  final _api = ApiClient.instance;

  Future<String> uploadFile(File file, String purpose, String contentType, {void Function(double)? onProgress}) async {
    final presign = await _api.dio.post('/uploads/presign', data: {
      'purpose': purpose, 'content_type': contentType,
    });
    final uploadUrl = presign.data['upload_url'] as String;
    final objectKey = presign.data['object_key'] as String;

    // A separate plain Dio instance (not ApiClient.instance) -- this PUT
    // goes straight to R2, not the app's own backend, and must NOT carry
    // the app's Bearer auth header.
    await Dio().put(
      uploadUrl,
      data: file.openRead(),
      options: Options(
        headers: {'Content-Type': contentType, Headers.contentLengthHeader: await file.length()},
      ),
      onSendProgress: (sent, total) => onProgress?.(sent / total),
    );
    return objectKey;
  }

  /// Mirrors uploads.js's makeThumbnailDataUrl -- downscaled low-quality
  /// JPEG as a base64 data: URL, sent inline with the message, never
  /// uploaded to R2. For video, pass a frame captured via video_thumbnail
  /// instead of the raw file bytes.
  Future<String> makeThumbnailDataUrl(File imageSource, {int maxWidth = 160}) async {
    final bytes = await imageSource.readAsBytes();
    final decoded = img.decodeImage(bytes)!;
    final resized = img.copyResize(decoded, width: maxWidth.clamp(1, decoded.width));
    final jpeg = img.encodeJpg(resized, quality: 50);
    return 'data:image/jpeg;base64,${base64Encode(jpeg)}';
  }
}
```

### Profile picture
- `lib/screens/settings/settings_screen.dart` (or wherever the equivalent
  of React's SettingsSection lives): tap avatar → `image_picker` →
  `UploadService.uploadFile(file, 'profile_picture', 'image/jpeg')` →
  `ApiClient.instance.dio.post('/profile/picture', data: {'object_key': ...})`.
- A shared `Avatar` widget (mirrors the React-side consolidation) —
  `NetworkImage`/`CachedNetworkImage` when a URL exists, initial-letter
  circle otherwise. Search the codebase for every existing
  initial-letter-avatar `CircleAvatar` usage and route it through this one
  widget, same reasoning as the React side: one change point, not N.

### Chat/DM attachments
- `lib/screens/chats/global_chat_screen.dart` (already has an attach flow
  for the vision-extraction image feature) and the DM screen: add
  video/file pick options (`image_picker`'s `pickVideo`, `file_picker`'s
  `FilePicker.platform.pickFiles()`). For images, call
  `makeThumbnailDataUrl()` on the picked file; for video, grab a frame via
  `video_thumbnail`'s `VideoThumbnail.thumbnailData` first and pass that
  through the same resize/encode path. Upload the full file via
  `UploadService`, then send the message with
  `attachment_url`/`attachment_type`/`thumbnail_data_url` fields alongside
  existing `description`/`content`.
- `lib/widgets/message_bubble.dart` (or its flat-row equivalent if this was
  ported from the React redesign): decode and show `thumbnailDataUrl`
  immediately (`Image.memory(base64Decode(...))`) while the full
  `attachmentUrl` loads in the background, same instant-preview behavior as
  the React side; on a failed/expired `attachmentUrl` load, fall back to
  just the thumbnail with a small "no longer available" label instead of
  Flutter's default broken-image icon.
- `lib/models/chat_message.dart`: add `attachmentUrl`/`attachmentType`/
  `thumbnailDataUrl` fields to the model, included in `toJson()`/
  `fromJson()` this time (unlike the earlier session's `localImage` field,
  which was deliberately local-only/non-persisted — these ARE meant to
  survive a reload, since the backend now actually stores them).

---

## 5. Testing / rollout checklist

1. `/uploads/presign` rejects an unknown `purpose` (400) and a
   disallowed `content_type` for a given purpose (400), before ever calling
   R2 — confirm no R2 API call happens on a rejected request (check R2
   dashboard request count, or add a log line).
2. Upload a real profile picture end-to-end (React and Flutter separately):
   presign → PUT to R2 → confirm → `GET /me` reflects the new
   `profile_picture_url` → old picture's R2 object is actually deleted
   (check the bucket).
3. Send a DM with an image attachment; confirm the recipient's WS push
   (`direct_message` event) carries `attachment_url`/`attachment_type` and
   renders correctly on both platforms.
4. Send a DM attachment while offline (React): confirm it lands in the
   `dm_outbox` IndexedDB store with the attachment reference intact, and
   retries correctly (including the attachment) once back online.
5. Confirm large-file behavior matches whatever was decided for the
   `ContentLengthRange` caveat above — try uploading something over the
   intended size limit and verify it's actually rejected somewhere, not
   silently accepted.
6. Cost sanity check after a week of real usage: R2 dashboard → confirm
   storage/operations are tracking within the free tier, or that the
   overage is the expected cheap amount if not.
7. Thumbnail renders instantly: send an image attachment, confirm the
   `thumbnail_data_url` bubble appears immediately (no visible wait), then
   confirm it swaps to the full-resolution `attachment_url` shortly after —
   on both platforms.
8. Thumbnail size discipline: inspect a real sent row's `thumbnail_data_url`
   length in Postgres — should be single-digit-to-low-teens KB, not
   hundreds of KB (a sign downscaling didn't actually run).
9. Lifecycle expiry graceful degradation: manually delete an R2 object (or
   set a 1-day rule temporarily to test faster) and confirm the *old*
   message still renders its thumbnail with a "no longer available" label
   instead of a broken-image icon on both platforms, and that
   `profile-pictures/`-prefixed objects are unaffected by the rule.
