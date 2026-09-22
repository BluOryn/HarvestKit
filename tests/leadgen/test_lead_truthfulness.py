"""A wrong cell is worse than an empty one.

Every case here is a way the pipeline previously put something in the
deliverable that it had no evidence for: somebody else's email address against
a person's name, or an `email_status` that claimed more than the probe
established. An empty cell costs one lead. A confidently wrong cell costs the
client's credibility with a real person who never gave them their address.
"""

from __future__ import annotations

from leadgen.person.strategies import team

FLAT_TEAM_PAGE = """
<html><body><div class="team">
  <h3>Anna Meier</h3>
  <p>Geschäftsführerin</p>
  <a href="mailto:anna.meier@firma.de">Mail</a>
  <a href="https://www.linkedin.com/in/anna-meier/">LinkedIn</a>
  <h3>Bernd Schulz</h3>
  <p>Leiter Personal</p>
  <h3>Clara Vogt</h3>
  <p>CTO</p>
</div></body></html>
"""

CARDED_TEAM_PAGE = """
<html><body><div class="team">
  <div class="card">
    <h3>Anna Meier</h3><p>Geschäftsführerin</p>
    <a href="mailto:anna.meier@firma.de">Mail</a>
  </div>
  <div class="card">
    <h3>Bernd Schulz</h3><p>Leiter Personal</p>
    <a href="mailto:bernd.schulz@firma.de">Mail</a>
  </div>
</div></body></html>
"""


def _by_name(hits):
    return {hit.name: hit for hit in hits}


def test_a_shared_container_does_not_hand_one_persons_email_to_everyone():
    """The regression. On a flat team page the "card" wraps every person, so
    `card.find(mailto)` returned Anna's address for Bernd and Clara too."""
    hits = _by_name(team.extract(FLAT_TEAM_PAGE, "https://firma.de/team"))
    assert set(hits) == {"Anna Meier", "Bernd Schulz", "Clara Vogt"}
    assert hits["Anna Meier"].email == "anna.meier@firma.de", "her own address still belongs to her"
    assert hits["Bernd Schulz"].email == ""
    assert hits["Clara Vogt"].email == ""


def test_a_shared_container_does_not_hand_one_persons_linkedin_to_everyone():
    hits = _by_name(team.extract(FLAT_TEAM_PAGE, "https://firma.de/team"))
    assert "anna-meier" in hits["Anna Meier"].linkedin
    assert hits["Bernd Schulz"].linkedin == ""
    assert hits["Clara Vogt"].linkedin == ""


def test_properly_carded_pages_still_get_their_addresses():
    """The guard must not cost the common, well-marked-up case."""
    hits = _by_name(team.extract(CARDED_TEAM_PAGE, "https://firma.de/team"))
    assert hits["Anna Meier"].email == "anna.meier@firma.de"
    assert hits["Bernd Schulz"].email == "bernd.schulz@firma.de"


def test_roles_are_still_read_per_person_on_a_flat_page():
    hits = _by_name(team.extract(FLAT_TEAM_PAGE, "https://firma.de/team"))
    assert hits["Anna Meier"].role == "Geschäftsführerin"
    assert hits["Bernd Schulz"].role == "Leiter Personal"


def test_the_last_person_on_a_flat_page_still_gets_their_own_address():
    """Scoping is forward-looking and bounded by the next person, so the final
    entry is not left out."""
    page = FLAT_TEAM_PAGE.replace(
        "<p>CTO</p>",
        '<p>CTO</p><a href="mailto:c.vogt@firma.de">V</a>',
    )
    hits = _by_name(team.extract(page, "https://firma.de/team"))
    assert hits["Clara Vogt"].email == "c.vogt@firma.de"
    assert hits["Bernd Schulz"].email == "", "Clara's address is not Bernd's"


def test_umlauts_do_not_defeat_the_name_match():
    assert team._address_echoes_name("mueller@firma.de", "Jürgen Müller")
    assert team._address_echoes_name("j.mueller@firma.de", "Jürgen Müller")
    assert not team._address_echoes_name("info@firma.de", "Jürgen Müller")


def test_short_name_particles_do_not_match_by_accident():
    """ "de" inside "verkauf@" must not read as a hit on "Jan de Vries"."""
    assert not team._address_echoes_name("verkauf@firma.nl", "Jan de Vries")
    assert team._address_echoes_name("vries@firma.nl", "Jan de Vries")


