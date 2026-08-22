"""Extraction pipeline: JSON-LD, universal DOM, filters, throttle."""

from __future__ import annotations

import json
import threading
import time

from job_scraper.config import KeywordConfig, LocationConfig
from job_scraper.extract import _detect_tech, extract_job_from_page
from job_scraper.http import HostThrottle, _looks_like_block, _retry_after_seconds
from job_scraper.main import _filter_by_keywords, _filter_by_location
from job_scraper.models import JobListing
from job_scraper.normalize import match_location
from job_scraper.universal import cluster_anchors, universal_extract

JSONLD_PAGE = """
<html><head>
<link rel="canonical" href="https://acme.test/jobs/senior-python-engineer"/>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting",
 "title":"Senior Python Engineer",
 "description":"<p>Build things.</p><h3>Requirements</h3><ul><li>5 years of experience</li><li>Bachelor degree</li></ul>",
 "hiringOrganization":{"@type":"Organization","name":"Acme AS","url":"https://acme.test"},
 "jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",
   "addressLocality":"Oslo","addressCountry":"NO","postalCode":"0150"}},
 "employmentType":["FULL_TIME"],
 "datePosted":"2026-05-01","validThrough":"2026-06-30",
 "baseSalary":{"@type":"MonetaryAmount","currency":"NOK",
   "value":{"@type":"QuantitativeValue","minValue":"800000","maxValue":"950000","unitText":"YEAR"}}}
</script></head>
<body><main><h1>Senior Python Engineer</h1>
<p>We use Python, Kubernetes and PostgreSQL. Remote friendly.</p>
<a href="https://boards.greenhouse.io/acme/jobs/42">Apply now</a>
</main></body></html>
"""


def test_jsonld_page_extracts_core_fields():
    job = extract_job_from_page(JSONLD_PAGE, "https://acme.test/jobs/senior-python-engineer?utm_source=x")
    assert job is not None
    assert job.title == "Senior Python Engineer"
    assert job.company == "Acme AS"
    assert job.city == "Oslo"
    assert job.postal_code == "0150"
    assert job.posted_date == "2026-05-01"
    assert job.valid_through == "2026-06-30"
    assert job.salary_min == "800000"
    assert job.salary_max == "950000"
    assert job.salary_currency == "NOK"
    # Apply URL must prefer the external ATS over the current page.
    assert "greenhouse.io" in job.apply_url


def test_jsonld_requirements_section_is_parsed():
    job = extract_job_from_page(JSONLD_PAGE, "https://acme.test/jobs/x")
    assert "5 years" in job.requirements
    assert job.qualifications == job.requirements


def test_tech_detection_is_specific_and_avoids_substrings():
    assert _detect_tech("We use Python and Kubernetes") == ["python", "kubernetes"]
    # "ai" must not fire on "maintain", "java" must not fire on "javanese".
    assert _detect_tech("maintain the javanese pottery") == []
    # Longest match wins: no duplicate generic + specific pair.
    hits = _detect_tech("Spring Boot and Vue.js")
    assert hits == ["vue.js", "spring boot"] or hits == ["spring boot", "vue.js"]
    assert "spring" not in hits and "vue" not in hits


def test_heuristics_scan_the_jsonld_description_not_just_the_dom():
    """On ATS pages the whole job body lives in JSON-LD and never reaches the DOM."""
    html = """
    <html><head><script type="application/ld+json">
    {"@context":"https://schema.org","@type":"JobPosting","title":"Backend Engineer",
     "description":"You will run our Kubernetes clusters and write Go services all day, every day, for a large distributed platform.",
     "hiringOrganization":{"name":"Acme"}}
    </script></head><body><main><h1>Backend Engineer</h1></main></body></html>
    """
    job = extract_job_from_page(html, "https://acme.test/jobs/9")
    assert "kubernetes" in job.tech_stack


