# Design: memory sources, community, and worlds

Status: **Proposed — awaiting owner confirmation.** Nothing here is built yet.

## Problem

Lore is one ever-growing pool per channel. That fails in three ways:

1. **Invented cast on solo and react streams.** Every name the model notices can only be
   typed `character | person | place | item | group | other`. A chatter read out twice becomes a
   confirmed `person`; a character in a reacted video becomes a `character` and its plot becomes
   the stream's storyline. Video audio is transcribed as if spoken on stream, and speaker ID
   treats video voices as new speakers.
2. **No separation between what a streamer does.** GTA RP on Monday, Elden Ring on Tuesday and
   just chatting on Wednesday share one cast and one storyline list.
3. **Twitch category is unreliable.** Streamers play games under "Just Chatting" and chat under a
   game category, so category alone cannot decide which lore applies.

## Principles

- **Never invent.** When the model cannot tell what a name is, it creates nothing. An empty cast
  is a correct result for a solo stream.
- **Every memory item records where it came from** (its *source*), and summaries use that
  wording ("Sam reacted to a video where…", "chat regular Dave…").
- **Category is a hint, not a decision.** What is actually happening in the transcript wins.
- **Everything automatic can be overridden**, and nothing is deleted by reclassification.

## Concepts

### Sources

Every name the extractor proposes carries a source:

| Source | Examples | Where it lives | Cast? |
|---|---|---|---|
| `streamer` | the streamer and their configured characters | channel config (exists today) | always |
| `participant` | RP characters, squadmates, co-streamers, guests | the active **world** | yes |
| `chat` | a chatter the streamer reads out or replies to | **community** (channel) | only once a *regular* |
| `media` | people/characters in a video, movie, clip being reacted to | the **media item** | no |
| `mentioned` | celebrities, other streamers, news being discussed | **topics** (channel) | no |

### Memory tiers

- **World canon**: participants, places, items and storylines of one continuous setting
  ("NoPixel GTA RP", "Elden Ring playthrough").
- **Community** (per channel, follows the streamer across worlds): chat regulars, running
  bits with chat.
- **Media** (per channel): videos/shows/creators reacted to. Characters inside media stay
  attached to that media item and never enter any cast. A creator or show that recurs across
  streams becomes a recurring topic ("weekly Survivor reactions").
- **Topics and personal storylines** (per channel): what solo streamers carry between streams
  ("apartment hunt", "diet challenge", "the drama with X"). These replace a cast for
  just-chatting streamers.

### Activity and stream parts

Each analysis window gets an **activity** label from the extraction call (no extra LLM call):
`gameplay`, `roleplay`, `media_react`, `chatting`, or `break` (ads, BRB, music). Consecutive
windows with the same activity and world form a **stream part**. One stream can have several
parts (two hours chatting, then three hours of a game).

### Worlds

A world is a continuous setting with its own canon. Stream parts are assigned to worlds, not
whole sessions. A part that cannot be assigned confidently is **unsorted** and writes no
world canon (see *Held proposals* below).

## World assignment

Signals, strongest first:

1. **Name overlap**: participants mentioned in recent windows that already belong to a world's
   cast. Three known RP characters in five minutes is near-certain.
2. **Activity + game guess** from the extraction ("roleplay, GTA V, NoPixel") matched against a
   world's hints.
3. **Stream title** keywords matched against world hints.
4. **Twitch category**, low weight.

Rules:

- **Hysteresis**: switching world requires ~3 consecutive agreeing windows (about 3–5 minutes),
  so one off-topic tangent does not flip the world.
- **Below threshold → unsorted.** Participant proposals from unsorted windows are *held* in the
  window's `extraction_json` (already stored today) and replayed into the world once the part is
  assigned.
- **New worlds**: a sustained activity whose game/setting guess matches no world creates a
  *provisional* world named from the guess. It is confirmed after appearing in 2 streams or by
  the owner; provisional worlds can be merged or renamed.
- **Manual override** per stream part always wins and is never re-guessed.
- `media_react`, `chatting` and `break` parts attach to no world; they read and write community,
  media and topics only.

## Community (chat) rules

- A chatter mentioned in one stream is recorded only in that stream's events. No memory entry.
- Mentions are counted per **distinct stream** (`chat_mentions`), so one spammy hour does not
  create lore.
- Appearing in **3 separate streams** (configurable) promotes the chatter to a **regular**:
  a `chat`-source entity in the community tier. Owner can promote/demote manually.
