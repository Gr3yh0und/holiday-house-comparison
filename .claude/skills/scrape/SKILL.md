---
name: scrape
description: Start a fresh scrape of all houses into cache/houses.json and report progress at 25/50/75/100 %. Use when the user asks to "scrape", "refresh the prices", "get new data", or "update the cache". Does not deploy — run /deploy afterwards for that.
---

# Scrape

Runs `app.py --scrape-only` in the background and reports how far it is. A full run takes
about 8 minutes (fewo-direkt.de needs 20–45 s cooldowns between houses). It only writes
`cache/houses.json` (plus the sled run / loipen caches); nothing is published. The scraper
replaces `cache/houses.json` only when the run succeeds, so stopping it is safe.

Arguments the user may give, passed through to `app.py`: `--broker fewo|booking|huetten|interhome`,
`--limit N`, `--force` (re-fetch sled runs too). `--broker`/`--limit` results are merged into the
existing `cache/houses.json`; the other houses keep their last data. `--house NAME` re-scrapes one house and patches
the cache; it prints no progress lines, so just run it in the foreground instead.

## 1. Check that no scrape is already running

```bash
pgrep -af "app.py --scrape-only" || echo "none running"
```

If one runs, tell the user and stop — two runs fight over the same cache files and the
same browser fingerprint.

## 2. Start it in the background

From the repo root. `xvfb-run` gives Chrome a virtual screen when there is no `$DISPLAY`
(SSH, server); headless Chrome gets blocked by fewo-direkt.de. `-u` keeps the log unbuffered.

```bash
LOG=cache/scrape.log
if [ -n "$DISPLAY" ]; then RUN=""; else RUN="xvfb-run -a"; fi
nohup bash -c "$RUN .venv/bin/python -u app.py --scrape-only $ARGS > $LOG 2>&1; echo \"[scrape-exit] \$?\" >> $LOG" >/dev/null 2>&1 &
echo started
```

(`$ARGS` = the pass-through arguments above, or empty.) Tell the user it started, how many
houses (the first `[progress] 0/N` line in the log), and that you will report at 25/50/75 %.

## 3. Watch it with Monitor

Start a **Monitor** (`timeout_ms` 1800000, description "scrape progress") with this command.
It emits one line per milestone, plus every failure signal, and exits when the run ends:

```bash
tail -n +1 -F cache/scrape.log 2>/dev/null | awk '
  /^\[progress\]/ {
    match($0, /\(([0-9]+)%\)/, m); p = m[1] + 0
    while (next_ms <= 75 && p >= next_ms + 25) { next_ms += 25; print "PROGRESS " next_ms "% — " $2 " houses"; fflush() }
    next
  }
  /bot\/rate-limit page|scrape returned no data|error scraping|Traceback|ERROR/ { print "PROBLEM " $0; fflush(); next }
  /Data saved to cache\/houses.json/ { print "SAVED " $0; fflush(); next }
  /^\[scrape-exit\]/ { print "EXIT " $2; fflush(); exit }
  BEGIN { next_ms = 0 }
'
```

On each event, tell the user in one short line:

- `PROGRESS 25%` / `50%` / `75%` → "Scrape 50 % done (9/18 houses)."
- `PROBLEM ...` → name the house (the `Scraping house:` line above it in `cache/scrape.log`) and
  the problem. One bot page is normal noise; several in a row means the IP or browser is
  flagged — say so, and suggest stopping (`pkill -f "app.py --scrape-only"`) and retrying later.
- `EXIT 0` → go to step 4. `EXIT` non-zero → show the last 20 lines of `cache/scrape.log`.

If the Monitor expires before `EXIT`, check `pgrep -af "app.py --scrape-only"` and re-arm it.

## 4. Report the result

```bash
.venv/bin/python - <<'EOF'
import json
d = json.load(open('cache/houses.json'))
print(d['updated_at'], 'status', d['status'])
for t in d['trips']:
    for h in t['houses']:
        print(f"{t['name'][:12]:12} {h['name'][:32]:32} {str(h['price']):10} {h['time']}")
EOF
```

Report 100 %, the status (`ok`, or `degraded` = some houses failed), how many houses are
`Available` with a price vs. `Unavailable` (booked out) vs. `check_manually` (huetten.com and
houses without `house_url` — no availability data), and any house with no data.
The log line `booked out, but input.json sets price` names houses where an old hand-typed
`price` in `input.json` overrides the scrape — list them, the user may want to delete those lines. Then:
the site is not updated yet — `/deploy` publishes it.
