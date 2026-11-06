"""Work out a company's job board from any job link that points at it."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse

_LOCALE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2})?$")
_ORACLE_SITE = re.compile(r"/sites/([^/]+)")
_WORKDAY_HOST = re.compile(r"^([\w-]+)\.(wd\d+)\.myworkdayjobs\.com$")
_WORKDAY_SITE_HOST = re.compile(r"^(wd\d+)\.myworkdaysite\.com$")
_GREENHOUSE_SKIP = {"embed", "jobs", "job_app"}
_WORKABLE_SKIP = {"j", "api"}


def parse_board(url):
    """Return (ats, slug) for a job URL, or None if it isn't a platform we read."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = parsed.netloc.lower().split(":")[0]
    parts = [unquote(part) for part in parsed.path.split("/") if part]

    if host.endswith("greenhouse.io"):
        query = parse_qs(parsed.query)
        token = (query.get("for") or query.get("token") or [None])[0]
        if parts and parts[0] == "embed":
            return ("greenhouse", token) if token else None
        if parts and parts[0] not in _GREENHOUSE_SKIP:
            return "greenhouse", parts[0]
        return None
    if host == "jobs.lever.co" and parts:
        return "lever", parts[0]
    if host == "jobs.eu.lever.co" and parts:
        return "lever", "eu/" + parts[0]
    if host == "jobs.ashbyhq.com" and parts:
        return "ashby", parts[0]
    if host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com") and parts:
        return "smartrecruiters", parts[0]
    if host == "apply.workable.com" and parts and parts[0] not in _WORKABLE_SKIP:
        return "workable", parts[0]

    if host == "ats.rippling.com" and parts:
        return "rippling", parts[0]
    if host.endswith(".oraclecloud.com"):
        match = _ORACLE_SITE.search(parsed.path)
        return ("oracle", "%s/%s" % (host, match.group(1))) if match else None

    match = _WORKDAY_HOST.match(host)
    if match:
        if parts and _LOCALE.match(parts[0]):
            parts = parts[1:]
        if parts and parts[0] != "job":
            return "workday", "%s/%s/%s" % (host, match.group(1), parts[0])
        return None

    match = _WORKDAY_SITE_HOST.match(host)
    if match and "recruiting" in parts:
        index = parts.index("recruiting")
        if len(parts) > index + 2:
            tenant, site = parts[index + 1], parts[index + 2]
            return "workday", "%s.%s.myworkdayjobs.com/%s/%s" % (
                tenant, match.group(1), tenant, site)
    return None


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_GREENHOUSE_ID = re.compile(r"(?:gh_jid=|/jobs/)(\d+)")
_WORKDAY_ID = re.compile(r"/job/(?:.*/)?[^/]*_([A-Za-z0-9-]+?)(?:-\d+)?(?:/apply.*)?(?:\?.*)?$")
_NUMERIC_ID = re.compile(r"/(\d{6,})(?:[/?]|$)")
_ORACLE_ID = re.compile(r"/job/(\d+)")
_SITE_ID = re.compile(r"/(?:job|jobs|search|details|results)/(\d{4,})")


def posting_key(url):
    """Identify a posting from its link, since lists often reword a role's title."""
    host = urlparse(url).netloc.lower()
    if "lever.co" in host or "ashbyhq.com" in host:
        match = _UUID.search(url)
        return match.group(0).lower() if match else None
    if "myworkdayjobs.com" in host or "myworkdaysite.com" in host:
        match = _WORKDAY_ID.search(url)
        return "%s:%s" % (host.split(".")[0], match.group(1).lower()) if match else None
    if "oraclecloud.com" in host:
        match = _ORACLE_ID.search(url)
        return "%s:%s" % (host, match.group(1)) if match else None
    if "smartrecruiters.com" in host or "amazon.jobs" in host:
        match = _NUMERIC_ID.search(url)
        return match.group(1) if match else None
    if "greenhouse.io" in host or "gh_jid=" in url:
        match = _GREENHOUSE_ID.search(url)
        return "gh:%s" % match.group(1) if match else None
    match = _SITE_ID.search(url)
    return "%s:%s" % (".".join(host.split(".")[-2:]), match.group(1)) if match else None