def test_phone_regex_keeps_all_four_norwegian_groups():
    html = """
    <html><head><script type="application/ld+json">
    {"@type":"JobPosting","title":"Eng","description":"A reasonably long description of the role so the fallback does not kick in."}
    </script></head>
    <body><main><h1>Eng</h1><p>Ring oss pa +47 95 83 21 97 for sporsmal om stillingen.</p></main></body></html>
    """
    job = extract_job_from_page(html, "https://x.test/jobs/1")
    assert job.application_phone == "+47 95 83 21 97"


def test_universal_extract_declines_a_non_job_page():
    html = "<html><body><h1>Random blog post</h1><p>Just some text.</p></body></html>"
    assert universal_extract(html, "https://example.test/blog") is None


def test_universal_extract_reads_a_plain_dom_job_page():
    html = """
    <html><head><meta property="og:title" content="Utvikler | FINN.no"/></head>
    <body><main><h1>Utvikler</h1>
    <p>Arbeidsgiver soker en utvikler. Apply via link.</p>
    <dl><dt>Sokelig</dt><dd>x</dd></dl>
    <p>Requirements: python. Responsibilities: build.</p>
    </main></body></html>
    """
    job = universal_extract(html, "https://x.test/stilling/1")
    assert job is not None
    # The "| FINN.no" aggregator suffix must be stripped from the title.
    assert job.title == "Utvikler"


def test_cluster_anchors_finds_the_job_link_group():
    html = (
        "<html><body>"
        + "".join(f'<a href="/job/{i}">Role {i}</a>' for i in range(6))
        + '<a href="/about/us">About</a><a href="/legal/tos">Terms</a></body></html>'
    )
    urls = cluster_anchors(html, "https://x.test/search")
    assert len(urls) == 6
    assert all("/job/" in u for u in urls)


def test_cluster_anchors_ignores_tiny_clusters():
    html = '<html><body><a href="/job/1">a</a><a href="/job/2">b</a></body></html>'
    assert cluster_anchors(html, "https://x.test/") == []


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------


def test_allow_remote_lets_a_remote_job_through_a_city_filter():
    """The remote escape hatch was unreachable dead code before."""
    remote = JobListing(title="Eng", location="Anywhere", remote_type="remote")
    assert match_location(remote, ["Berlin"], [], allow_remote=True) is True
    assert match_location(remote, ["Berlin"], [], allow_remote=False) is False


def test_location_include_and_exclude():
    berlin = JobListing(title="Eng", location="Berlin, Germany")
    munich = JobListing(title="Eng", location="Munich, Germany")
    assert match_location(berlin, ["Berlin"], [], allow_remote=True) is True
    assert match_location(munich, ["Berlin"], [], allow_remote=True) is False
    assert match_location(berlin, [], ["Berlin"], allow_remote=True) is False


def test_keyword_filter_matches_on_tech_stack():
    """Configs document filtering on title+description+tech_stack."""
    job = JobListing(title="Engineer", description="Great team", tech_stack="kubernetes, go")
    kept = _filter_by_keywords([job], KeywordConfig(include=["kubernetes"]))
    assert kept == [job]
    assert job.keywords_matched == ["kubernetes"]


def test_short_keywords_use_word_boundaries():
    job = JobListing(title="Maintain the system", description="")
    assert _filter_by_keywords([job], KeywordConfig(include=["ai"])) == []
    ai_job = JobListing(title="AI Engineer", description="")
    assert len(_filter_by_keywords([ai_job], KeywordConfig(include=["ai"]))) == 1


def test_location_filter_is_a_noop_when_unconfigured():
    jobs = [JobListing(title="a"), JobListing(title="b")]
    assert _filter_by_location(jobs, LocationConfig()) == jobs


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------


def test_block_heuristic_spares_real_pages():
    assert _looks_like_block("") is True
    assert _looks_like_block("<html><body>Access Denied</body></html>") is True
    # A real posting that merely mentions a blocked word is not a block.
    real = '<html><script type="application/ld+json">{"@type":"JobPosting"}</script>Access denied</html>'
    assert _looks_like_block(real) is False
    assert _looks_like_block("<html>" + "content " * 2000 + "captcha</html>") is False


