# Internship Tracker

My personal internship finder for software and other tech roles. It keeps checking thousands of company job boards, publishes open internships to a searchable table sorted by the date each employer posted them, and emails me when new ones match my preferences.

It's built to run entirely on GitHub for free, so my computer can stay off.

## How it works

1. A GitHub Actions job ([scrape.yml](.github/workflows/scrape.yml)) scans in a loop for about 5.5 hours. The schedule queues the next run, which starts when that one ends.
2. Each cycle checks every board. Boards that haven't changed answer almost instantly, so only real changes cost any time.
3. It reads the public job APIs of Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Workday, Oracle and Amazon.
4. Every cycle it also checks [SimplifyJobs](https://github.com/SimplifyJobs/Summer2027-Internships) and [vanshb03](https://github.com/vanshb03/Summer2027-Internships) for new commits. They add roles from systems without an open API, and their links reveal new company boards to scan automatically.
5. Listings are saved to the `data` branch. The page ([site/index.html](site/index.html)) is published with GitHub Pages and refreshes itself about every 75 seconds.
6. New matches open a GitHub issue that mentions me, which GitHub sends as an email.

Roles disappear once the employer takes them down or their season has passed. If a board can't be read, its roles stay until the next successful read.

The Posted column shows the employer's own date. Roles from the community lists show the date they were added to the list, which can be later than the real posting.

## My preferences

Edit [config.json](config.json) on GitHub or locally. The next run uses the changes.

| Setting | What it does |
| --- | --- |
| `tracks` | Which kinds of role to keep at all. Remove one to hide it everywhere |
| `alerts.enabled` | Turns alerts on or off |
| `alerts.every_minutes` | The shortest gap between alert emails |
| `alerts.regions` | Any of `us`, `canada`, `remote`, `other`. Empty means all |
| `alerts.tracks` | Any of `Software`, `AI/ML`, `Data`, `Hardware`, `Quant`, `Product`, `Security`, `Design`, `Business`, `Other`. Empty means all |
| `alerts.keywords` | Only alert on roles containing one of these words. Empty means all |
| `alerts.exclude_keywords` | Never alert on roles containing these words |
| `alerts.max_age_days` | Skip roles posted longer ago than this |
| `watch_companies` | Always alert for these companies, ignoring the alert filters |
| `exclude_companies` | Hide these companies everywhere |
| `extra_boards` | Add a board by hand, such as `{"ats": "greenhouse", "slug": "stripe", "company": "Stripe"}` |

The Big tech and AI lab highlights come from the `BIG_TECH` and `AI_LABS` lists in [site/index.html](site/index.html). Names must match exactly, so add a company there to highlight it.

The Saved and Applied marks on the page are stored in my browser, so they don't sync between devices.

## Running it locally

Needs Python 3.9 or newer, and nothing else.

```bash
python3 -m tracker
```

```bash
python3 -m unittest discover -s tests -t .
```

To see the page after a scan, serve it and open http://localhost:8123. Opening `index.html` directly won't load any listings.

```bash
mkdir -p _site && cp site/* data/jobs.json data/meta.json _site/ && python3 -m http.server 8123 --directory _site
```
