# broll-fetcher

Free B-roll for your video script. Give it your `scenes.json` timeline and it downloads a matching
**real** video clip or photo for every stock scene from free sources:

| Source | What | Key needed? | Credit needed? |
|---|---|---|---|
| [Pexels](https://www.pexels.com) | videos + photos | free key | no (appreciated) |
| [Pixabay](https://pixabay.com) | videos + photos | free key | no (appreciated) |
| [Wikimedia Commons](https://commons.wikimedia.org) | photos of real people, places, events | no | **yes** (most files) |
| [Openverse](https://openverse.org) | Creative Commons photos | no | **yes** (except CC0 / public domain) |

It then writes:

- `media/stock/s01_pexels.mp4`, `media/stock/s02_wikimedia.jpg`, ... (one file per stock scene)
- `media/stock/manifest.json` - for every scene: file, source, page URL, author, license, size, duration
- `media/stock/CREDITS.md` - ready-made credit lines to paste into your YouTube description
- `scenes.with_media.json` - your scenes, with a `media_path` added to each stock scene, so HyperFrames/Codex can use it directly

100% free, MIT licensed, works on Windows, macOS and Linux (Python 3.10+).

---

## 1. Get the two free API keys (5 minutes)

Wikimedia and Openverse work with no key. For video clips you want Pexels and Pixabay:

**Pexels**
1. Make a free account at https://www.pexels.com/join
2. Go to https://www.pexels.com/api/ and click **Your API Key** / **Get Started**
3. Answer the short questions about your project (e.g. "B-roll for my YouTube videos") and copy the key

**Pixabay**
1. Make a free account at https://pixabay.com/accounts/register/
2. Open https://pixabay.com/api/docs/ while logged in - your personal key is shown on that page in the
   **Parameters** section, next to `key`

## 2. Setup

Install Python 3.10 or newer from https://www.python.org/downloads/ (on Windows tick **"Add python.exe to PATH"**).

```bash
git clone https://github.com/<you>/broll-fetcher.git
cd broll-fetcher

python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

Copy `.env.example` to `.env` and paste your keys:

```
PEXELS_API_KEY=your_pexels_key
PIXABAY_API_KEY=your_pixabay_key
BROLL_CONTACT=you@example.com
```

(Windows: `copy .env.example .env` then open `.env` in Notepad.)

## 3. Run it

```bash
# See what it would pick, download nothing:
python fetch.py scenes.json --dry-run

# Download everything:
python fetch.py scenes.json --out media/stock

# Only some sources (tried in the order you write them):
python fetch.py scenes.json --sources pexels,pixabay
python fetch.py scenes.json --sources wikimedia,openverse      # no keys needed
```

Example output (a real run of the included `scenes.json` with no keys, so `s01` falls back to a photo;
with a Pexels key it gets a video clip):

```
broll-fetcher 1.0.0: 4 scenes, 3 stock scenes to fetch
[s01] video for "city skyline night", needs 6.4s
    no video found - looking for a still image instead (animate it with a slow zoom)
    -> wikimedia #142949336 (1920x1080, CC BY 4.0) by David Kernan
    saved media/stock/s01_wikimedia.jpg (0.4 MB)
[s02] image for "Eiffel Tower", needs 6.6s
    -> wikimedia #21375035 (1920x1280, CC BY-SA 3.0) by Jorge Royan
    saved media/stock/s02_wikimedia.jpg (1.4 MB)
[s03] image for "Neil Armstrong Moon" (from narration), needs 6.5s
    -> wikimedia #6449925 (1920x1185, Public domain) by NASA
    saved media/stock/s03_wikimedia.jpg (0.7 MB)
done in 3s: 3 ready, 0 missing
```

Run it again any time: files that are already downloaded are kept (use `--force` to pick again), and
searches are cached for 24 hours in `.cache/`.

All options: `python fetch.py --help`

| Option | Meaning |
|---|---|
| `--out media/stock` | where files go |
| `--sources a,b,c` | which sources, in order (default `pexels,pixabay,wikimedia,openverse`) |
| `--dry-run` | list the picks only |
| `--scenes-out file.json` | where to write the scenes + media paths (default `scenes.with_media.json`) |
| `--force` | re-pick and re-download everything |
| `--no-cache` | ignore the 24h search cache |
| `--quiet` | less output |

Exit code is `0` when every stock scene got a file and `1` when some are missing (they're listed at the end).

## 4. The scenes.json format

A list of scenes (or `{"scenes": [...]}`):

```json
{"id": "s01", "start": 0.0, "end": 6.4,
 "narration": "Every night, millions of lights switch on...",
 "visual_type": "stock_video",
 "stock_query": "city skyline night",
 "orientation": "landscape",
 "min_duration": 5}
```

- Only `stock_video` and `stock_image` scenes are fetched. `ai_video`, `ai_image`, `motion_graphic` are skipped.
- No `stock_query`? It makes one from the narration (keeps names like "Neil Armstrong" together). Short,
  concrete queries work best: `"rain on window"`, not `"the feeling of sadness in the city"`.
- `orientation`: `landscape` (16:9, default), `portrait` (9:16 for Shorts) or `square`.
- The clip should last at least `end - start` seconds (or `min_duration` if bigger).

**How it picks:** for each scene it asks the sources in order and scores the results: right shape (16:9 for
landscape), 1080p (it never downloads 4K, to save space), clip long enough for the scene, high in the
source's own ranking, and never the same clip twice in one video. If no video exists for a `stock_video`
scene, it takes a photo instead and marks it `"fallback": "image_instead_of_video"` - put a slow zoom on it.

## 5. How it plugs into your HyperFrames workflow (step 8)

```
step 6  Codex builds scenes.json from your voiceover timings
step 7  you make the AI images/clips  -> media/ai/
step 8  python fetch.py scenes.json --out media/stock      <- this tool
step 9  Codex + HyperFrames assemble the video
```

Then tell Codex in step 9:

> Read `scenes.with_media.json`. For every scene, place the file in `media_path` on the timeline from
> `start` to `end` (images: slow Ken Burns zoom; videos: trim to the scene length, loop if too short).
> AI scenes use the files in `media/ai/` with the same scene id. Add the lines from
> `media/stock/CREDITS.md` to `description.txt`.

## 6. Licenses - read this before you publish

- **Pexels** and **Pixabay**: free for YouTube, including monetized channels, no credit required.
  Don't re-sell the clips as-is and don't use recognizable people in a way that suggests they endorse something.
  ([Pexels license](https://www.pexels.com/license/), [Pixabay license](https://pixabay.com/service/license-summary/))
- **Wikimedia Commons** and **Openverse**: every file has its own license (CC BY, CC BY-SA, CC0, public domain...).
  CC BY / BY-SA **require a credit** (author, license, link) - that's what `CREDITS.md` is for. Open the source
  page of each file once to confirm the license and look for extra restrictions (trademarks, personality rights).
  Openverse searches are limited to licenses that allow commercial use and editing (CC BY, BY-SA, CC0, PDM).
- This tool can't guarantee a license is correct - the uploader sets it. When in doubt, use Pexels/Pixabay.

## 7. Limits and being polite

- Pexels: 200 requests/hour. Pixabay: 100 requests/minute and asks you to cache results for 24 hours. The tool
  spaces out calls, retries politely on errors, and caches every search for 24h in `.cache/broll/`.
- One search is usually 1 request, so a 150-scene video uses roughly 150-300 requests. If Pexels runs out it
  moves on to Pixabay automatically; run it again an hour later for the rest.
- Pixabay photos come at max 1280px wide (their free API size). Pexels/Wikimedia give 1920px.
- If Pillow is installed (it's in requirements.txt), huge photos are shrunk to 1920px automatically.

## 8. Tests

```bash
python -m unittest discover -s tests -v
```

The tests fake Pexels and Pixabay, so they run without keys or internet.

## Similar projects

- [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) (MIT) - full "topic to short video" app;
  it searches Pexels/Pixabay for clips using AI-written keywords. Great all-in-one, but it builds its own video
  and doesn't read your own scene timeline.
- [free-ai-video-generator](https://github.com/bodrumundenizi-beep/free-ai-video-generator) (MIT) - Windows app,
  script to YouTube Short with Pexels/Pixabay footage.

broll-fetcher does one job: your `scenes.json` in, matching free media + credits out.

## License

MIT - see [LICENSE](LICENSE).
