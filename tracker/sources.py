"""One fetcher per hiring platform, plus the community internship lists.

Every fetcher returns dicts with: title, company, location, url, posted, pay, detail.
`location` is a list of strings; `posted` is an ISO timestamp or None. `detail` is a
URL to read the job's description from when the listing itself doesn't give the pay.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from .classify import is_internship
from .net import Unchanged, fetch_json, fetch_text

# Community repositories, as name: (repository, branch).
COMMUNITY_LISTS = {
    "simplify": ("SimplifyJobs/Summer2027-Internships", "dev"),
    "vansh": ("vanshb03/Summer2027-Internships", "dev"),
}
# Repositories read only to find more company boards, not for their roles.
DISCOVERY_LISTS = {"newgrad": ("SimplifyJobs/New-Grad-Positions", "dev")}
LISTINGS_PATH = ".github/scripts/listings.json"
SEARCH_TERMS = ("intern", "co-op")
WORKDAY_PAGE = 20
WORKDAY_MAX_PAGES = 8
# Comparing result counts can miss one role closing as another opens, so read in full now and then.
WORKDAY_FULL_READ_EVERY = 30
AMAZON_PAGE = 100
AMAZON_MAX_PAGES = 15
JIBE_PAGE = 100
TIKTOK_PAGE = 100
TIKTOK_MAX_PAGES = 40
BUILT_IN_BOARDS = [
    {"ats": "amazon", "slug": "jobs", "company": "Amazon"},
    {"ats": "google", "slug": "jobs", "company": "Google"},
    {"ats": "apple", "slug": "jobs", "company": "Apple"},
    {"ats": "tiktok", "slug": "jobs", "company": "TikTok"},
    {"ats": "jibe", "slug": "careers.amd.com", "company": "AMD"},
    {"ats": "jibe", "slug": "jobs.keysight.com", "company": "Keysight Technologies"},
]


def iso(value):
    """Convert an ISO string or epoch seconds to a UTC ISO timestamp, or None."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            parsed = datetime.fromtimestamp(value, timezone.utc)
        else:
            text = re.sub(r"\.(\d{3})\d*", r".\1", value.strip().replace("Z", "+00:00"))
            text = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", text)
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return None


_MONEY = r"\$\s?(\d[\d,]*(?:\.\d+)?)\s?(k)?"
_PAY_RANGE = re.compile(_MONEY + r"(?:\s*(?:/|per)\s*(?:hr|hour))?\s*(?:-|–|—|to)\s*" + _MONEY
                        + r"(?![\d,.]*\d)(?!\s*(?:million|billion|m\b|b\b))", re.I)
_PAY_HOURLY = re.compile(_MONEY + r"\s*(?:/|per |an )\s*(?:hr|hour)", re.I)


def _amount(number, thousands):
    return float(number.replace(",", "")) * (1000 if thousands else 1)


def _format_pay(low, high):
    if 10 <= low <= high <= 300:
        text = "$%g" % low if low == high else "$%g–$%g" % (low, high)
        return text + "/hr"
    if 20000 <= low <= high <= 500000:
        return "$%dk–$%dk/yr" % (low / 1000, high / 1000) if low != high else "$%dk/yr" % (low / 1000)
    return None


def find_pay(text):
    """Pull a pay range out of free text, ignoring amounts that can't be pay."""
    for match in _PAY_RANGE.finditer(text or ""):
        found = _format_pay(_amount(*match.group(1, 2)), _amount(*match.group(3, 4)))
        if found:
            return found
    match = _PAY_HOURLY.search(text or "")
    if match:
        amount = _amount(*match.group(1, 2))
        return _format_pay(amount, amount)
    return None


def read_detail(url):
    """Read one job's page (Greenhouse or Workday) for its pay and exact posting date."""
    response = fetch_json(url)
    info = response.get("jobPostingInfo") or {}
    text = response.get("content") or info.get("jobDescription")
    text = re.sub(r"<[^>]+>", " ", html.unescape(html.unescape(text or "")))
    return {"pay": find_pay(text.replace("\xa0", " ")), "posted": iso(info.get("startDate"))}


