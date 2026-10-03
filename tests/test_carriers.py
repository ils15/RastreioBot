import pytest

from apis import apicorreios
from apis import apitrackingmore
from utils.misc import check_type
from utils import status


@pytest.mark.parametrize("code,carrier", (
    ("AB123456789CD", apicorreios),
    ("LP00147037652437", apitrackingmore),
    ("wrong-code", None),
))
def test_get_carrier_by_code(code, carrier):
    assert check_type(code) == carrier


@pytest.mark.parametrize("carriers,sorted_carriers", (
    (
        [{'code': 'correios'}, {'code': 'cainiao'}],
        [{'code': 'cainiao'}, {'code': 'correios'}],
    ),
    (
        [{'code': 'correios'}, {'code': '4px'}],
        [{'code': 'correios'}, {'code': '4px'}],
    ),
))
def test_sort_carriers(carriers, sorted_carriers):
    assert apitrackingmore.sort_carriers(carriers) == sorted_carriers


def test_get_carriers_normalizes_legacy_scalar_code(monkeypatch):
    monkeypatch.setattr(
        apitrackingmore.db,
        "search_package",
        lambda code: {"carrier": "cainiao"},
    )
    monkeypatch.setattr(
        apitrackingmore.trackingmore,
        "detect_carrier_from_code",
        lambda code: pytest.fail("valid cached code should not trigger detection"),
    )

    assert apitrackingmore.get_carriers("LP00147037652437") == [
        {"code": "cainiao"}
    ]


@pytest.mark.parametrize(
    "cached,expected",
    (
        ({"code": "cainiao", "name": "Cainiao"}, [{"code": "cainiao", "name": "Cainiao"}]),
        (
            [{"code": "cainiao", "name": "Cainiao"}, {"code": "4px"}],
            [{"code": "cainiao", "name": "Cainiao"}, {"code": "4px"}],
        ),
    ),
)
def test_get_carriers_preserves_valid_cached_mappings(monkeypatch, cached, expected):
    monkeypatch.setattr(
        apitrackingmore.db,
        "search_package",
        lambda code: {"carrier": cached},
    )
    monkeypatch.setattr(
        apitrackingmore.trackingmore,
        "detect_carrier_from_code",
        lambda code: pytest.fail("valid cached mappings should not trigger detection"),
    )

    assert apitrackingmore.get_carriers("LP00147037652437") == expected


@pytest.mark.parametrize(
    "package",
    (
        None,
        {},
        {"carrier": None},
        {"carrier": ""},
        {"carrier": "  "},
        {"carrier": {}},
        {"carrier": {"name": "Unknown"}},
        {"carrier": []},
        {"carrier": [{"name": "Unknown"}]},
        {"carrier": ["cainiao"]},
        {"carrier": 42},
    ),
)
def test_get_carriers_detects_for_missing_or_invalid_cached_values(
    monkeypatch, package
):
    detected = [{"code": "correios", "name": "Correios"}, {"code": "cainiao"}]
    updates = []
    detections = []
    monkeypatch.setattr(apitrackingmore.db, "search_package", lambda code: package)
    monkeypatch.setattr(
        apitrackingmore.trackingmore,
        "detect_carrier_from_code",
        lambda code: detections.append(code) or detected,
    )
    monkeypatch.setattr(
        apitrackingmore.db,
        "update_package",
        lambda code, **kwargs: updates.append((code, kwargs)),
    )

    assert apitrackingmore.get_carriers("LP00147037652437") == [
        {"code": "cainiao"},
        {"code": "correios", "name": "Correios"},
    ]
    assert detections == ["LP00147037652437"]
    assert updates == [
        (
            "LP00147037652437",
            {
                "carrier": [
                    {"code": "cainiao"},
                    {"code": "correios", "name": "Correios"},
                ]
            },
        )
    ]


def test_get_carriers_does_not_return_malformed_detection_payload(monkeypatch):
    monkeypatch.setattr(
        apitrackingmore.db, "search_package", lambda code: {"carrier": None}
    )
    monkeypatch.setattr(
        apitrackingmore.trackingmore,
        "detect_carrier_from_code",
        lambda code: {"not": "a carrier list"},
    )
    monkeypatch.setattr(apitrackingmore.db, "update_package", lambda *args, **kwargs: None)

    assert apitrackingmore.get_carriers("LP00147037652437") == []


def test_get_passes_normalized_legacy_carrier_code_to_consumer(monkeypatch):
    calls = []
    monkeypatch.setattr(
        apitrackingmore.db,
        "search_package",
        lambda code: {"carrier": "cainiao"},
    )
    monkeypatch.setattr(
        apitrackingmore,
        "get_or_create_tracking_item",
        lambda carrier, code: calls.append((carrier, code)) or {"status": "notfound"},
    )

    assert apitrackingmore.get("LP00147037652437") == status.NOT_FOUND_TM
    assert calls == [("cainiao", "LP00147037652437")]
