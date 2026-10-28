# GLOBEWIRE and TREMOR

Two ways to read the world's news from the same feeds:

- **GLOBEWIRE** (`/`): every story as a planet on a radar around Earth.
- **TREMOR** (`/tremor/`): a seismograph of ongoing situations, showing what moved since you last looked.

## GLOBEWIRE

World news on a radar around Earth. Every story is a planet:

- **Direction from Earth** shows where in the world it's happening. Sectors run eastward from the Americas through Europe, Africa, the Middle East and Asia-Pacific. Worldwide stories sit over the Pacific.
- **Distance from Earth** shows how old it is. New stories land close in and drift outward over the day.
- **Size** shows how many outlets carry it. Stories carried by 3 or more outlets get a ring.
- **A pulse** marks a story that is spreading fast: at least two more outlets picked it up in the last update. The label disappears once the count falls back.
- **Outlets** are counted once each, so BBC World and BBC Asia carrying the same story count as one.
- **Colour** shows the desk: World, Politics, Finance, Tech or Science.

Click a planet and a tractor beam pulls it into the reading panel. The panel shows each outlet's own headline for that story. The **60-second flyby** pulls in the top story from each desk against a countdown. Click a region on the rim to show only that part of the world. The **star chart** below the sky lists everything as plain text.

Stories come from about 40 RSS feeds from 25 outlets:

- **International:** BBC, Guardian, Al Jazeera, DW, France 24, NPR, Sky News, CBS News, Euronews
- **Based in the regions they cover:** South China Morning Post, CNA (Singapore), The Hindu (India), ABC Australia, Africanews, The Jerusalem Post, The National (UAE), MercoPress (South America)
- **Politics, finance, tech, science:** Politico, CNBC, MarketWatch, Ars Technica, The Verge, TechCrunch, Hacker News, ScienceDaily, NASA

Headlines from different outlets are matched into one story when they share enough wording, or when outlets in the same place share uncommon names (Lula, Storm Boelo) and some wording. Each run on the Actions tab lists every feed's count, or its error, under **Annotations**.

## The live site

`.github/workflows/publish.yml` rebuilds the site every 15 minutes and publishes it with GitHub Pages. One-time setup:

1. In this repository, open **Settings → Pages**.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.
3. Open the **Actions** tab, pick **Publish news sky**, and press **Run workflow** for the first build.

The site appears at `https://<your-username>.github.io/globewire/`.

Notes:

- GitHub treats schedules as best-effort and often runs them late, so the 15-minute updates come from an outside timer (below). GitHub's own schedule runs hourly as a backup.
- GitHub pauses scheduled workflows in a repository with no commits for 60 days. The keep-alive below prevents that; if it ever happens anyway, use **Enable workflow** on the Actions tab.
- If every feed fails, the run stops and the last good version stays up.
- `.github/workflows/keepalive.yml` makes a one-line heartbeat commit once a month if nothing else was committed in the last 25 days, so GitHub never pauses the schedule.

### Reliable 15-minute updates with an outside timer

A free scheduler such as [cron-job.org](https://cron-job.org) calls GitHub's API every 15 minutes to start **Publish news sky**.

1. On GitHub, go to **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**. Limit it to this repository and give it **Actions: Read and write** and nothing else.
2. On cron-job.org, create a job that sends `POST` to `https://api.github.com/repos/<owner>/globewire/actions/workflows/publish.yml/dispatches` every 15 minutes, with the body `{"ref":"main"}` and these headers:
   - `Authorization: Bearer <your token>`
   - `Accept: application/vnd.github+json`
   - `X-GitHub-Api-Version: 2022-11-28`
   - `Content-Type: application/json`
3. A test run should return status `204`, and a new run appears on the Actions tab.

The token can only start and manage workflow runs in this one repository. When it expires, create a new one and paste it into the job.

## Run it on your own computer

Needs Python 3.9+. No installs.

```
python3 globewire.py                    # opens http://localhost:8017 and refreshes every 10 minutes
python3 globewire.py --brief            # 60-second brief in the terminal
python3 globewire.py --export news.json # save a "tape" to load into a copy of the page
```

Keep `globewire.html` next to `globewire.py`.

## Adding a feed or a place

- **Feeds:** add a line to `FEEDS` in `globewire.py` with the name, category (or `None` to auto-sort), default region and URL.
- **Places:** where a story happens comes from `PLACES`. Each entry has a name, region, longitude and keywords. Join multi-word keywords with an underscore (`white_house`). Start a keyword with `=` to match its exact capitalisation (`=US`).

## TREMOR

TREMOR groups developments into situations, meaning ongoing stories followed over days, and draws each one as a seismograph trace.

- **Situations, not articles.** A follow-up story joins its situation when its headlines share enough distinctive words with it, or share a name that runs through the situation (Storm Boelo, Lula). Two situations that turn out to be the same story are joined, and links to either still work.
- **Developments, not headlines.** Other outlets' headlines on the same development, within 6 hours, are counted as one development carried by more outlets, so a big story looks big.
- **Since you left.** Your browser remembers your last visit, and the blue line marks it. "The minute" lists only what moved since then, biggest first, and can read it aloud.
- **Magnitude** combines how many independent outlets carry a development, how fast it spread, and whether it moved an existing situation. M4 and up shows in orange.
- **Next tremors** are dates mentioned in the reporting that are still ahead.
- **Blind spots** plots people affected (numbers taken from the reporting) against coverage.

`tremor.py` runs after `globewire.py` in the workflow. A situation with follow-ups is kept until it has been quiet for 14 days; a one-off story is dropped after 3 days. Run it locally with `python3 tremor.py` and open http://localhost:8018.

Each run on the Actions tab shows a short TREMOR health report under **Annotations**: how much history it holds, how many situations it has followed across days, its biggest situations, and the headlines grouped under the top four.

### Optional: AI summaries

With an API key, Claude writes each situation's name, a one-line state, what it was before, claims with the outlets that confirm or dispute them, and how the outlets frame it. Without a key TREMOR uses the headlines as they are.

1. Create a key at https://console.anthropic.com.
2. In this repository, go to **Settings → Secrets and variables → Actions → New repository secret**. Name it `ANTHROPIC_API_KEY`.
3. Optional: add a repository **variable** `TREMOR_MODEL` to choose the model. The default is `claude-opus-5-5`. `claude-haiku-5-5` costs far less.

Summaries are written only for situations with a new development of magnitude 4 or more. That's at most 4 per run, and each situation is summarized at most once every 3 hours. You can change these limits with the `TREMOR_AI_MAX` and `TREMOR_AI_EVERY_HOURS` environment variables.
