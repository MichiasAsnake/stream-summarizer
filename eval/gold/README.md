# Gold-file format

Pass a JSON file with any of these fields to `python -m eval.runner --gold FILE`:

```json
{
  "transcript": "Reference transcript text.",
  "events": ["Morgan reveals the plan"],
  "entities": ["Morgan"],
  "threads": ["The warehouse plan"],
  "summary_facts": ["Morgan revealed the plan"]
}
```

The runner uses a fresh temporary database by default and exercises the same
ingest, VAD, ASR, windowing, extraction, memory, rolling-summary, and recap
code used by the application. Use `--db-url` only when the evaluation records
need to be retained for inspection.