- Regulars appear in context and summaries labelled as chat, and never merge with world
  characters (a chatter named "Kael" cannot merge with RP character Kael, because entity
  resolution only matches within the same source and tier).

## Media rules

- Speech in `media_react` windows is labelled `media_audio` when the model judges it to be the
  video rather than the streamer. Those transcript segments are excluded from speaker ID and
  voiceprint enrollment, and from attribution to world characters.
- The extractor names the media item when it can ("reacting to <title> by <creator>"). Names
  inside it are stored on that media item only.
- Detection is inferred from the transcript, so it is imperfect; when unsure it leans toward
  `media_audio`, because mislabelling video speech as stream speech is the costlier error.

## Data model changes

| Change | Purpose |
|---|---|
| `worlds` (id, channel_id, name, kind, status provisional/confirmed/archived, content_profile, hints JSON, created_at) | worlds; content profile moves here from channel |
| `stream_parts` (id, session_id, t_start, t_end, activity, world_id, media_item_id, confidence, assigned_by auto/manual) | parts of a stream and their world |
| `media_items` (id, channel_id, title, creator, kind, first_session_id, stream_count, notes JSON) | media reacted to, with names inside it |
| `chat_mentions` (channel_id, handle, session_id, mentions, last_context) | distinct-stream counting before promotion |
| `entities.source`, `entities.world_id` (nullable) | source label; world for participants, null for community |
| `entities.status` gains `regular` | promoted chatters |
| `threads.world_id` (nullable), `threads.kind` (storyline / topic / bit) | world storylines vs channel topics and running bits |
| `windows.activity`, `windows.activity_conf` | per-window activity |
| `segments.origin` (stream / media / unknown) | keep video audio out of speaker ID |

All additive; existing rows keep working.

## Extraction changes

- `EntityUpdate.source` (required) and a rule: create only for `participant` with evidence; never
  for `media` or `mentioned`; `chat` only records a mention.
- New window-level `activity` object: kind, game/setting guess, media title/creator, confidence.
- `AttributionOut.kind` gains `chat_reading` and `media_audio`.
- `EventOut.type` gains `reaction` and `chat_interaction`.
- Content-profile hints are rewritten per activity rather than per channel.

## Context pack (per window)

- Streamer block (unchanged).
- Previously on: last final summaries **from the same world** for world parts; the last stream
  overall for chatting/react parts.
- Cast: streamer + world participants (relevance-ranked, as today) + community regulars.
- Threads: world storylines + channel topics and bits.
- Current media item, when reacting.
- In-stream history and rolling summary (as today).

## Summary wording

Rolling, recap and final prompts receive source labels and must keep them: media events are
described as reactions, chat as chat, mentioned people as topics. They never say a media
character or chatter "did" something in the stream's world.

## Migration

- Each channel's existing entities and threads move into one confirmed world, **"Main"**, with
  `source='participant'` and a `needs_review` flag. Existing sessions get one stream part in that
  world. Nothing is deleted.
- A one-time review endpoint lists legacy entities that look like chatters or media (seen only
  in chat-reading attributions, or only in one stream) so they can be reclassified in bulk.

## API and UI

- Worlds: list, rename, merge, confirm, archive, edit hints.
- Stream parts: view per session, override world or activity.
- Community: list regulars, promote/demote.
- Entities: change source/world.
- UI: current activity and world badge; regulars list; media item shown during reacts.

## Evaluation

- Gold sets gain activity labels, source labels and multi-stream sequences.
- New metrics: **invented-character rate** on solo/react gold (target ≈ 0), world-assignment
  accuracy per window, regular-promotion precision, media-audio labelling precision/recall.

## Phases

1. **Sources, community, media detection** under the current channel-level memory. Fixes
   invented cast for solo and react streams on its own.
2. **Worlds**: activity-based stream parts, assignment, provisional worlds, migration to "Main",
   per-world context.
3. **Refinement**: after-stream compaction of descriptions, dormant → resolved lifecycle,
   opt-in shared worlds across channels (for example several streamers on one RP server).

## Open questions for the owner

1. Is **3 separate streams** the right bar for a chat regular?
2. **Chat usernames are personal data.** Store handles as-is, or only for regulars (with
   one-off mentions kept as counts)? In `APP_MODE=public`, should regulars be shown by handle?
3. Should provisional worlds be created automatically, or suggested for the owner to confirm?
4. Should shared worlds across channels (phase 3) move earlier?
5. Is ~3–5 minutes of agreement the right delay before switching world?
