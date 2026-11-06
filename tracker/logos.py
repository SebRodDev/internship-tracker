"""Find each company's website so the page can show its logo."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlparse

from .net import FetchError, fetch_json

LOOKUPS_PER_CYCLE = 150
SEARCH_URL = "https://autocomplete.clearbit.com/v1/companies/suggest?query="

# Hosts that belong to a hiring platform or list, not to the employer.
_NOT_EMPLOYERS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "myworkdaysite.com",
    "smartrecruiters.com", "workable.com", "icims.com", "oraclecloud.com", "eightfold.ai",
    "jobvite.com", "applytojob.com", "rippling.com", "vansh.nyc", "simplify.jobs",
    "taleo.net", "successfactors.com", "successfactors.eu", "paylocity.com", "ultipro.com",
    "dayforcehcm.com", "adp.com", "breezy.hr", "bamboohr.com", "recruitee.com", "teamtailor.com",
    "jazz.co", "phenompeople.com", "avature.net", "brassring.com", "linkedin.com", "indeed.com",
    "github.com", "google.com", "gem.com", "dover.com", "pinpointhq.com", "paycomonline.net",
)
_TWO_PART_SUFFIX = re.compile(r"\.(co|com|org|ac|gov)\.[a-z]{2}$")


def _key(name):
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def domain_from_url(url):
    """Return the employer's domain if this job link is on their own site."""
    host = urlparse(url).netloc.lower().split(":")[0]
    if _TWO_PART_SUFFIX.search(host):
        return None
    domain = ".".join(host.split(".")[-2:])
    return None if "." not in domain or domain in _NOT_EMPLOYERS else domain


def best_match(company, suggestions):
    """Only trust an exact name match. A wrong logo is worse than a plain letter."""
    wanted = _key(company)
    for suggestion in suggestions:
        if _key(suggestion.get("name")) == wanted:
            return suggestion.get("domain")
    return None


def _lookup(company):
    return best_match(company, fetch_json(SEARCH_URL + quote(company), timeout=10, retries=0))


def fill_domains(jobs, domains):
    """Attach a domain to each job. `domains` caches them by company; "" means none found."""
    for item in jobs:
        found = domain_from_url(item["url"])
        if found and not domains.get(_key(item["company"])):
            domains[_key(item["company"])] = found

    missing = {}
    for item in jobs:
        if _key(item["company"]) not in domains:
            missing.setdefault(_key(item["company"]), item["company"])

    def lookup(entry):
        key, company = entry
        try:
            domains[key] = _lookup(company) or ""
        except (FetchError, AttributeError, TypeError):
            pass  # Try again on a later cycle.

    with ThreadPoolExecutor(6) as pool:
        list(pool.map(lookup, list(missing.items())[:LOOKUPS_PER_CYCLE]))

    for item in jobs:
        item["domain"] = domains.get(_key(item["company"])) or None
