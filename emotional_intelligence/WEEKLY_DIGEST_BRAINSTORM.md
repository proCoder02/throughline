# Weekly Personal-Insight Digest — Brainstorm

Status: exploring, not yet built. This doc tracks the idea, the design
options considered, what's been decided, and what's still open.

## The idea

The EI pipeline already extracts facts, preferences, beliefs, memories,
and personality traits per user — but none of it is ever shown to the
user. It's purely server-side context injected into chat replies. A
periodic "here's what I've noticed about you" digest turns that invisible
data into a visible, curiosity-driven reason to open the app — something
the existing nudges (all obligation-framed: overdue tasks, mood dips,
stale friendships) don't offer.

Expected to be one of the most-viewed sections of the app, comparable to
global chat — so it needs real UI presence, not just a notification
tap-through, and a backend that scales independent of user count.

## UI design (settled through mockup iteration)

Three Artifacts were built and iterated on this session:

1. First pass: single scrollable screen, one long paragraph digest.
2. Second pass: Tinder-style swipeable card stack, one card per insight
   (preference / reminder / mood / personality / friendship), real
   drag-physics (not a carousel — left advances, right goes back).
3. Third pass: split into two phases (all "about you" cards, then all
   "from the world" cards) — **rejected**, felt disconnected.
4. Fourth pass (current direction): **one card per insight, each card
   internally split** — "About you" section on top, a thin "From the
   world" divider, then a recessed panel below with a real-world
   suggestion tied to that specific insight, source credited.
5. Fifth pass: added a photo banner to the "from the world" half, source
   credited as a pill directly on the image.

**Home-screen placement**: mirror `lib/widgets/mood_trend_card.dart`
exactly — a new `InsightPreviewCard`, self-contained, embedded directly in
`ChatsScreen` (the app's landing tab, where global chat's own entry point
also lives) next to the existing `MoodTrendCard`. Gives it permanent
visibility instead of relying on someone tapping a push notification.

## Backend architecture (settled)

**Problem**: naively hitting a news/image API per user per insight
multiplies cost by user count and duplicates work for users who share a
topic.

**Fix**: decouple content ingestion from personalization.
- A shared, scheduled worker (`world_content_refresh.py`, own thread,
  every 6-12h) fetches real-world content **per topic tag** (a small fixed
  vocabulary — hiking, exam-study, morning-mood, productivity, friendship,
  etc.), not per user. Stores into `emotional_intelligence.world_content_cache`.
- Per-user digest generation (`weekly_digest.py`) never calls an external
  API — it does one LLM call to extract *this user's* insights, then
  matches each insight's category against the cache via a plain indexed
  lookup (exact tag match, full-text-search fallback reusing the same
  `tsvector`/GIN pattern `knowledge_cards` already uses).
- Net effect: external API call volume is bounded by **topic count**
  (~20-50), completely flat regardless of whether the app has 100 users or
  10 million. A million users interested in "hiking" all see the same
  cached photo/headline that week.

## Open question: where do the images actually come from?

This is the part still genuinely undecided. Options on the table:

| Option | Pros | Cons |
|---|---|---|
| **NewsAPI / GNews** | Structured JSON incl. a real `image` field tied to the actual article | Free tier is rate-limited + non-commercial only; needs an API key |
| **Google News RSS** | Fully free, no key, no rate-limit paperwork (just public RSS) | Doesn't reliably include a clean image field at all |
| **Scrape article's `og:image`** | Works with any headline source, incl. RSS | Extra HTTP call per item, fragile (sites can block bots), adds latency |
| **Reddit API** | Free tier for non-commercial use; posts often carry `thumbnail`/`preview.images` already extracted | Content is community-voted, not curated news — different tone |
| **Unsplash (topic stock photo, not the literal article photo)** | Reliable, consistent aspect ratio, free tier w/ attribution, no scraping fragility | Not the *actual* article's image — a representative stand-in instead |
| **Generated fallback (deterministic gradient/pattern keyed by topic hash)** | Zero API dependency, zero rate limits, infinite scale, always available | Not a real photo — purely abstract/decorative |

**Current leaning**: hybrid, not either/or. Real cached photo (NewsAPI/GNews
or Unsplash) as the primary path when the cache has one; deterministic
generated gradient (same visual trick used for placeholders in the
mockups) as the fallback when it doesn't — cache miss, API hiccup, or a
topic not yet refreshed. Never show a broken image or blank space.

Since images are cached per-topic (not per-user), reliability/uptime is
the real concern here, not raw scale at millions of users — the caching
model already makes the scale question moot.

**Still to decide**: which real-photo source to actually build against
first (NewsAPI vs GNews vs Unsplash-only), and the exact topic-tag
vocabulary the digest-generation prompt should be constrained to (needs to
be small enough to keep the cache tight, broad enough to cover what
real users' EI data actually produces).

## Data shape (settled)

`weekly_digests.content` is a JSON array of card objects:
```json
[{
  "category": "preference",
  "label": "Preference",
  "headline": "Hiking keeps coming up",
  "body": "You've mentioned hiking a few times now...",
  "world": {
    "headline": "Trail running is having a moment",
    "body": "People who share your interest in hiking are trying...",
    "source": "Outside",
    "source_url": "https://...",
    "image_url": "https://..."
  }
}]
```
`world` is nullable per-card — a card with no matched world content (or a
world section with no image) is still valid and renders fine.

## Delivery mechanism (settled, reuses existing infra)

- Cooldown-gated via the nudge engine's existing `_recently_sent` helper
  (`nudge_type="weekly_digest"`, 7-day cooldown) — no new scheduling
  concept, just a fourth checker function in the existing hourly
  `run_nudge_cycle` loop. This also naturally staggers generation load
  across hours instead of batching everyone on one calendar day.
- Push is a generic teaser ("Your weekly insight is ready"), same
  established pattern as `task_created`/`direct_message` — real content
  fetched via `GET /insights/digest` once the user taps in.

## Not yet decided / not yet scoped

- Final image-source pick (see table above).
- Exact topic-tag vocabulary.
- Whether past digests should be browsable (currently: only the latest is
  ever shown/stored meaningfully; no history view planned yet).
- React client (explicitly deferred — Flutter first, prove the format,
  add React later).
- Whether `InsightPreviewCard` should ever show a "seen" vs "new" state
  differently, or always just show the latest regardless of view status.
