import pytest

from src.risk.rr import compute_rr


def test_rr_basic_value():
    # entry=100, stop=90(risk=10), target=130(reward=30) -> RR=3.0
    assert compute_rr(entry=100, stop=90, target=130) == pytest.approx(3.0)


@pytest.mark.parametrize(
    "target,expected",
    [(119.9, 1.99), (120.0, 2.00), (120.1, 2.01)],
)
def test_rr_boundary_values_around_2_0(target, expected):
    # entry=100, stop=90 -> risk=10
    assert compute_rr(entry=100, stop=90, target=target) == pytest.approx(expected)


def test_rr_none_when_risk_is_zero():
    assert compute_rr(entry=100, stop=100, target=120) is None


def test_rr_none_when_risk_is_negative():
    # stop이 entry보다 위에 있으면(잘못된 스탑) 계산 불가
    assert compute_rr(entry=100, stop=105, target=120) is None
