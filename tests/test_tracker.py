import tempfile
import time
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from tracker import __main__ as main
from tracker.__main__ import (alert_body, choose_alert_jobs, fill_details, first_seen_for, job_id,
                              lead_over_lists, merge, update_boards)
from tracker.classify import is_internship, is_past, regions_for, term_for, track_for
from tracker.discover import parse_board, posting_key
from tracker.logos import best_match, domain_from_url, fill_domains
from tracker.net import FetchError, Unchanged
from tracker.sources import (WORKDAY_FULL_READ_EVERY, amazon_date, find_pay, iso, job, workday,
                             workday_location, workday_posted)


class InternshipFilter(unittest.TestCase):
    def test_keeps_internships(self):
        for title in [
            "Software Engineer Intern",
            "Summer 2027 Internship - Data",
            "Product Manager Intern",
            "Mechanical Engineering Co-op",
            "Coop Student, Firmware",
            "Summer Analyst, Investment Banking",
            "Interns - Marketing",
            "Recruiting Intern",
            "Student Worker - Data Analyst",
        ]:
            self.assertTrue(is_internship(title), title)

    def test_drops_everything_else(self):
        for title in [
            "Internal Audit Manager",
            "International Sales Lead",
            "Senior Software Engineer",
            "Interim Director of Finance",
            "Internship Program Manager",
            "Intern Coordinator",
            "Campus Recruiter, Interns",
            "Manager, Internships",
        ]:
            self.assertFalse(is_internship(title), title)


class Labels(unittest.TestCase):
    def test_tracks(self):
        self.assertEqual(track_for("Machine Learning Intern"), "AI/ML")
        self.assertEqual(track_for("Quantitative Trader Intern"), "Quant")
        self.assertEqual(track_for("Software Engineer Intern"), "Software")
        self.assertEqual(track_for("Firmware Intern"), "Hardware")
        self.assertEqual(track_for("Intern", "Quant"), "Quant")
        self.assertEqual(track_for("Intern"), "Other")
        self.assertEqual(track_for("Embedded Software Intern"), "Software")
        self.assertEqual(track_for("Mechanical Engineer Intern", "Hardware"), "Other")
        self.assertEqual(track_for("Hardware Design Intern"), "Hardware")

    def test_regions(self):
        self.assertEqual(regions_for(["Foster City, CA"]), ["us"])
        self.assertEqual(regions_for(["Toronto, ON, CA"]), ["canada"])
        self.assertEqual(regions_for(["Remote (Canada)"]), ["canada", "remote"])
        self.assertEqual(regions_for(["US CA Santa Clara (+ more)"]), ["us"])
        self.assertEqual(regions_for(["Bangalore, KA, IN"]), ["other"])
        self.assertEqual(regions_for(["China, Shanghai"]), ["other"])
        self.assertEqual(regions_for(["Remote"]), ["remote"])
        self.assertEqual(regions_for(["Golden CO (+ more)"]), ["us"])
        self.assertEqual(regions_for(["Remote - Germany"]), ["other"])
        self.assertEqual(regions_for(["WI-Milwaukee"]), ["us"])
        self.assertEqual(regions_for(["Mexico City"]), ["other"])
        self.assertEqual(regions_for(["New Mexico"]), ["us"])
        self.assertEqual(regions_for(["Headquarters"], "US"), ["us"])
        self.assertEqual(regions_for(["Headquarters"], "CA"), ["canada"])
        self.assertEqual(regions_for(["Headquarters"], "DE"), ["other"])
        self.assertEqual(regions_for([]), ["other"])

    def test_term(self):
        self.assertEqual(term_for("Intern - Summer 2027"), "Summer 2027")
        self.assertEqual(term_for("Fall '26 Co-op"), "Fall 2026")
        self.assertEqual(term_for("Intern", ["Spring 2027"]), "Spring 2027")
        self.assertIsNone(term_for("Intern"))
        self.assertIsNone(term_for("Intern", ["N/A"]))


