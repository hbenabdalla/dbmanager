"""LDAP certificate host-name checks accept IP subject alternative names (ldap3's fallback on
Python 3.12+ refuses every IP address) and still refuse wrong names."""

import pytest
from ldap3.utils.tls_backport import CertificateError

from app.services.ldap_auth import match_hostname

CERT = {
    "subject": ((("commonName", "dc01.corp.example"),),),
    "subjectAltName": (
        ("DNS", "dc01.corp.example"),
        ("IP Address", "10.0.0.5"),
        ("IP Address", "2001:DB8:0:0:0:0:0:1\n"),
    ),
}


def test_ip_hosts_match_ip_sans_only() -> None:
    match_hostname(CERT, "10.0.0.5")
    match_hostname(CERT, "2001:db8::1")
    match_hostname(CERT, "[2001:db8::1]")
    with pytest.raises(CertificateError):
        match_hostname(CERT, "10.0.0.6")
    # an IP never matches through the common name or a DNS entry
    with pytest.raises(CertificateError):
        match_hostname({"subject": ((("commonName", "10.0.0.7"),),)}, "10.0.0.7")


def test_dns_hosts_keep_ldap3_matching() -> None:
    from ldap3.utils.tls_backport import match_hostname as backport

    match_hostname(CERT, "dc01.corp.example", backport)
    with pytest.raises(CertificateError):
        match_hostname(CERT, "evil.example", backport)


def test_the_check_is_installed_in_ldap3() -> None:
    import ldap3.core.tls as ldap3_tls

    from app.services import ldap_auth

    ldap_auth._server({"server_uri": "ldaps://10.0.0.5", "verify_cert": True})
    assert ldap3_tls.match_hostname is match_hostname
    ldap_auth._install_hostname_check()  # idempotent
    assert ldap3_tls.match_hostname is match_hostname