# --------------------------------------------------------------------------
# email_status must not claim more than the probe established
# --------------------------------------------------------------------------


def _resolve(monkeypatch, *, pattern: str, guess_without_anchor: bool):
    """Run _resolve_email against a domain whose SMTP probe says "ok"."""
    from leadgen import assemble
    from leadgen.email.validate import EmailVerdict
    from leadgen.person.hit import PersonHit

    monkeypatch.setattr(assemble, "validate", lambda *a, **k: EmailVerdict("ok", ""))
    return assemble._resolve_email(
        PersonHit(name="Anna Meier", role="CTO", source_url="https://firma.de/team"),
        assemble.CompanyContext(name="Firma", domain="firma.de", country="DE"),
        "Anna",
        "Meier",
        pattern,
        "high" if pattern else "low",
        True,
        guess_without_anchor,
    )


def test_a_blind_guess_is_never_labelled_verified(monkeypatch):
    """`verified` only ever meant "this domain rejects unknown recipients". For
    an address with no anchor behind it — the modal first.last applied blind —
    stamping that on the row invited a buyer filtering to `verified` to mail a
    column of pure guesses."""
    email, status, evidence = _resolve(monkeypatch, pattern="", guess_without_anchor=True)
    assert email == "anna.meier@firma.de"
    assert status == "inferred_low"
    assert evidence.startswith("guessed:"), "the row must still say it was a guess"


def test_an_anchored_address_on_a_validating_domain_is_still_verified(monkeypatch):
    """The fix must not demote the rows that earned the label."""
    _, status, _ = _resolve(monkeypatch, pattern="first.last", guess_without_anchor=True)
    assert status == "verified"


# --------------------------------------------------------------------------
# an SMTP server refusing to talk to us is not evidence about the recipient
# --------------------------------------------------------------------------


def test_greylisting_is_not_read_as_proof_the_domain_validates(monkeypatch):
    """450 is "try again later". The old test was `code in (250, 251)`, so every
    non-250 — greylisting, rate limits, an IP blocklisting — came back as "this
    domain does per-mailbox validation", which is what promoted guesses."""
    from leadgen.email import validate as v

    v._catch_all_cache.clear()
    monkeypatch.setattr(v, "_mx_host", lambda d: "mx.firma.de")
    monkeypatch.setattr(v, "_probe_catch_all", v._probe_catch_all)
    monkeypatch.setattr(v, "smtplib", _fake_smtplib(450))
    assert v.is_catch_all("firma.de") is None


def test_a_real_no_such_mailbox_reply_still_means_the_domain_validates(monkeypatch):
    from leadgen.email import validate as v

    v._catch_all_cache.clear()
    monkeypatch.setattr(v, "_mx_host", lambda d: "mx.firma.de")
    monkeypatch.setattr(v, "smtplib", _fake_smtplib(550))
    assert v.is_catch_all("firma.de") is False


def test_an_accepting_domain_is_still_catch_all(monkeypatch):
    from leadgen.email import validate as v

    v._catch_all_cache.clear()
    monkeypatch.setattr(v, "_mx_host", lambda d: "mx.firma.de")
    monkeypatch.setattr(v, "smtplib", _fake_smtplib(250))
    assert v.is_catch_all("firma.de") is True


def test_an_inconclusive_probe_is_not_cached_for_the_rest_of_the_run(monkeypatch):
    """A four-hour run must be allowed to ask again after a momentary block."""
    from leadgen.email import validate as v

    v._catch_all_cache.clear()
    monkeypatch.setattr(v, "_mx_host", lambda d: "mx.firma.de")
    monkeypatch.setattr(v, "smtplib", _fake_smtplib(421))
    assert v.is_catch_all("firma.de") is None
    assert "firma.de" not in v._catch_all_cache

    monkeypatch.setattr(v, "smtplib", _fake_smtplib(550))
    assert v.is_catch_all("firma.de") is False


def _fake_smtplib(code: int):
    """A stand-in for the smtplib module that answers RCPT with `code`."""

    class _Server:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def ehlo_or_helo_if_needed(self):
            return None

        def mail(self, sender):
            return 250, b"ok"

        def rcpt(self, recipient):
            return code, b"canned"

    class _Module:
        SMTPException = Exception

        @staticmethod
        def SMTP(host, port, timeout=None):  # noqa: N802 — mirrors smtplib's name
            return _Server()

    return _Module