class BoardDiscovery(unittest.TestCase):
    def test_platforms(self):
        cases = {
            "https://job-boards.greenhouse.io/verkada/jobs/1": ("greenhouse", "verkada"),
            "https://boards.greenhouse.io/embed/job_app?for=acme&token=1": ("greenhouse", "acme"),
            "https://jobs.lever.co/zoox/abc/apply": ("lever", "zoox"),
            "https://jobs.eu.lever.co/acme/abc": ("lever", "eu/acme"),
            "https://jobs.ashbyhq.com/interplay/abc": ("ashby", "interplay"),
            "https://jobs.smartrecruiters.com/WesternDigital/7": ("smartrecruiters", "WesternDigital"),
            "https://apply.workable.com/altom-transport/j/F3/apply": ("workable", "altom-transport"),
            "https://byu.wd1.myworkdayjobs.com/byu-careers/job/Provo/x": (
                "workday", "byu.wd1.myworkdayjobs.com/byu/byu-careers"),
            "https://byu.wd1.myworkdayjobs.com/en-US/byu-careers/job/Provo/x": (
                "workday", "byu.wd1.myworkdayjobs.com/byu/byu-careers"),
            "https://wd5.myworkdaysite.com/en-US/recruiting/guidewire/external/job/x": (
                "workday", "guidewire.wd5.myworkdayjobs.com/guidewire/external"),
        }
        for url, expected in cases.items():
            self.assertEqual(parse_board(url), expected, url)

    def test_ignores_other_sites(self):
        for url in ["https://www.tesla.com/careers/1", "https://apply.workable.com/j/ABC", "", "not a url"]:
            self.assertIsNone(parse_board(url), url)

    def test_existing_boards_keep_their_state(self):
        boards = {"lever:zoox": {"ats": "lever", "slug": "zoox", "company": "Zoox", "misses": 3}}
        items = [{"url": "https://jobs.lever.co/zoox/1", "company_name": "Other"},
                 {"url": "https://jobs.lever.co/new/1", "company_name": "New"}]
        update_boards(boards, {"list": items}, [{"ats": "ashby", "slug": "ramp"}])
        self.assertEqual(boards["lever:zoox"]["misses"], 3)
        self.assertEqual(boards["lever:new"]["company"], "New")
        self.assertEqual(boards["ashby:ramp"]["company"], "ramp")


class PostingKeys(unittest.TestCase):
    def test_same_posting_matches_across_link_styles(self):
        pairs = [
            ("https://jobs.lever.co/zoox/7b20b929-d9a4-4995-a472-16cd4925a877",
             "https://jobs.lever.co/zoox/7b20b929-d9a4-4995-a472-16cd4925a877/apply"),
            ("https://stripe.com/jobs/search?gh_jid=8172487",
             "https://job-boards.greenhouse.io/stripe/jobs/8172487"),
            ("https://nvidia.wd5.myworkdayjobs.com/Site/job/US-CA/Intern_JR2027176",
             "https://nvidia.wd5.myworkdayjobs.com/en-US/Site/job/Santa-Clara/Intern-2027_JR2027176-1/apply"),
            ("https://a.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1994",
             "https://a.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1994/?utm=1"),
            ("https://www.amazon.jobs/en/jobs/3202652/software-intern", "https://amazon.jobs/en/jobs/3202652/x"),
        ]
        for first, second in pairs:
            self.assertIsNotNone(posting_key(first), first)
            self.assertEqual(posting_key(first), posting_key(second), first)

    def test_unknown_sites_have_no_key(self):
        self.assertIsNone(posting_key("https://www.tesla.com/careers/search/job/intern-1"))


class Dates(unittest.TestCase):
    def test_iso(self):
        self.assertEqual(iso("2026-09-03T13:30:34-04:00"), "2026-09-03T17:30:34Z")
        self.assertEqual(iso("2026-04-07T17:12:35.753+00:00"), "2026-04-07T17:12:35Z")
        self.assertEqual(iso("2026-11-06T01:06:26.689Z"), "2026-11-06T01:06:26Z")
        self.assertEqual(iso("2026-05-29"), "2026-05-29T00:00:00Z")
        self.assertEqual(iso("2026-08-20T23:28:00+0000"), "2026-08-20T23:28:00Z")
        self.assertEqual(iso(0), "1970-01-01T00:00:00Z")
        self.assertIsNone(iso(None))
        self.assertIsNone(iso("soon"))

    def test_workday_dates(self):
        now = datetime(2026, 11, 5, tzinfo=timezone.utc)
        self.assertEqual(workday_posted("Posted Today", now), "2026-11-05T00:00:00Z")
        self.assertEqual(workday_posted("Posted Yesterday", now), "2026-11-04T00:00:00Z")
        self.assertEqual(workday_posted("Posted 23 Days Ago", now), "2026-10-13T00:00:00Z")
        self.assertIsNone(workday_posted("Posted 30+ Days Ago", now))

    def test_workday_location_uses_path_when_hidden(self):
        item = {"locationsText": "3 Locations", "externalPath": "/job/US-CA-Santa-Clara/Intern_1"}
        self.assertEqual(workday_location(item), "US CA Santa Clara (+ more)")
        self.assertEqual(workday_location({"locationsText": "Austin, TX"}), "Austin, TX")


