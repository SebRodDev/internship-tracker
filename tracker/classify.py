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


_STATE_CODES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|"
    "NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC"
)
_US_COUNTRY = re.compile(r"united states|\bUSA?\b|\bU\.S\.|\bAmerica\b", re.I)
_US_CODE = re.compile(
    # "Austin, TX", "US-CA", "Remote-IL", "WI-Milwaukee", "Golden CO", "Pittsburgh PA, 15222"
    r",\s*(?:%(s)s)\b|\bUSA?[- ](?:%(s)s)\b|\b[Rr]emote[- ](?:%(s)s)\b|^(?:%(s)s)\s*[-, ]\s*[A-Z]"
    r"|[a-z] (?:%(s)s)(?:$|[ ,(])" % {"s": _STATE_CODES}
)
_US_SHORT = re.compile(r"\b(?:NYC|SF|LA|DC)\b")
_US_WORDS = re.compile(
    r"\b(?:alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia"
    r"|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts"
    r"|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey"
    r"|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania"
    r"|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington"
    r"|west virginia|wisconsin|wyoming"
    r"|san francisco|bay area|silicon valley|mountain view|sunnyvale|palo alto|menlo park|cupertino"
    r"|santa clara|san jose|san mateo|redwood city|san diego|irvine|pasadena|los angeles|berkeley"
    r"|oakland|sacramento|seattle|bellevue|redmond|kirkland|portland|phoenix|tempe|scottsdale"
    r"|las vegas|reno|salt lake city|boulder|denver|minneapolis|madison|milwaukee|detroit"
    r"|ann arbor|cleveland|columbus|cincinnati|indianapolis|pittsburgh|philadelphia|baltimore"
    r"|arlington|reston|herndon|mclean|raleigh|durham|charlotte|nashville|atlanta|miami|orlando"
    r"|tampa|dallas|houston|san antonio|plano|austin|trenton|hoboken|jersey city|stamford"
    r"|hartford|new haven|providence|manhattan|brooklyn|boston|chicago|huntsville|el segundo"
    r"|long beach|newport beach|scotts valley|waltham|wellesley|poughkeepsie|tucson|chandler"
    r"|dearborn|parsippany|peachtree city|richardson|novi|batavia|district of columbia"
    r"|puerto rico)\b",
    re.I,
)
_FOREIGN = re.compile(
    r"\b(?:sgp|europe|frankfurt|dresden|bremen|basel|belgrade|sofia|baku"
    r"|belo horizonte|leeds|bruxelles|brussel|cyberjaya"
    r"|uk|gb|united kingdom|great britain|england|scotland|wales|northern ireland|ireland|london"
    r"|dublin|belfast|edinburgh|manchester|bristol|france|paris|germany|deutschland|berlin|munich"
    r"|m[uü]nchen|hamburg|spain|madrid|barcelona|portugal|lisbon|italy|milan|rome|netherlands"
    r"|amsterdam|eindhoven|belgium|brussels|luxembourg|switzerland|z[uü]rich|geneva|austria|vienna"
    r"|sweden|stockholm|norway|oslo|denmark|copenhagen|finland|helsinki|poland|warsaw|krak[oó]w"
    r"|gdansk|czech|prague|hungary|budapest|romania|bucharest|bulgaria|serbia|croatia|greece"
    r"|turkey|istanbul|ukraine|russia|israel|tel aviv|egypt|morocco|nigeria|kenya|south africa"
    r"|cape town|johannesburg|saudi|riyadh|uae|united arab emirates|dubai|abu dhabi|qatar|doha"
    r"|india|bengaluru|bangalore|hyderabad|pune|mumbai|new delhi|delhi|gurgaon|gurugram|noida"
    r"|chennai|pakistan|bangladesh|sri lanka|china|beijing|shanghai|shenzhen|guangzhou|hangzhou"
    r"|suzhou|hong kong|taiwan|taipei|hsinchu|japan|tokyo|osaka|korea|seoul|singapore|malaysia"
    r"|kuala lumpur|penang|petaling jaya|thailand|bangkok|vietnam|ho chi minh|hanoi|philippines"
    r"|manila|indonesia|jakarta|australia|sydney|melbourne|new zealand|auckland|argentina"
    r"|buenos aires|brazil|brasil|s[aã]o paulo|chile|santiago|colombia|bogot[aá]|peru|lima"
    r"|costa rica|heredia|(?<!new )mexico|emea|apac|latam)\b"
    r"|^(?:SG|FR|ES|NL|PL|CN|AU|KR|HK|TW|MY|TH|VN|PH|CH|AT|BE|SE|DK|NO|FI|TR|RO|CZ|HU|PT|GR"
    r"|AR|CL|PE|LU|ZA|AE|SA|JP|BR|MX|IT|IE)[,-]\s?",
    re.I,
)
_CANADA = re.compile(
    r"\b(?:canada|can|cad|ontario|quebec|qu[eé]bec|british columbia|alberta|manitoba|nova scotia"
    r"|toronto|vancouver|montr[eé]al|montral|ottawa|calgary|edmonton|waterloo|mississauga|markham"
    r"|winnipeg|halifax|longueuil|kanata|surrey)\b",
    re.I,
)
_CANADA_CODE = re.compile(r",\s*(?:ON|QC|BC|AB|MB|SK|NS|NB)\b|\bCA-(?:ON|QC|BC|AB)\b")
_REMOTE = re.compile(r"remote|work from home|\bwfh\b|anywhere|nationwide", re.I)


def place_kind(location):
    """Sort one location into "us", "canada", "remote", "foreign" or "unknown"."""
    location = location.replace("_", " ")
    if _CANADA.search(location) or _CANADA_CODE.search(location):
        return "canada"
    if _US_COUNTRY.search(location):
        return "us"
    if _FOREIGN.search(location):
        return "foreign"
    if _US_CODE.search(location) or _US_SHORT.search(location) or _US_WORDS.search(location):
        return "us"
    return "remote" if _REMOTE.search(location) else "unknown"


def regions_for(locations, country=None):
    """Return region labels for a role: us, canada, remote, other.

    `country` is the employer's own country code, used when the text can't be placed.
    """
    found = set()
    for location in locations:
        kind = place_kind(location)
        if kind in ("us", "canada"):
            found.add(kind)
        if kind != "foreign" and _REMOTE.search(location):
            found.add("remote")
        if kind in ("foreign", "unknown") and "remote" not in found:
            found.add("other")
    if found <= {"other"}:
        if country in ("US", "USA"):
            return ["us"]
        if country in ("CA", "CAN"):
            return ["canada"]
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
