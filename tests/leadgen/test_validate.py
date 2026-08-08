"""Email validation ladder: syntax, role accounts, MX, catch-all."""

from __future__ import annotations

import pytest

from leadgen.email import validate as V


@pytest.mark.parametrize(
    "email",
    [
        "info@acme.de",
        "jobs@acme.de",
        "karriere@acme.de",
        "hr@acme.de",
        "bewerbung@acme.de",
        "contact@acme.fr",
        "empleo@acme.es",
        "no-reply@acme.de",
        "info-de@acme.de",
        "jobs2024@acme.de",
    ],
)
def test_role_accounts_are_rejected(email):
    assert V.is_role_account(email) is True


@pytest.mark.parametrize("email", ["anna.schmidt@acme.de", "j.doe@acme.co.uk", "a@b.io"])
def test_personal_addresses_are_not_role_accounts(email):
    assert V.is_role_account(email) is False


@pytest.mark.parametrize("email", ["anna.schmidt@acme.de", "a+tag@sub.example.co.uk", "x_y@d-e.io"])
def test_valid_syntax(email):
    assert V.valid_syntax(email) is True


@pytest.mark.parametrize("email", ["", "no-at-sign", "@acme.de", "a@", "a@b", "a b@acme.de", "a@acme..de"])
def test_invalid_syntax(email):
    assert V.valid_syntax(email) is False


def test_validate_rejects_role_account_before_touching_the_network(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("network must not be touched for a role account")

    monkeypatch.setattr(V, "has_mx", explode)
    verdict = V.validate("info@acme.de", smtp=False)
    assert verdict.status == "rejected"
    assert "role account" in verdict.reason


def test_validate_drops_a_domain_with_no_mx(monkeypatch):
    monkeypatch.setattr(V, "has_mx", lambda domain: False)
    verdict = V.validate("anna@dead.de", smtp=False)
    assert verdict.status == "rejected"
    assert "mx" in verdict.reason.lower()


def test_validate_returns_unknown_when_smtp_is_disabled(monkeypatch):
    monkeypatch.setattr(V, "has_mx", lambda domain: True)
    assert V.validate("anna@acme.de", smtp=False).status == "unknown"


def test_validate_flags_catch_all(monkeypatch):
    monkeypatch.setattr(V, "has_mx", lambda domain: True)
    monkeypatch.setattr(V, "is_catch_all", lambda domain: True)
    assert V.validate("anna@acme.de", smtp=True).status == "catch_all"


def test_validate_reports_unknown_when_the_probe_is_refused(monkeypatch):
    monkeypatch.setattr(V, "has_mx", lambda domain: True)
    monkeypatch.setattr(V, "is_catch_all", lambda domain: None)
    assert V.validate("anna@acme.de", smtp=True).status == "unknown"


def test_validate_passes_a_clean_domain(monkeypatch):
    monkeypatch.setattr(V, "has_mx", lambda domain: True)
    monkeypatch.setattr(V, "is_catch_all", lambda domain: False)
    assert V.validate("anna@acme.de", smtp=True).status == "ok"


def test_mx_lookups_are_cached(monkeypatch):
    calls = []

    def fake_resolve(domain, record_type):
        calls.append(domain)
        return ["mx1"]

    monkeypatch.setattr(V, "_resolve", fake_resolve)
    V.has_mx.cache_clear()
    V.has_mx("acme.de")
    V.has_mx("acme.de")
    assert calls == ["acme.de"], "second lookup must come from the cache"
    V.has_mx.cache_clear()