def raw(title="Software Intern", company="Acme", location=("Austin, TX",), posted=None, board="lever:acme"):
    return {"title": title, "company": company, "location": list(location), "url": "https://x.test/1",
            "posted": posted, "board": board}


class Merging(unittest.TestCase):
    NOW = "2026-11-05T12:00:00Z"

    def test_list_copy_with_a_reworded_title_loses_to_the_employers_date(self):
        uuid = "7b20b929-d9a4-4995-a472-16cd4925a877"
        employer = dict(raw(title="Software Intern, Autonomy [Summer 2027]", posted="2026-09-01T00:00:00Z"),
                        url="https://jobs.lever.co/zoox/" + uuid)
        listed = dict(raw(title="Software Intern - Autonomy", posted="2026-11-05T10:00:00Z",
                          board="community:simplify"), url="https://jobs.lever.co/zoox/%s/apply" % uuid)
        jobs = merge([employer, listed], {}, self.NOW, set())
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["posted"], "2026-09-01T00:00:00Z")

    def test_short_link_list_copy_is_matched_by_title(self):
        employer = raw(title="Software Intern, Systems [Summer 2027]", posted="2026-09-01T00:00:00Z")
        listed = dict(raw(title="Software Intern - Systems", posted="2026-11-05T10:00:00Z",
                          board="community:vansh"), url="https://go.vansh.nyc/1jW")
        jobs = merge([employer, listed], {}, self.NOW, set())
        self.assertEqual([item["posted"] for item in jobs], ["2026-09-01T00:00:00Z"])

    def test_list_role_we_cant_read_directly_stays(self):
        listed = dict(raw(board="community:simplify"), url="https://www.tesla.com/careers/1")
        self.assertEqual(len(merge([listed], {}, self.NOW, set())), 1)

    def test_same_role_in_two_places_is_one_row(self):
        jobs = merge([raw(location=["Austin, TX"]), raw(location=["Toronto, ON"])], {}, self.NOW, set())
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["location"], ["Austin, TX", "Toronto, ON"])
        self.assertEqual(jobs[0]["regions"], ["canada", "us"])

    def test_company_spelling_is_ignored_when_matching(self):
        jobs = merge([raw(company="Western Digital"), raw(company="WesternDigital")], {}, self.NOW, set())
        self.assertEqual(len(jobs), 1)

    def test_first_seen_survives_between_runs(self):
        seen = {job_id("Acme", "Software Intern"): "2026-01-01T00:00:00Z"}
        self.assertEqual(merge([raw()], seen, self.NOW, set())[0]["first_seen"], "2026-01-01T00:00:00Z")

    def test_retained_job_can_be_merged_again(self):
        once = merge([raw()], {}, self.NOW, set())
        self.assertEqual(once[0]["track"], "Software")
        self.assertEqual(merge(once, {}, self.NOW, set())[0]["track"], "Software")

    def test_only_wanted_tracks_are_kept(self):
        jobs = [raw(), raw(title="Marketing Intern")]
        kept = merge(jobs, {}, self.NOW, set(), ["Software"])
        self.assertEqual([item["title"] for item in kept], ["Software Intern"])

    def test_found_time(self):
        old = "2026-09-01T00:00:00Z"
        today = "2026-11-05T09:00:00Z"
        self.assertEqual(first_seen_for(old, self.NOW, False), old)
        self.assertEqual(first_seen_for(today, self.NOW, False), self.NOW)
        self.assertEqual(first_seen_for(None, self.NOW, False), self.NOW)
        self.assertEqual(first_seen_for(today, self.NOW, True), today)
        self.assertEqual(first_seen_for(None, self.NOW, True), "2026-10-06T12:00:00Z")

    def test_sorted_by_posting_date_not_when_found(self):
        seen = {job_id("Acme", "Found Long Ago Intern"): "2026-01-01T00:00:00Z"}
        jobs = merge([raw(title="Just Found Software Intern", posted="2026-03-01T00:00:00Z"),
                      raw(title="Found Long Ago Intern", posted="2026-11-01T00:00:00Z"),
                      raw(title="Undated Software Intern")], seen, self.NOW, set())
        self.assertEqual([item["title"] for item in jobs], [
            "Found Long Ago Intern", "Just Found Software Intern", "Undated Software Intern"])

    def test_excluded_company(self):
        self.assertEqual(merge([raw()], {}, self.NOW, {"acme"}), [])


