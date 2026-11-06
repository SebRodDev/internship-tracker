"""Decide what counts as an internship, and label its track, region and term."""
from __future__ import annotations

import re

_INTERN = re.compile(
    r"\b(interns?|internships?|co-?ops?|summer\s+(20\d\d\s+)?(analyst|associate)s?"
    r"|working\s+student|student\s+worker|werkstudent\w*|industrial\s+placement|placement\s+year)\b",
    re.I,
)
# Staff who run an internship program, rather than the internship itself.
_NOT_INTERN = re.compile(
    r"\b(interns?|internships?|co-?ops?)\s+(program\s+|programs\s+)?"
    r"(manager|coordinator|recruiter|director|specialist|mentor|supervisor|lead)\b"
    r"|\b(manager|director|coordinator|head)\s*(,|-|of|for)\s*(\w+\s+)?(interns?|internships?|co-?ops?)\b"
    r"|\brecruiter\b",
    re.I,
)


def is_internship(title):
    return bool(_INTERN.search(title)) and not _NOT_INTERN.search(title)


_TRACKS = [
    ("Quant", r"quant|trading|trader|algo\w*\s+dev"),
    ("Security", r"security|cyber|infosec|threat|penetration|red team"),
    ("AI/ML", r"machine learning|\bml\b|\bai\b|artificial intelligence|deep learning|\bnlp\b"
              r"|computer vision|\bllm\b|robot\w*\s+learning|applied scientist|research scientist"),
    ("Data", r"\bdata\b|analytics|business intelligence|\bbi\b|statistic"),
    ("Product", r"product manage|product management|\bapm\b|program manag|technical program"
                r"|\bpm\b|product owner"),
    ("Design", r"\bdesigner\b|\b(ux|ui|graphic|visual|product) design|\bux\b|user experience"
               r"|user research"),
    ("Software", r"software|developer|\bswe\b|\bsde\b|full.?stack|front.?end|back.?end|\bweb\b"
                 r"|mobile|\bios\b|android|devops|\bsre\b|site reliability|cloud|platform engineer"
                 r"|infrastructure|\bqa\b|\bit\b|information technology|computer science|programmer"),
    # Physical engineering isn't a tech role, whatever a list filed it under.
    ("Other", r"mechanical|manufacturing|aerospace|propulsion|civil|structural|chemical"
              r"|construction|industrial|materials|process engineer|facilities|environmental"),
    ("Hardware", r"hardware|electrical|embedded|firmware|fpga|asic|silicon|circuit|robotics"
                 r"|\brf\b|semiconductor|systems? engineer|avionics|\bdft\b|verification"),
    ("Business", r"financ|account|audit|tax|investment|banking|\bsales\b|marketing|strategy"
                 r"|consult|operations|supply chain|\bhr\b|human resources|people|legal|talent"
                 r"|communications|business|recruiting"),
]
_TRACKS = [(name, re.compile(pattern, re.I)) for name, pattern in _TRACKS]

# Community lists use their own category names.
_CATEGORY_TRACKS = {
    "software": "Software",
    "software engineering": "Software",
    "ai/ml/data": "AI/ML",
    "data science, ai & machine learning": "AI/ML",
    "hardware": "Hardware",
    "hardware engineering": "Hardware",
    "product": "Product",
    "product management": "Product",
    "quant": "Quant",
    "quantitative finance": "Quant",
}


def track_for(title, category=None):
    for name, pattern in _TRACKS:
        if pattern.search(title):
            return name
    return _CATEGORY_TRACKS.get((category or "").lower(), "Other")


_US_STATES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
    "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC"
)
_US_CODE = re.compile(r"(,\s*|\bUS[- ])(%s)\b" % _US_STATES.replace(" ", "|"))
_US_NAME = re.compile(r"\bUS\b|\bUSA\b|\bU\.S\.")
# Workday paths lose their commas, as in "Golden CO (+ more)".
_US_PATH = re.compile(r"[a-z] (%s)(?: \(\+ more\))?$" % _US_STATES.replace(" ", "|"))
_US_WORDS = re.compile(
    r"united states|america\b|new york|san francisco|bay area|seattle|boston|chicago|austin"
    r"|los angeles|san jose|washington,? d\.?c|atlanta|denver|dallas|houston|philadelphia"
    r"|california|texas|florida|massachusetts|virginia|illinois|colorado|michigan|ohio"
    r"|pennsylvania|new jersey|north carolina|arizona|oregon|minnesota|maryland",
    re.I,
)
_CANADA = re.compile(
    r"canada|toronto|vancouver|montr[eé]al|ottawa|calgary|edmonton|waterloo|mississauga"
    r"|winnipeg|halifax|qu[eé]bec|ontario|british columbia|alberta|,\s*(ON|QC|BC|AB|MB|SK|NS|NB)\b",
    re.I,
)
# Places whose country or state codes would otherwise read as a US state.
_ELSEWHERE = re.compile(
    r"india|bengaluru|bangalore|hyderabad|pune|mumbai|delhi|gurgaon|chennai|germany|deutschland"
    r"|france|spain|italy|ireland|netherlands|poland|china|japan|korea|singapore|australia"
    r"|brazil|mexico|israel|\buk\b|united kingdom|london|england",
    re.I,
)
_REMOTE = re.compile(r"remote|work from home|\bwfh\b|anywhere", re.I)


def regions_for(locations):
    """Return region labels for a list of location strings: us, canada, remote, other."""
    found = set()
    for location in locations:
        matched = False
        if _REMOTE.search(location):
            found.add("remote")
        if _CANADA.search(location):
            found.add("canada")
            matched = True
        elif _US_NAME.search(location) or _US_WORDS.search(location):
            found.add("us")
            matched = True
        elif (_US_CODE.search(location) or _US_PATH.search(location)) and not _ELSEWHERE.search(location):
            found.add("us")
            matched = True
        if not matched and not _REMOTE.search(location):
            found.add("other")
    return sorted(found) or ["other"]


_TERM = re.compile(r"\b(spring|summer|fall|autumn|winter)\s*[-']?\s*((?:20)?\d\d)\b", re.I)
_TERM_YEAR_FIRST = re.compile(r"\b(20\d\d)\s+(spring|summer|fall|autumn|winter)\b", re.I)
_YEAR = re.compile(r"\b20\d\d\b")
# The month after each season ends, as (month, years to add).
_SEASON_ENDS = {"spring": (6, 0), "summer": (9, 0), "fall": (1, 1), "autumn": (1, 1), "winter": (4, 0)}


def term_for(title, terms=None):
    if terms and terms[0] != "N/A":
        return terms[0]
    match = _TERM_YEAR_FIRST.search(title)
    if match:
        return "%s %s" % (match.group(2).title(), match.group(1))
    match = _TERM.search(title)
    if not match:
        return None
    year = match.group(2)
    return "%s %s" % (match.group(1).title(), year if len(year) == 4 else "20" + year)


def is_past(title, term, today):
    """True if the role is for a season that's already over. `today` is a date."""
    if term:
        parts = term.lower().split()
        if len(parts) == 2 and parts[0] in _SEASON_ENDS and parts[1].isdigit():
            month, extra_years = _SEASON_ENDS[parts[0]]
            return (today.year, today.month) >= (int(parts[1]) + extra_years, month)
    years = _YEAR.findall(title)
    return bool(years) and max(int(year) for year in years) < today.year
