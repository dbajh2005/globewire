# GLOBEWIRE

World news on a radar around Earth. Every story is a planet:

- **Direction from Earth** shows where in the world it's happening. Sectors run eastward from the Americas through Europe, Africa, the Middle East and Asia-Pacific. Worldwide stories sit over the Pacific.
- **Distance from Earth** shows how old it is. New stories land close in and drift outward over the day.
- **Size** shows how many outlets carry it. Stories carried by 3 or more outlets get a ring.
- **A pulse** marks breaking news, meaning a story's coverage jumped since the last update.
- **Colour** shows the desk: World, Politics, Finance, Tech or Science.

Click a planet and a tractor beam pulls it into the reading panel. The panel shows each outlet's own headline for that story. The **60-second flyby** pulls in the top story from each desk against a countdown. Click a region on the rim to show only that part of the world. The **star chart** below the sky lists everything as plain text.

Stories come from about 30 RSS feeds: BBC, Guardian, Al Jazeera, DW, France 24, NPR, Politico, CNBC, MarketWatch, Ars Technica, The Verge, TechCrunch, Hacker News, ScienceDaily, NASA and more.

## The live site

`.github/workflows/publish.yml` rebuilds the site every 15 minutes and publishes it with GitHub Pages. One-time setup:

1. In this repository, open **Settings → Pages**.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.
3. Open the **Actions** tab, pick **Publish news sky**, and press **Run workflow** for the first build.

The site appears at `https://<your-username>.github.io/globewire/`.

Notes:

- GitHub sometimes starts scheduled runs a few minutes late.
- GitHub pauses scheduled workflows in a repository with no activity for 60 days. Re-enable it from the Actions tab if that happens.
- If every feed fails, the run stops and the last good version stays up.

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
