# Video Transcript Discovery — 2026-01-24

## What We Found

Drive videos have auto-generated ASR transcripts accessible via:

```
https://drive.google.com/u/0/timedtext
  ?id={internal_video_id}
  &vid={video_identifier}
  &caps=asr
  &authpayload={session_token}
  &v={internal_video_id}
  &type=track
  &lang=en
  &kind=asr
  &fmt=json3
```

## Response Format (json3)

```json
{
  "wireMagic": "pb3",
  "events": [
    {
      "tStartMs": 9200,
      "dDurationMs": 5319,
      "wWinId": 1,
      "segs": [
        { "utf8": "Hello.", "acAsrConf": 0 }
      ]
    },
    {
      "tStartMs": 10800,
      "dDurationMs": 3719,
      "segs": [
        { "utf8": "Hello." },
        { "utf8": " Hi", "tOffsetMs": 560 },
        { "utf8": " everyone.", "tOffsetMs": 719 }
      ]
    }
  ]
}
```

- `tStartMs` — start time in milliseconds
- `dDurationMs` — duration
- `segs` — text segments with optional offset
- `acAsrConf` — ASR confidence (0 = high?)

## Open Questions

1. **ID Mapping**: Drive file ID `1Pkzue1Y6zhKYI4IhY4ME21xDAZgegeRK` → internal video ID `16HUqEoJfpiAvmAf2iOPtORMQp2pIZOKB`. How?

2. **authpayload**: Session-specific token. Generated client-side. Needed in addition to cookies.

3. **Redirect flow**: First request (302) may provide authpayload, second (200) uses it.

4. **Gemini Summary**: Different endpoint entirely — the summary panel uses a separate API, not `/timedtext`.

## Test Video

- File: "Smartphone BLP Debrief - 2025_11_24 13_56 GMT – Recording.mp4"
- Drive ID: `1Pkzue1Y6zhKYI4IhY4ME21xDAZgegeRK`
- Internal ID: `16HUqEoJfpiAvmAf2iOPtORMQp2pIZOKB`
- Duration: 72 minutes (4,357,124ms)