def job(title, company, location, url, posted, pay=None, detail=None, country=None):
    locations = location if isinstance(location, list) else [location]
    return {
        "title": " ".join((title or "").split()),
        "company": company,
        "location": [place.strip() for place in locations if place and place.strip()],
        "url": url,
        "posted": posted,
        "pay": pay,
        "detail": detail,
        "country": (country or "").upper()[:3] or None,
    }


def greenhouse(board):
    url = "https://boards-api.greenhouse.io/v1/boards/%s/jobs" % quote(board["slug"])
    return [
        job(item["title"], board["company"], (item.get("location") or {}).get("name"),
            item["absolute_url"], iso(item.get("first_published")),
            detail="%s/%s" % (url, item["id"]))
        for item in fetch_json(url, cache=board)["jobs"]
        if is_internship(item["title"])
    ]


def lever(board):
    slug = board["slug"]
    host = "api.lever.co"
    if slug.startswith("eu/"):
        slug, host = slug[3:], "api.eu.lever.co"
    url = "https://%s/v0/postings/%s?mode=json" % (host, quote(slug))
    results = []
    for item in fetch_json(url, timeout=40, cache=board):
        commitment = (item.get("categories") or {}).get("commitment") or ""
        if is_internship(item["text"]) or is_internship(commitment):
            categories = item.get("categories") or {}
            places = categories.get("allLocations") or [categories.get("location")]
            created = item.get("createdAt")
            salary = item.get("salaryRange") or {}
            pay = None
            if salary.get("currency") == "USD" and salary.get("min"):
                pay = _format_pay(float(salary["min"]), float(salary.get("max") or salary["min"]))
            results.append(job(item["text"], board["company"], places, item["hostedUrl"],
                               iso(created / 1000) if created else None,
                               pay=pay or find_pay(item.get("salaryDescriptionPlain")),
                               country=item.get("country")))
    return results


def ashby(board):
    url = "https://api.ashbyhq.com/posting-api/job-board/%s?includeCompensation=true" % quote(board["slug"])
    results = []
    for item in fetch_json(url, timeout=40, cache=board)["jobs"]:
        listed = item.get("isListed", True)
        if listed and (is_internship(item["title"]) or item.get("employmentType") == "Intern"):
            places = [item.get("location")]
            places += [extra.get("location") for extra in item.get("secondaryLocations") or []]
            compensation = item.get("compensation") or {}
            address = (item.get("address") or {}).get("postalAddress") or {}
            results.append(job(item["title"], board["company"], places, item["jobUrl"],
                               iso(item.get("publishedAt")),
                               pay=find_pay(compensation.get("compensationTierSummary")),
                               country=address.get("addressCountry")))
    return results


def smartrecruiters(board):
    results = {}
    for term in SEARCH_TERMS:
        url = "https://api.smartrecruiters.com/v1/companies/%s/postings?q=%s&limit=100" % (
            quote(board["slug"]), quote(term))
        for item in fetch_json(url)["content"]:
            if is_internship(item["name"]):
                place = item.get("location") or {}
                parts = [place.get("city"), place.get("region"), (place.get("country") or "").upper()]
                location = ", ".join(part for part in parts if part)
                if place.get("remote"):
                    location = "Remote - " + location
                results[item["id"]] = job(
                    item["name"], board["company"], location,
                    "https://jobs.smartrecruiters.com/%s/%s" % (board["slug"], item["id"]),
                    iso(item.get("releasedDate")), country=place.get("country"))
    return list(results.values())


def workable(board):
    url = "https://apply.workable.com/api/v1/widget/accounts/%s" % quote(board["slug"])
    results = []
    for item in fetch_json(url)["jobs"]:
        if is_internship(item["title"]):
            parts = [item.get("city"), item.get("state"), item.get("country")]
            location = ", ".join(part for part in parts if part)
            if item.get("telecommuting"):
                location = "Remote - " + location
            codes = [place.get("countryCode") for place in item.get("locations") or []]
            results.append(job(item["title"], board["company"], location, item["url"],
                               iso(item.get("published_on")),
                               country="US" if "US" in codes else (codes or [None])[0]))
    return results


