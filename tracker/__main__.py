"""Scan job boards, update the listings, and send alerts.

With no flags this does one scan. With --watch it keeps scanning, checking every board
each cycle. Boards that haven't changed answer almost instantly, so cycles stay short.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .classify import is_past, regions_for, term_for, track_for
from .discover import parse_board, posting_key
from .net import FetchError, Gone, Unchanged, fetch_json
from .logos import fill_domains
from .sources import (BUILT_IN_BOARDS, COMMUNITY_LISTS, FETCHERS, iso, job, latest_commit, read_detail,
                      read_listings)

# Bump this when filtering changes, so boards get re-read instead of reporting "unchanged".
SCAN_VERSION = 3
WORKERS = 40
CYCLE_BUDGET_SECONDS = 15 * 60
MIN_CYCLE_SECONDS = 60
# GitHub allows few API calls without a token, so check less often when running locally.
GITHUB_TOKEN = os.environ.get("GH_TOKEN")
COMMUNITY_EVERY_SECONDS = 0 if GITHUB_TOKEN else 10 * 60
HEARTBEAT_SECONDS = 10 * 60
DETAIL_LOOKUPS_PER_CYCLE = 200
DROP_BOARD_AFTER_404S = 10
FORGET_SEEN_AFTER_DAYS = 365
MIN_HEALTHY_RATIO = 0.5
RECENT_DAYS = 2
UNDATED_FIRST_RUN_DAYS = 30
LEAD_WINDOW_DAYS = 7
ALERT_LIMIT = 100


def format_time(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso():
    return format_time(datetime.now(timezone.utc))


def parse_time(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def load(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def save(path, value):
    temporary = Path(str(path) + ".tmp")
    temporary.write_text(json.dumps(value, separators=(",", ":"), ensure_ascii=False))
    temporary.replace(path)


def normalize(text):
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def job_id(company, title):
    key = normalize(company) + "|" + normalize(title)
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def board_key(ats, slug):
    return "%s:%s" % (ats, slug.lower())


def read_community(commits):
    """Fetch community lists with new commits. Returns ({name: raw items}, failed names).

    `commits` remembers the last commit read from each list, so unchanged lists cost
    one tiny request.
    """
    lists, failed = {}, set()
    for name, (repository, branch) in COMMUNITY_LISTS.items():
        try:
            commit = latest_commit(repository, branch, GITHUB_TOKEN)
            if commit != commits.get(name):
                lists[name] = read_listings(repository, commit)
                commits[name] = commit
        except (FetchError, KeyError, TypeError) as error:
            print("community list failed: %s" % error, file=sys.stderr)
            failed.add(name)
    return lists, failed


def update_boards(boards, lists, extra_boards):
    """Add every board we can work out from community links or the config."""
    for items in lists.values():
        for item in items:
            found = parse_board(item.get("url") or "")
            if found:
                key = board_key(*found)
                if key not in boards:
                    boards[key] = {"ats": found[0], "slug": found[1],
                                   "company": item.get("company_name") or found[1], "misses": 0}
    for extra in extra_boards:
        key = board_key(extra["ats"], extra["slug"])
        boards.setdefault(key, {"ats": extra["ats"], "slug": extra["slug"],
                                "company": extra.get("company") or extra["slug"], "misses": 0})


def scrape_boards(boards, keys):
    """Read the given boards. Returns (jobs, keys that failed, keys that hadn't changed)."""
    jobs, failed, unchanged = [], set(), set()
    started = time.monotonic()

    def read(key):
        if time.monotonic() - started > CYCLE_BUDGET_SECONDS:
            raise FetchError("out of time")
        return FETCHERS[boards[key]["ats"]](boards[key])

    with ThreadPoolExecutor(WORKERS) as pool:
        futures = {pool.submit(read, key): key for key in keys}
        for future in as_completed(futures):
            key = futures[future]
            try:
                found = future.result()
            except Unchanged:
                boards[key]["misses"] = 0
                unchanged.add(key)
            except Gone:
                boards[key]["misses"] = boards[key].get("misses", 0) + 1
                failed.add(key)
            except Exception as error:  # One odd board must never stop the others.
                # Its ETag may be from a response we couldn't use, so don't trust it.
                boards[key].pop("etag", None)
                failed.add(key)
                print("failed %s: %r" % (key, error), file=sys.stderr)
            else:
                boards[key]["misses"] = 0
                for item in found:
                    item["board"] = key
                jobs.extend(found)
    return jobs, failed, unchanged


def community_jobs(lists):
    jobs = []
    for name, items in lists.items():
        for item in items:
            if not item.get("active") or not item.get("is_visible", True):
                continue
            if not item.get("title") or not item.get("url"):
                continue
            found = job(item["title"], item.get("company_name") or "Unknown",
                        list(item.get("locations") or []), item["url"], iso(item.get("date_posted")))
            found["board"] = "community:" + name
            found["category"] = item.get("category")
            found["terms"] = item.get("terms")
            jobs.append(found)
    return jobs


def first_seen_for(posted, now, first_run):
    """When a job counts as found. Old postings aren't new finds, even on a new board."""
    current = parse_time(now)
    if posted and parse_time(posted) < current - timedelta(days=RECENT_DAYS):
        return posted
    if first_run:
        return posted or format_time(current - timedelta(days=UNDATED_FIRST_RUN_DAYS))
    return now


def loose_key(raw):
    """Company and title without the tags lists add or remove, like "[Summer 2027]"."""
    title = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", raw["title"])
    title = re.sub(r"\b(spring|summer|fall|autumn|winter)\b|\b20\d\d\b", " ", title, flags=re.I)
    return normalize(raw["company"]) + "|" + normalize(title)


def without_list_copies(raw_jobs):
    """Drop list entries for postings we read from the employer.

    The employer's copy has the true date; a list's date is when someone added it.
    Some lists use short links with no posting ID, so titles are compared as well.
    """
    employer_jobs = [raw for raw in raw_jobs if not raw["board"].startswith("community:")]
    links = {posting_key(raw["url"]) for raw in employer_jobs} - {None}
    titles = {loose_key(raw) for raw in employer_jobs}
    return [raw for raw in raw_jobs
            if not raw["board"].startswith("community:")
            or (posting_key(raw["url"]) not in links and loose_key(raw) not in titles)]


def merge(raw_jobs, seen, now, excluded, tracks=None, first_run=False):
    """Collapse one role listed in several places, and fill in the derived fields."""
    merged = {}
    for raw in without_list_copies(raw_jobs):
        if normalize(raw["company"]) in excluded:
            continue
        key = job_id(raw["company"], raw["title"])
        current = merged.get(key)
        if current is None:
            merged[key] = dict(raw, id=key, location=list(raw["location"]))
            continue
        for place in raw["location"]:
            if place not in current["location"]:
                current["location"].append(place)
        if raw["posted"] and (not current["posted"] or raw["posted"] < current["posted"]):
            current["posted"] = raw["posted"]
        current["pay"] = current.get("pay") or raw.get("pay")
        current["detail"] = current.get("detail") or raw.get("detail")

    results = []
    today = parse_time(now).date()
    for item in merged.values():
        track = item.get("track") or track_for(item["title"], item.get("category"))
        if tracks and track not in tracks:
            continue
        term = item.get("term") or term_for(item["title"], item.get("terms"))
        if is_past(item["title"], term, today):
            continue
        if item["id"] not in seen:
            seen[item["id"]] = first_seen_for(item["posted"], now, first_run)
        results.append({
            "id": item["id"],
            "company": item["company"],
            "title": item["title"],
            "location": item["location"],
            "regions": regions_for(item["location"]),
            "track": track,
            "term": term,
            "pay": item.get("pay"),
            "detail": item.get("detail"),
            "url": item["url"],
            "board": item["board"],
            "posted": item["posted"],
            "first_seen": seen[item["id"]],
        })
    sort_by_posted(results)
    return results


def sort_by_posted(jobs):
    """Newest posting first; roles with no known date go last."""
    jobs.sort(key=lambda item: (item["posted"] or "", item["first_seen"]), reverse=True)


def fill_details(jobs, details):
    """Read job pages a batch at a time, missing dates first, and attach what's known."""
    todo = [item for item in jobs
            if item["detail"] and item["id"] not in details and not (item["pay"] and item["posted"])]
    todo.sort(key=lambda item: bool(item["posted"]))
    with ThreadPoolExecutor(8) as pool:
        futures = {pool.submit(read_detail, item["detail"]): item
                   for item in todo[:DETAIL_LOOKUPS_PER_CYCLE]}
        for future in as_completed(futures):
            try:
                details[futures[future]["id"]] = future.result()
            except Exception as error:  # Try again on a later cycle.
                print("detail failed: %r" % error, file=sys.stderr)
    for item in jobs:
        known = details.get(item["id"]) or {}
        item["pay"] = item["pay"] or known.get("pay")
        item["posted"] = item["posted"] or known.get("posted")
        del item["detail"]
    sort_by_posted(jobs)


def lead_over_lists(lists, seen, now, tracking_since):
    """Compare when we found recent roles with when community lists added them.

    Only roles listed after tracking began count; earlier find times are back-filled.
    """
    cutoff = max(parse_time(now) - timedelta(days=LEAD_WINDOW_DAYS), parse_time(tracking_since))
    leads = []
    for items in lists.values():
        for item in items:
            listed = iso(item.get("date_posted"))
            found = seen.get(job_id(item.get("company_name"), item.get("title")))
            if listed and found and parse_time(listed) >= cutoff:
                leads.append((parse_time(listed) - parse_time(found)).total_seconds() / 60)
    if not leads:
        return None
    return {"ahead": sum(1 for lead in leads if lead > 0), "total": len(leads),
            "median_minutes": round(statistics.median(leads))}


def wanted(item, alerts, watched):
    """Does this job match the alert preferences in config.json?"""
    if normalize(item["company"]) in watched:
        return True
    text = ("%s %s" % (item["title"], item["company"])).lower()
    if any(word.lower() in text for word in alerts.get("exclude_keywords") or []):
        return False
    regions = alerts.get("regions") or []
    if regions and not set(regions) & set(item["regions"]):
        return False
    tracks = alerts.get("tracks") or []
    if tracks and item["track"] not in tracks:
        return False
    keywords = alerts.get("keywords") or []
    return not keywords or any(word.lower() in text for word in keywords)


def clean_for_markdown(text):
    """Scraped text must not create mentions, links or table cells in the alert."""
    return re.sub(r"[@\[\]()|<>`*_]", " ", text or "").strip()


def alert_body(items, site_url):
    lines = ["| Company | Role | Location | Pay |", "| --- | --- | --- | --- |"]
    for item in items[:ALERT_LIMIT]:
        lines.append("| %s | [%s](%s) | %s | %s |" % (
            clean_for_markdown(item["company"]),
            clean_for_markdown(item["title"]),
            item["url"].replace(")", "%29").replace(" ", "%20"),
            clean_for_markdown(", ".join(item["location"][:2])) or "Not listed",
            clean_for_markdown(item.get("pay")) or "Not listed"))
    if len(items) > ALERT_LIMIT:
        lines.append("\nPlus %d more." % (len(items) - ALERT_LIMIT))
    if site_url:
        lines.append("\n[See everything](%s)" % site_url)
    return "\n".join(lines)


def choose_alert_jobs(jobs, meta, config, now):
    """Return the jobs to alert on now; alerts go out at most every N minutes."""
    alerts = config.get("alerts") or {}
    last = meta.get("last_alert")
    if not alerts.get("enabled", True):
        return []
    if not last:
        # The first run sees everything as new, so it only sets the starting point.
        meta["last_alert"] = now
        return []
    current = parse_time(now)
    if current - parse_time(last) < timedelta(minutes=alerts.get("every_minutes", 15)):
        return []
    # A newly found board can surface old jobs, so ignore anything long since posted.
    oldest = current - timedelta(days=alerts.get("max_age_days", 14))
    watched = {normalize(name) for name in config.get("watch_companies") or []}
    fresh = [
        item for item in jobs
        if item["first_seen"] > last
        and (not item["posted"] or parse_time(item["posted"]) >= oldest)
        and wanted(item, alerts, watched)
    ]
    if fresh:
        meta["last_alert"] = now
    return fresh


def send_alert(items):
    """Open and close a GitHub issue that mentions the owner, which GitHub emails."""
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER")
    if not owner:
        print("alert (not sent outside GitHub): %d jobs" % len(items))
        return True
    body = "@%s\n\n%s" % (owner, alert_body(items, os.environ.get("SITE_URL", "")))
    title = "%d new internship%s" % (len(items), "" if len(items) == 1 else "s")
    try:
        result = subprocess.run(["gh", "issue", "create", "--title", title, "--body", body],
                                capture_output=True, text=True, check=True, timeout=60)
        subprocess.run(["gh", "issue", "close", result.stdout.strip()],
                       capture_output=True, check=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        print("alert failed: %r" % error, file=sys.stderr)
        return False
    return True


def publish(data):
    """Replace the data branch with one fresh commit, so history never grows."""
    remote = os.environ.get("DATA_REMOTE")
    if not remote:
        return
    shutil.rmtree(data / ".git", ignore_errors=True)
    identity = ["-c", "user.name=github-actions[bot]",
                "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com"]
    for command in (["init", "-q", "-b", "data"], ["add", "-A"],
                    identity + ["commit", "-q", "-m", "Update listings"],
                    ["push", "-q", "--force", remote, "data"]):
        try:
            subprocess.run(["git"] + command, cwd=str(data), check=True, timeout=120,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            # The error text would include the token, so only report the step.
            print("publish failed at: git %s" % command[-1], file=sys.stderr)
            return


class Scanner:
    def __init__(self, data, config):
        self.data = data
        self.config = config
        data.mkdir(parents=True, exist_ok=True)
        self.boards = load(data / "boards.json", {})
        self.seen = load(data / "seen.json", {})
        self.details = load(data / "details.json", {})
        self.domains = load(data / "domains.json", {})
        self.meta = load(data / "meta.json", {})
        self.jobs = load(data / "jobs.json", [])
        self.first_run = not self.seen
        self.community = {}
        self.community_at = 0
        self.list_commits = {}
        self.published_at = 0
        self.forget_board_state_if_stale()

    def forget_board_state_if_stale(self):
        """"Unchanged" only means something if our listings came from the same filters."""
        signature = json.dumps([SCAN_VERSION, self.config.get("tracks")])
        if self.jobs and self.meta.get("signature") == signature:
            return
        self.meta["signature"] = signature
        for board in self.boards.values():
            for field in ("etag", "totals", "checks"):
                board.pop(field, None)

    def live_boards(self):
        """Every board that still exists, shuffled so no one host gets them all at once."""
        keys = [key for key, board in self.boards.items()
                if board.get("misses", 0) < DROP_BOARD_AFTER_404S]
        random.shuffle(keys)
        return keys

    def cycle(self):
        started = time.monotonic()
        now = now_iso()
        config = self.config

        lists, failed_lists = {}, set()
        if not self.community_at or time.monotonic() - self.community_at >= COMMUNITY_EVERY_SECONDS:
            lists, failed_lists = read_community(self.list_commits)
            self.community.update(lists)
            self.community_at = time.monotonic()
            update_boards(self.boards, lists, BUILT_IN_BOARDS + (config.get("extra_boards") or []))
        keys = self.live_boards()
        direct, failed, unchanged = scrape_boards(self.boards, keys)

        # Roles on boards that didn't change, or couldn't be read, are still open as far as we know.
        retained = failed | unchanged
        community_names = {"community:" + name for name in COMMUNITY_LISTS}
        fresh_community = {"community:" + name for name in lists}
        kept_direct = [item for item in self.jobs if item["board"] in retained]
        kept_community = [item for item in self.jobs
                          if item["board"] in community_names - fresh_community]
        for item in kept_direct + kept_community:
            item.setdefault("detail", None)

        before = {item["id"] for item in self.jobs}
        new_community = community_jobs(lists)
        excluded = {normalize(name) for name in config.get("exclude_companies") or []}
        self.jobs = merge(kept_direct + direct + new_community + kept_community, self.seen, now,
                          excluded, config.get("tracks"), self.first_run)
        fill_details(self.jobs, self.details)
        fill_domains(self.jobs, self.domains)
        self.first_run = False

        cutoff = parse_time(now) - timedelta(days=FORGET_SEEN_AFTER_DAYS)
        active = {item["id"] for item in self.jobs}
        self.seen = {key: value for key, value in self.seen.items()
                     if key in active or parse_time(value) >= cutoff}
        self.details = {key: value for key, value in self.details.items() if key in self.seen}

        last_alert = self.meta.get("last_alert")
        alert = choose_alert_jobs(self.jobs, self.meta, config, now)
        if alert and not send_alert(alert):
            self.meta["last_alert"] = last_alert  # They'll go in the next alert instead.

        healthy = (len(failed) <= (1 - MIN_HEALTHY_RATIO) * max(len(keys), 1)
                   and len(failed_lists) < len(COMMUNITY_LISTS))
        changed = active != before
        self.meta.update({
            "updated": now,
            "jobs": len(self.jobs),
            "boards": len(self.boards),
            "boards_read": len(keys),
            "boards_failed": len(failed),
            "boards_unchanged": len(unchanged),
            "undated": sum(1 for item in self.jobs if not item["posted"]),
            "seconds": round(time.monotonic() - started),
            "healthy": healthy,
        })
        if lists:
            self.meta["lead"] = lead_over_lists(self.community, self.seen, now,
                                                self.meta.setdefault("tracking_since", now))
        self.save(changed)
        print("%s  %d internships, %d new, %d gone; %d boards checked (%d unchanged, %d failed) in %ds; %d alerted" % (
            now, len(self.jobs), len(active - before), len(before - active), len(keys), len(unchanged),
            len(failed), self.meta["seconds"], len(alert)), flush=True)
        return healthy

    def save(self, changed):
        for name, value in (("jobs", self.jobs), ("boards", self.boards), ("seen", self.seen),
                            ("details", self.details), ("domains", self.domains), ("meta", self.meta)):
            save(self.data / (name + ".json"), value)
        if changed or time.monotonic() - self.published_at >= HEARTBEAT_SECONDS:
            publish(self.data)
            self.published_at = time.monotonic()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--watch", type=float, metavar="MINUTES",
                        help="keep scanning for this many minutes")
    args = parser.parse_args()

    scanner = Scanner(Path(args.data_dir), load(args.config, {}))
    healthy = scanner.cycle()
    if args.watch:
        deadline = time.monotonic() + args.watch * 60
        while time.monotonic() < deadline:
            started = time.monotonic()
            try:
                healthy = scanner.cycle()
            except Exception as error:  # One bad cycle shouldn't end a run lasting hours.
                print("cycle failed: %r" % error, file=sys.stderr)
                healthy = False
            time.sleep(max(0, MIN_CYCLE_SECONDS - (time.monotonic() - started)))
    sys.exit(0 if healthy else 1)


if __name__ == "__main__":
    main()
