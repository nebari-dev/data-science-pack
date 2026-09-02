"""Tests for scripts/preview/keycloak_gitops.py.

Covers the GitOps hostname rewrite for Keycloak's own KC_HOSTNAME -- see
that module's docstring for why this can't just be a live kubectl patch
(ArgoCD selfHeal reverts it).
"""

from __future__ import annotations

from scripts.preview import keycloak_gitops as kg

# --- rewrite_hostname (pure) --------------------------------------------------


def test_rewrite_hostname_replaces_every_occurrence():
    text = (
        "issuerUrl: https://keycloak.nebari.local/realms/nebari\n"
        "redirectUri: https://keycloak.nebari.local/callback\n"
    )

    result = kg.rewrite_hostname(text, "https://keycloak.nebari.local", "https://kc-pr-205.example.com")

    assert "keycloak.nebari.local" not in result
    assert result.count("https://kc-pr-205.example.com") == 2


def test_rewrite_hostname_leaves_unrelated_text_untouched():
    text = "some_other_key: value\n"

    result = kg.rewrite_hostname(text, "https://keycloak.nebari.local", "https://kc-pr-205.example.com")

    assert result == text


# --- find_operator_deployment (pure) ------------------------------------------


def test_find_operator_deployment_matches_name_containing_operator():
    deployments = {
        "items": [
            {"metadata": {"namespace": "kube-system", "name": "coredns"}},
            {"metadata": {"namespace": "nic-system", "name": "nebari-operator-controller"}},
        ]
    }

    namespace, name = kg.find_operator_deployment(deployments)

    assert namespace == "nic-system"
    assert name == "nebari-operator-controller"


def test_find_operator_deployment_raises_when_none_found():
    deployments = {"items": [{"metadata": {"namespace": "kube-system", "name": "coredns"}}]}

    try:
        kg.find_operator_deployment(deployments)
        raise AssertionError("expected OperatorNotFoundError")
    except kg.OperatorNotFoundError:
        pass


# --- extract_env_value (pure) -------------------------------------------------


def test_extract_env_value_finds_named_var():
    env = [{"name": "OTHER", "value": "x"}, {"name": "KC_HOSTNAME", "value": "https://kc.example.com"}]

    assert kg.extract_env_value(env, "KC_HOSTNAME") == "https://kc.example.com"


def test_extract_env_value_returns_empty_string_when_absent():
    assert kg.extract_env_value([{"name": "OTHER", "value": "x"}], "KC_HOSTNAME") == ""


# --- wait_until_both_match (pure, time/sleep injected) ------------------------


def test_wait_until_both_match_returns_true_as_soon_as_both_equal_expected():
    calls = []

    def get_values():
        calls.append(1)
        # Wrong on the first call, correct on the second.
        return ("wrong", "wrong") if len(calls) == 1 else ("expected", "expected")

    fake_time = {"t": 0.0}
    matched, values = kg.wait_until_both_match(
        get_values, "expected", timeout_s=60, poll_interval_s=5,
        clock=lambda: fake_time["t"],
        sleep=lambda s: fake_time.__setitem__("t", fake_time["t"] + s),
    )

    assert matched is True
    assert values == ("expected", "expected")
    assert len(calls) == 2


def test_wait_until_both_match_returns_false_after_timeout():
    fake_time = {"t": 0.0}

    matched, values = kg.wait_until_both_match(
        lambda: ("wrong", "wrong"), "expected", timeout_s=20, poll_interval_s=5,
        clock=lambda: fake_time["t"],
        sleep=lambda s: fake_time.__setitem__("t", fake_time["t"] + s),
    )

    assert matched is False
    assert values == ("wrong", "wrong")


def test_wait_until_both_match_false_when_only_one_side_matches():
    fake_time = {"t": 0.0}

    matched, _ = kg.wait_until_both_match(
        lambda: ("expected", "wrong"), "expected", timeout_s=1, poll_interval_s=5,
        clock=lambda: fake_time["t"],
        sleep=lambda s: fake_time.__setitem__("t", fake_time["t"] + s),
    )

    assert matched is False
