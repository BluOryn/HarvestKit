"""Whose email is it, and is that name a person at all?

Every failure covered here shipped a *wrong* row rather than no row, which is
the worse kind: a lead list whose contact column is confidently incorrect costs
sender reputation and gets sent to real people who are not the intended
recipient.
"""

from __future__ import annotations

from job_scraper.universal import _is_ui_vocabulary, _mine_emails, mine_contacts
from leadgen.person.cascade import _email_rank, merge_hits
from leadgen.person.hit import PersonHit
from leadgen.person.strategies import impressum

# --------------------------------------------------- the board's own mailbox

JOBSCH_PAGE = """
<html><head><title>Senior Developer — jobs.ch</title></head>
<body>
  <nav>Recruiter Area Deutsch Français English Login</nav>
  <main>
    <h1>Senior Developer</h1>
    <p>Wir suchen eine erfahrene Entwicklerin fuer unser Team in Zuerich.</p>
    <p>Aufgaben: Entwicklung, Wartung, Code Review. Ihr Profil: mehrere Jahre Erfahrung.</p>
  </main>
  <footer>Bei Fragen: service@jobcloud.ch — JobCloud AG</footer>
</body></html>
"""


def test_the_board_operators_support_desk_is_never_the_recruiter():
    """100% of exported jobs.ch rows carried service@jobcloud.ch before this.

    The recruiter-flavour regex fired on the "job" inside "jobcloud", and an
    outbound campaign built from that file mails JobCloud's customer-service
    desk once per row.
    """
    contacts = mine_contacts(JOBSCH_PAGE)
    assert "jobcloud" not in (contacts.get("recruiter_email") or "")
    assert "jobcloud" not in (contacts.get("application_email") or "")


def test_operator_mailboxes_are_filtered_wherever_they_appear():
    assert _mine_emails("a service@jobcloud.ch b") == []
    assert _mine_emails("x jobs@stepstone.de y") == []
    assert _mine_emails("real a.schmidt@firma.de here") == ["a.schmidt@firma.de"]


def test_the_navigation_bar_is_not_a_person():
    """The exact string a previous fix claimed to have eliminated — it was
    removed from extract.py only, and this code path still produced it."""
    assert _is_ui_vocabulary("Recruiter Area Deutsch Français English Login") is True
    assert _is_ui_vocabulary("Vis mer") is True
    assert _is_ui_vocabulary("Anna Schmidt") is False
    assert _is_ui_vocabulary("Jean-Luc Picard") is False


# ----------------------------------------------------- the Impressum's info@

IMPRESSUM = """
<html><body>
<h1>Impressum</h1>
<p>Geschäftsführer: Anna Schmidt</p>
<p>E-Mail: info@firma.de</p>
</body></html>
"""

IMPRESSUM_PERSONAL = """
<html><body>
<h1>Impressum</h1>
<p>Geschäftsführerin: Anna Schmidt</p>
<p>E-Mail: a.schmidt@firma.de</p>
</body></html>
"""


def test_a_shared_inbox_is_not_attributed_to_the_named_director():
    hits = impressum.extract(IMPRESSUM, "https://firma.de/impressum")
    assert hits, "the director should still be found"
    assert hits[0].email == "", "info@ belongs to the company, not to Anna Schmidt"


def test_a_personal_address_on_the_impressum_still_is_attributed():
    hits = impressum.extract(IMPRESSUM_PERSONAL, "https://firma.de/impressum")
    assert hits[0].email == "a.schmidt@firma.de"


# ------------------------------------------------------------- merge order


def test_a_personal_address_beats_a_role_account_whichever_arrives_first():
    """/impressum is crawled before /team, and the merge was first-wins, so the
    shared inbox beat the real address and the domain lost its pattern anchor."""
    merged = merge_hits(
        [
            PersonHit(name="Anna Schmidt", email="info@firma.de", strategy="impressum"),
            PersonHit(name="Anna Schmidt", email="a.schmidt@firma.de", strategy="team"),
        ]
    )
    assert len(merged) == 1
    assert merged[0].email == "a.schmidt@firma.de"


def test_an_address_that_echoes_the_name_beats_one_that_does_not():
    merged = merge_hits(
        [
            PersonHit(name="Anna Schmidt", email="p.wolf@firma.de", strategy="team"),
            PersonHit(name="Anna Schmidt", email="a.schmidt@firma.de", strategy="press"),
        ]
    )
    assert merged[0].email == "a.schmidt@firma.de"


def test_email_rank_orders_the_three_cases():
    assert _email_rank("a.schmidt@firma.de", "Anna Schmidt") == 2
    assert _email_rank("p.wolf@firma.de", "Anna Schmidt") == 1
    assert _email_rank("info@firma.de", "Anna Schmidt") == 0
    assert _email_rank("", "Anna Schmidt") == 0