class Pay(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(find_pay("Pay: $32 - $55 per hour"), "$32–$55/hr")
        self.assertEqual(find_pay("$32/hr to $55/hr"), "$32–$55/hr")
        self.assertEqual(find_pay("$211.4K – $290.6K • Offers Equity"), "$211k–$290k/yr")
        self.assertEqual(find_pay("between $85,000 and more, $85,000 - $110,000 annually"), "$85k–$110k/yr")
        self.assertEqual(find_pay("We pay $40/hour"), "$40/hr")

    def test_ignores_other_money(self):
        self.assertIsNone(find_pay("We raised $50 - $100 million"))
        self.assertIsNone(find_pay("Revenue of $2 - $5 billion"))
        self.assertIsNone(find_pay(None))


class Details(unittest.TestCase):
    def test_attaches_cached_date_and_resorts(self):
        first = merge([raw(title="A Intern", posted="2026-10-01T00:00:00Z"), raw(title="B Intern")],
                      {}, "2026-11-05T12:00:00Z", set())
        details = {first[1]["id"]: {"pay": "$40/hr", "posted": "2026-11-01T00:00:00Z"}}
        fill_details(first, details)
        self.assertEqual(first[0]["title"], "B Intern")
        self.assertEqual(first[0]["pay"], "$40/hr")
        self.assertNotIn("detail", first[0])


class Logos(unittest.TestCase):
    def test_uses_a_company_site_but_never_a_hiring_platform(self):
        self.assertEqual(domain_from_url("https://stripe.com/jobs/search?gh_jid=1"), "stripe.com")
        self.assertEqual(domain_from_url("https://careers.amd.com/jobs/1"), "amd.com")
        for url in ["https://jobs.lever.co/zoox/1", "https://nvidia.wd5.myworkdayjobs.com/x",
                    "https://x.icims.com/jobs/1", "https://go.vansh.nyc/j", "https://a.co.uk/j"]:
            self.assertIsNone(domain_from_url(url), url)

    def test_lookup_needs_an_exact_name(self):
        suggestions = [{"name": "Figure 1", "domain": "figure1.com"}]
        self.assertIsNone(best_match("Figure", suggestions))
        suggestions.append({"name": "FIGURE", "domain": "figure.com"})
        self.assertEqual(best_match("Figure", suggestions), "figure.com")

    def test_attaches_known_domains_without_network(self):
        jobs = [dict(raw(), url="https://stripe.com/jobs/1", id="1")]
        domains = {}
        fill_domains(jobs, domains)
        self.assertEqual(jobs[0]["domain"], "stripe.com")


class FullCycle(unittest.TestCase):
    """Runs real scan cycles with the network replaced by fake boards."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data = Path(folder.name)
        self.results = {}
        self.community = []
        patches = [
            mock.patch.dict(main.FETCHERS, {"fake": self.fake_fetch}),
            mock.patch.object(main, "read_community", self.fake_lists),
            mock.patch.object(main, "fill_domains", lambda jobs, domains: None),
            mock.patch.object(main, "BUILT_IN_BOARDS", []),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def fake_lists(self, commits, sources=main.COMMUNITY_LISTS):
        if sources is not main.COMMUNITY_LISTS:
            return {}, set()
        changed = commits.get("simplify") != id(self.community)
        commits["simplify"] = id(self.community)
        return ({"simplify": self.community} if changed else {}), set()

    def fake_fetch(self, board):
        result = self.results[board["slug"]]
        if isinstance(result, Exception):
            raise result
        return [dict(item) for item in result]

    def scanner(self):
        extra = [{"ats": "fake", "slug": slug, "company": slug.upper()} for slug in self.results]
        return main.Scanner(self.data, {"extra_boards": extra, "tracks": ["Software"]})

    def titles(self, scanner):
        return sorted(item["title"] for item in scanner.jobs)

    def job(self, title):
        return job(title, "A", "Austin, TX", "https://a.test/1", "2026-11-01T00:00:00Z")

    def test_jobs_arrive_stay_and_leave(self):
        self.results = {"a": [self.job("One Software Intern"), self.job("Two Software Intern")]}
        scanner = self.scanner()
        self.assertTrue(scanner.cycle())
        self.assertEqual(len(scanner.jobs), 2)

        self.results["a"] = Unchanged("no change")
        scanner.community_at = time.monotonic()
        scanner.cycle()
        self.assertEqual(len(scanner.jobs), 2)

        self.results["a"] = FetchError("network down")
        scanner.cycle()
        self.assertEqual(len(scanner.jobs), 2)

        self.results["a"] = [self.job("Two Software Intern")]
        scanner.cycle()
        self.assertEqual(self.titles(scanner), ["Two Software Intern"])

    def test_an_unexpected_error_on_one_board_spares_the_rest(self):
        self.results = {"a": RuntimeError("surprise"), "b": [self.job("Software Intern")]}
        scanner = self.scanner()
        scanner.cycle()
        self.assertEqual(self.titles(scanner), ["Software Intern"])

    def test_community_roles_survive_cycles_that_skip_the_list(self):
        self.community = [{"title": "Listed Software Intern", "company_name": "C", "active": True,
                           "url": "https://c.test/1", "locations": ["Austin, TX"], "date_posted": 1793900000}]
        scanner = self.scanner()
        scanner.cycle()
        scanner.community_at = time.monotonic()
        scanner.cycle()
        self.assertEqual(self.titles(scanner), ["Listed Software Intern"])

    def test_list_is_reread_when_it_changes_and_closed_roles_leave(self):
        item = {"title": "Listed Software Intern", "company_name": "C", "active": True,
                "url": "https://c.test/1", "locations": ["Austin, TX"], "date_posted": 1793900000}
        self.community = [item]
        scanner = self.scanner()
        scanner.cycle()
        self.community = [dict(item, active=False)]
        scanner.community_at = 0
        scanner.cycle()
        self.assertEqual(scanner.jobs, [])

    def test_dead_board_is_skipped_and_its_jobs_removed(self):
        self.results = {"a": [self.job("Software Intern")]}
        scanner = self.scanner()
        scanner.cycle()
        scanner.boards["fake:a"]["misses"] = main.DROP_BOARD_AFTER_404S
        scanner.community_at = time.monotonic()
        scanner.cycle()
        self.assertEqual(scanner.jobs, [])
        self.assertEqual(scanner.live_boards(), [])

    def test_state_survives_a_restart(self):
        self.results = {"a": [self.job("Software Intern")]}
        scanner = self.scanner()
        scanner.cycle()
        scanner.boards["fake:a"]["etag"] = "abc"
        scanner.save(False)
        restarted = self.scanner()
        self.assertEqual(self.titles(restarted), ["Software Intern"])
        self.assertEqual(restarted.boards["fake:a"]["etag"], "abc")

    def test_changing_filters_forces_a_fresh_read(self):
        self.results = {"a": [self.job("Software Intern")]}
        scanner = self.scanner()
        scanner.cycle()
        scanner.boards["fake:a"]["etag"] = "abc"
        scanner.save(False)
        changed = main.Scanner(self.data, {"tracks": ["Software", "Data"]})
        self.assertNotIn("etag", changed.boards["fake:a"])

    def test_failed_alert_is_retried(self):
        self.results = {"a": [self.job("Software Intern")]}
        scanner = self.scanner()
        scanner.cycle()
        scanner.meta["last_alert"] = "2026-01-01T00:00:00Z"
        with mock.patch.object(main, "choose_alert_jobs", return_value=[{}]), \
                mock.patch.object(main, "send_alert", return_value=False):
            scanner.cycle()
        self.assertEqual(scanner.meta["last_alert"], "2026-01-01T00:00:00Z")


class CommunityLists(unittest.TestCase):
    def test_discovery_list_adds_boards_but_no_roles(self):
        def fake(commits, sources=main.COMMUNITY_LISTS):
            if sources is main.COMMUNITY_LISTS:
                return {"simplify": []}, set()
            return {"newgrad": [{"url": "https://jobs.lever.co/newco/1", "company_name": "NewCo",
                                 "title": "Software Engineer Intern", "active": True}]}, set()

        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(main, "read_community", fake), \
                mock.patch.object(main, "BUILT_IN_BOARDS", []), \
                mock.patch.object(main, "scrape_boards", return_value=([], set(), set())), \
                mock.patch.object(main, "fill_domains"):
            scanner = main.Scanner(Path(folder), {})
            scanner.cycle()
        self.assertIn("lever:newco", scanner.boards)
        self.assertEqual(scanner.jobs, [])

    def read(self, commits):
        with mock.patch.object(main, "latest_commit", return_value="abc"), \
                mock.patch.object(main, "read_listings", return_value=[{"title": "x"}]) as reader:
            return main.read_community(commits), reader

    def test_new_commit_is_read_and_remembered(self):
        commits = {}
        (lists, failed), reader = self.read(commits)
        self.assertEqual(set(lists), {"simplify", "vansh"})
        self.assertEqual(commits, {"simplify": "abc", "vansh": "abc"})
        self.assertEqual(failed, set())

    def test_same_commit_downloads_nothing(self):
        (lists, _), reader = self.read({"simplify": "abc", "vansh": "abc"})
        self.assertEqual(lists, {})
        reader.assert_not_called()

    def test_failure_is_retried_next_time(self):
        commits = {}
        with mock.patch.object(main, "latest_commit", side_effect=FetchError("down")):
            lists, failed = main.read_community(commits)
        self.assertEqual(failed, {"simplify", "vansh"})
        self.assertEqual(commits, {})


class PastSeasons(unittest.TestCase):
    def test_past_and_current(self):
        today = date(2026, 11, 6)
        self.assertTrue(is_past("Intern", "Summer 2026", today))
        self.assertTrue(is_past("Intern 2025", None, today))
        self.assertFalse(is_past("Intern", "Fall 2026", today))
        self.assertFalse(is_past("Intern", "Winter 2027", today))
        self.assertFalse(is_past("Intern", "Summer 2027", today))
        self.assertFalse(is_past("2026 Intern", None, today))
        self.assertFalse(is_past("Intern", None, today))
        self.assertTrue(is_past("Intern", "Fall 2026", date(2027, 1, 2)))

    def test_year_first_titles(self):
        self.assertEqual(term_for("2027 Summer Intern"), "Summer 2027")


class Sources(unittest.TestCase):
    def test_rippling_board(self):
        self.assertEqual(parse_board("https://ats.rippling.com/pathos/jobs/9396"), ("rippling", "pathos"))

    def test_blocked_platforms_are_not_read(self):
        self.assertIsNone(parse_board("https://johndeere.eightfold.ai/careers/job/1"))
        for name in ("eightfold", "muse", "linkedin"):
            self.assertNotIn(name, main.FETCHERS)

    def test_oracle_board(self):
        self.assertEqual(
            parse_board("https://ewvl.fa.us8.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1994"),
            ("oracle", "ewvl.fa.us8.oraclecloud.com/CX_1"))

    def test_amazon_date(self):
        self.assertEqual(amazon_date("November  5, 2026"), "2026-11-05T00:00:00Z")
        self.assertIsNone(amazon_date(None))

    def workday_board(self, responses, **state):
        board = dict({"slug": "a.wd1.myworkdayjobs.com/a/site", "company": "A"}, **state)
        with mock.patch("tracker.sources.fetch_json", side_effect=responses):
            return board, workday(board)

    def test_workday_same_totals_means_unchanged(self):
        page = {"total": 0, "jobPostings": []}
        with self.assertRaises(Unchanged):
            self.workday_board([page, page], totals=[0, 0])

    def test_workday_new_total_reads_and_remembers(self):
        posting = {"title": "Software Intern", "externalPath": "/job/x", "locationsText": "Austin, TX"}
        board, jobs = self.workday_board(
            [{"total": 1, "jobPostings": [posting]}, {"total": 0, "jobPostings": []}], totals=[0, 0])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(board["totals"], [1, 0])

    def test_workday_full_read_happens_eventually(self):
        page = {"total": 0, "jobPostings": []}
        board, jobs = self.workday_board([page, page], totals=[0, 0], checks=WORKDAY_FULL_READ_EVERY)
        self.assertEqual(board["checks"], 0)


class Lead(unittest.TestCase):
    def test_compares_with_list_date(self):
        now = "2026-11-05T12:00:00Z"
        seen = {job_id("A", "One"): "2026-11-05T10:00:00Z", job_id("A", "Two"): "2026-11-05T11:00:00Z"}
        items = [{"company_name": "A", "title": "One", "date_posted": datetime(2026, 11, 5, 11, tzinfo=timezone.utc).timestamp()},
                 {"company_name": "A", "title": "Two", "date_posted": 0}]
        lead = lead_over_lists({"list": items}, seen, now, "2026-11-01T00:00:00Z")
        self.assertEqual(lead, {"ahead": 1, "total": 1, "median_minutes": 60})
        self.assertIsNone(lead_over_lists({"list": items}, seen, now, "2026-11-05T11:59:00Z"))


class Alerts(unittest.TestCase):
    NOW = "2026-11-05T12:00:00Z"

    def item(self, **overrides):
        base = merge([raw()], {}, "2026-11-05T11:00:00Z", set())[0]
        base.update(overrides)
        return base

    def run_alert(self, jobs, last="2026-11-05T06:00:00Z", **alerts):
        config = {"alerts": dict({"regions": ["us"]}, **alerts)}
        meta = {"last_alert": last} if last else {}
        return choose_alert_jobs(jobs, meta, config, self.NOW), meta

    def test_first_run_stays_quiet(self):
        sent, meta = self.run_alert([self.item()], last=None)
        self.assertEqual(sent, [])
        self.assertEqual(meta["last_alert"], self.NOW)

    def test_new_job_alerts(self):
        sent, meta = self.run_alert([self.item()])
        self.assertEqual(len(sent), 1)
        self.assertEqual(meta["last_alert"], self.NOW)

    def test_waits_for_digest_time(self):
        sent, _ = self.run_alert([self.item()], last="2026-11-05T11:30:00Z")
        self.assertEqual(sent, [])

    def test_held_jobs_go_in_the_next_digest(self):
        sent, _ = self.run_alert([self.item(first_seen="2026-11-05T06:30:00Z")])
        self.assertEqual(len(sent), 1)

    def test_respects_preferences(self):
        self.assertEqual(self.run_alert([self.item(regions=["other"])])[0], [])
        self.assertEqual(self.run_alert([self.item()], tracks=["Quant"])[0], [])
        self.assertEqual(self.run_alert([self.item()], keywords=["robot"])[0], [])
        self.assertEqual(self.run_alert([self.item()], exclude_keywords=["software"])[0], [])

    def test_watched_company_ignores_filters(self):
        meta = {"last_alert": "2026-11-05T06:00:00Z"}
        config = {"alerts": {"tracks": ["Quant"]}, "watch_companies": ["ACME"]}
        self.assertEqual(len(choose_alert_jobs([self.item()], meta, config, self.NOW)), 1)

    def test_old_postings_from_new_boards_are_skipped(self):
        sent, _ = self.run_alert([self.item(posted="2026-01-01T00:00:00Z")])
        self.assertEqual(sent, [])

    def test_scraped_text_cannot_inject_markdown(self):
        body = alert_body([self.item(title="Intern @everyone [x](http://bad.test) | y")], "")
        self.assertNotIn("@everyone", body)
        self.assertNotIn("[x]", body)
        self.assertEqual(body.splitlines()[-1].count("|"), 5)


if __name__ == "__main__":
    unittest.main()