def test_retry_after_accepts_seconds_dates_and_garbage():
    assert _retry_after_seconds("5") == 5.0
    assert _retry_after_seconds(None) == 8.0
    assert _retry_after_seconds("not-a-number") == 8.0
    assert _retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0  # past date → no wait
    assert _retry_after_seconds("99999") == 30.0  # clamped


def test_host_throttle_enforces_min_delay_and_inflight_cap():
    throttle = HostThrottle(max_inflight=1, min_delay=0.05)
    start = time.perf_counter()
    for _ in range(3):
        throttle.acquire("h")
        throttle.release("h")
    assert time.perf_counter() - start >= 0.10  # two gaps of 50 ms


def test_host_throttle_is_thread_safe():
    throttle = HostThrottle(max_inflight=2, min_delay=0.0)
    peak = 0
    current = 0
    lock = threading.Lock()

    def worker():
        nonlocal peak, current
        throttle.acquire("h")
        with lock:
            current += 1
            peak = max(peak, current)
        time.sleep(0.01)
        with lock:
            current -= 1
        throttle.release("h")

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak <= 2


# --- jobs.ch shapes -------------------------------------------------------
# A live jobs.ch detail page carries a full JSON-LD body, a nav bar whose links
# include the word "Recruiter", and a "similar jobs" rail advertising a
# different employer's role. Each of those broke a different field.

_JOBSCH_POSTING = json.dumps(
    {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Senior Model-Based System Engineer",
        "description": "<p>"
        + "At Belimo we build actuators and sensors for heating and ventilation. " * 8
        + "You will own the model-based systems engineering toolchain.</p>",
        "hiringOrganization": {"@type": "Organization", "name": "BELIMO Automation AG"},
        "applicantLocationRequirements": {"@type": "Country", "name": "Switzerland"},
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressRegion": "Hinwil",
                "postalCode": "8340",
                "addressCountry": "CH",
            },
        },
        "occupationalCategory": {
            "@type": "CategoryCode",
            "codeValue": "98",
            "name": "Technical / Electronics",
        },
        "industry": "Industry various",
    }
)

_JOBSCH_HTML = f"""
<html><head><script type="application/ld+json">{_JOBSCH_POSTING}</script></head>
<body>
  <nav>{"".join(f'<a href="/{i}">link{i}</a>' for i in range(14))}
    <a href="/r">Recruiter</a><a href="/a">Area</a><a href="/d">Deutsch</a></nav>
  <main><h1>Senior Model-Based System Engineer</h1></main>
  <aside><h2>Similar jobs</h2>
    <p>DevSecOps Engineer (alle) LEGIC Identsystems AG Wallisellen Homeoffice</p></aside>
</body></html>
"""


def _jobsch_job():
    return extract_job_from_page(_JOBSCH_HTML, "https://www.jobs.ch/en/vacancies/detail/abc/")


def test_applicant_location_requirements_alone_does_not_mean_remote():
    """jobs.ch stamps "Country: Switzerland" on every posting, so treating its
    presence as remote marked 100% of them remote — street address and all."""
    assert _jobsch_job().remote_type == ""


def test_a_typed_category_node_is_read_as_its_name():
    """`occupationalCategory` arrives as a CategoryCode object; str() on it put a
    Python dict repr in the department column."""
    job = _jobsch_job()
    assert job.department == "Technical / Electronics"
    assert job.company_industry == "Industry various"


def test_a_recruiter_is_not_mined_out_of_the_site_navigation():
    """ "… Salary estimator Recruiter Area Deutsch Français" is a nav bar, and it
    yielded "Area Deutsch" as the recruiter on every posting on the site."""
    job = _jobsch_job()
    assert job.recruiter_name == ""
    assert job.recruiter_title == ""


def test_a_neighbouring_employers_card_does_not_contribute_to_this_posting():
    """The "similar jobs" rail belongs to other companies. Scanning it tagged a
    mechanical-engineering role "devsecops" and made it remote via Homeoffice."""
    job = _jobsch_job()
    assert "devsecops" not in job.tech_stack.lower()
    assert job.remote_type == ""