def workday_posted(text, now=None):
    """Workday only says things like 'Posted 23 Days Ago'."""
    now = now or datetime.now(timezone.utc)
    text = (text or "").lower()
    if "today" in text:
        days = 0
    elif "yesterday" in text:
        days = 1
    else:
        match = re.search(r"(\d+)\s+days?", text)
        if not match or "+" in text:
            return None
        days = int(match.group(1))
    # Workday gives no time of day, so don't imply one.
    return (now - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")


def workday_location(item):
    text = item.get("locationsText") or ""
    if re.match(r"\d+ Locations", text):
        # The real place is only in the job's path, such as /job/US-CA-Santa-Clara/...
        parts = (item.get("externalPath") or "").split("/")
        if len(parts) > 2:
            return parts[2].replace("-", " ") + " (+ more)"
    return text


def workday(board):
    host, tenant, site = board["slug"].split("/")
    url = "https://%s/wday/cxs/%s/%s/jobs" % (host, tenant, site)

    def search(term, page):
        return fetch_json(url, body={"appliedFacets": {}, "limit": WORKDAY_PAGE,
                                     "offset": page * WORKDAY_PAGE, "searchText": term})

    # Workday has no ETags, but a search's total is a cheap sign that nothing changed.
    first_pages = {term: search(term, 0) for term in SEARCH_TERMS}
    totals = [first_pages[term].get("total") for term in SEARCH_TERMS]
    checks = board.get("checks", 0)
    if totals == board.get("totals") and checks < WORKDAY_FULL_READ_EVERY:
        board["checks"] = checks + 1
        raise Unchanged(url)

    results = {}
    for term in SEARCH_TERMS:
        for page in range(WORKDAY_MAX_PAGES):
            response = first_pages[term] if page == 0 else search(term, page)
            postings = response.get("jobPostings") or []
            matches = [item for item in postings if is_internship(item.get("title") or "")]
            for item in matches:
                results[item["externalPath"]] = job(
                    item["title"], board["company"], workday_location(item),
                    "https://%s/%s%s" % (host, site, item["externalPath"]),
                    workday_posted(item.get("postedOn")),
                    detail="https://%s/wday/cxs/%s/%s%s" % (host, tenant, site, item["externalPath"]))
            # Results are ranked, so a page with no internship titles means we're done.
            if not matches or len(postings) < WORKDAY_PAGE:
                break
    board["totals"] = totals
    board["checks"] = 0
    return list(results.values())


def oracle(board):
    host, site = board["slug"].split("/")
    results = {}
    for term in SEARCH_TERMS:
        url = ("https://%s/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
               "&expand=requisitionList&finder=findReqs;siteNumber=%s,keyword=%s,"
               "sortBy=POSTING_DATES_DESC,limit=100" % (host, quote(site), quote(term)))
        for search in fetch_json(url)["items"]:
            for item in search.get("requisitionList") or []:
                if is_internship(item.get("Title") or ""):
                    results[item["Id"]] = job(
                        item["Title"], board["company"], item.get("PrimaryLocation"),
                        "https://%s/hcmUI/CandidateExperience/en/sites/%s/job/%s" % (host, site, item["Id"]),
                        iso(item.get("PostedDate")))
    return list(results.values())


def amazon_date(text):
    try:
        return datetime.strptime(" ".join(text.split()), "%B %d, %Y").strftime("%Y-%m-%dT00:00:00Z")
    except (AttributeError, ValueError):
        return None


def amazon(board):
    results = {}
    for page in range(AMAZON_MAX_PAGES):
        url = "https://www.amazon.jobs/en/search.json?base_query=intern&sort=recent&result_limit=%d&offset=%d" % (
            AMAZON_PAGE, page * AMAZON_PAGE)
        postings = fetch_json(url).get("jobs") or []
        for item in postings:
            if is_internship(item.get("title") or ""):
                results[item["job_path"]] = job(
                    item["title"], board["company"], item.get("location"),
                    "https://www.amazon.jobs" + item["job_path"], amazon_date(item.get("posted_date")))
        if len(postings) < AMAZON_PAGE:
            break
    return list(results.values())


def jibe(board):
    """The careers-site platform used by AMD, Keysight and others."""
    host = board["slug"]
    results = {}
    for term in SEARCH_TERMS:
        url = "https://%s/api/jobs?keywords=%s&limit=%d&page=1" % (host, quote(term), JIBE_PAGE)
        for entry in fetch_json(url).get("jobs") or []:
            item = entry.get("data") or {}
            if is_internship(item.get("title") or ""):
                results[item["slug"]] = job(
                    item["title"], board["company"], item.get("full_location"),
                    "https://%s/jobs/%s" % (host, item["slug"]), iso(item.get("posted_date")),
                    country=item.get("country_code"))
    return list(results.values())


def rippling(board):
    url = "https://ats.rippling.com/api/v2/board/%s/jobs?page=0&pageSize=100" % quote(board["slug"])
    results = []
    for item in fetch_json(url).get("items") or []:
        if is_internship(item.get("name") or ""):
            places = item.get("locations") or item.get("workLocation") or []
            names = [place.get("name") if isinstance(place, dict) else place
                     for place in (places if isinstance(places, list) else [places])]
            results.append(job(item["name"], board["company"], names, item["url"], None))
    return results


def tiktok(board):
    url = "https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts"
    results = []
    for page in range(TIKTOK_MAX_PAGES):
        body = {"recruitment_id_list": [], "job_category_id_list": [], "subject_id_list": [],
                "location_code_list": [], "keyword": "intern", "limit": TIKTOK_PAGE,
                "offset": page * TIKTOK_PAGE}
        data = fetch_json(url, body=body, extra_headers={"website-path": "tiktok"}).get("data") or {}
        postings = data.get("job_post_list") or []
        for item in postings:
            if is_internship(item.get("title") or ""):
                place = item.get("city_info") or {}
                names = []
                while place:
                    names.append(place.get("en_name"))
                    place = place.get("parent") or place.get("parent_city")
                results.append(job(item["title"], board["company"], ", ".join(filter(None, names)),
                                   "https://lifeattiktok.com/search/%s" % item["id"], None))
        if len(postings) < TIKTOK_PAGE:
            break
    return results


def google(board):
    """Google's results page embeds its data as a JavaScript array.

    Only the first page is read, because Google's robots rules disallow the later ones.
    """
    url = ("https://www.google.com/about/careers/applications/jobs/results"
           "?target_level=INTERN_AND_APPRENTICE&location=United%20States&sort_by=date")
    text = fetch_text(url)
    start = text.index("data:", text.index("AF_initDataCallback({key: 'ds:1'")) + 5
    rows = json.JSONDecoder().raw_decode(text[start:])[0][0] or []
    return [
        job(row[1], "Google", [place[0] for place in row[9] or []],
            "https://www.google.com/about/careers/applications/jobs/results/%s" % row[0],
            iso(row[12][0]) if row[12] else None, country="US")
        for row in rows
    ]


def apple(board):
    """Apple's search page embeds its results as JSON."""
    results = {}
    for page in range(1, 11):
        url = ("https://jobs.apple.com/en-us/search?team=internships-STDNT-INTRN"
               "&location=united-states-USA&page=%d" % page)
        text = fetch_text(url)
        match = re.search(r'__staticRouterHydrationData\s*=\s*JSON\.parse\((".*?")\);', text, re.S)
        state = json.loads(json.loads(match.group(1)))
        loader = state["loaderData"]
        found = next((value for value in loader.values()
                      if isinstance(value, dict) and "searchResults" in value), {})
        rows = found.get("searchResults") or []
        for item in rows:
            path = "%s/%s" % (item["positionId"], item.get("transformedPostingTitle") or "")
            names = [place.get("name") for place in item.get("locations") or []]
            results[item["positionId"]] = job(
                item["postingTitle"], "Apple", names, "https://jobs.apple.com/en-us/details/" + path,
                iso(item.get("postDateInGMT")), country="US")
        if len(results) >= (found.get("totalRecords") or 0) or not rows:
            break
    return list(results.values())


FETCHERS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
    "workable": workable,
    "workday": workday,
    "oracle": oracle,
    "amazon": amazon,
    "jibe": jibe,
    "rippling": rippling,
    "tiktok": tiktok,
    "google": google,
    "apple": apple,
}


def latest_commit(repository, branch, token=None):
    headers = {"Authorization": "Bearer " + token} if token else None
    url = "https://api.github.com/repos/%s/git/ref/heads/%s" % (repository, branch)
    return fetch_json(url, extra_headers=headers)["object"]["sha"]


def read_listings(repository, commit):
    """Read a list at one commit, which skips the 5-minute cache on a branch's file links."""
    url = "https://raw.githubusercontent.com/%s/%s/%s" % (repository, commit, LISTINGS_PATH)
    return fetch_json(url, timeout=60)
