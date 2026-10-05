import datetime as dt
import tempfile
import unittest
from pathlib import Path

from advisory import cio, config, data, desks, memo

RULES = """
regime:
  risk_on_min: 1
  risk_off_max: -3
rules:
  - id: R4
    group: 환율
    condition: "DXY.chg_1m >= 2"
    message: "달러 강세"
    score: -1
  - id: R8
    group: 리스크심사
    condition: "HY_OAS.pct_1y >= 85"
    message: "스프레드 확대"
    score: -2
"""
TH = {"risk_on_min": 1, "risk_off_max": -3}


def row(market, seg, regime, breadth, ok=True, passed=False, size=0.5, zone="SETUP"):
    return {"code": "X", "name": "X", "market": seg, "status": "OK" if ok else "확인불가", "reason": None if ok else "시세 기준일 불일치",
            "regime": regime, "breadth": breadth, "sizeFactor": size, "passAll": passed, "zone": zone, "entryVerdict": "NO-GO"}


class ParseTest(unittest.TestCase):
    def test_rules_and_thresholds_parse_without_yaml(self):
        r = data.parse_rules(RULES)
        self.assertEqual(r["R8"]["condition"], "HY_OAS.pct_1y >= 85")
        self.assertEqual(r["R4"]["score"], -1)
        self.assertEqual(data.parse_regime(RULES), TH)


class CioTest(unittest.TestCase):
    def macro(self, regime="risk_off", score=-3):
        return {"regime": regime, "score": score, "flips": [{"id": "R8", "gain": 2, "condition": "HY_OAS.pct_1y >= 85"}]}

    def segs(self, **kw):
        base = {"KOSPI": {"regime": "YELLOW", "breadth": 0.57}, "KOSDAQ": {"regime": "RED", "breadth": 0.87}, "US": {"regime": "GREEN", "breadth": 0.26}}
        base.update(kw)
        return base

    def test_score_and_label_thresholds(self):
        d = cio.decide(self.macro(), self.segs(), TH)
        self.assertEqual((d["score"], d["stance"]), (-2, "neutral"))
        self.assertEqual((d["toOffense"], d["toDefense"]), (5, 1))
        bad = cio.decide(self.macro(), self.segs(US={"regime": "RED", "breadth": 0.2}), TH)
        self.assertEqual(bad["stance"], "defense")
        good = cio.decide({"regime": "risk_on", "score": 3, "flips": []}, self.segs(KOSPI={"regime": "GREEN", "breadth": 0.7}, KOSDAQ={"regime": "GREEN", "breadth": 0.7}, US={"regime": "GREEN", "breadth": 0.7}), TH)
        self.assertEqual(good["stance"], "offense")

    def test_flip_resolves_macro_and_changes_stance_when_it_matters(self):
        d = cio.decide(self.macro(), self.segs(), TH)
        self.assertTrue(any("R8 해소" in f and "중립" in f for f in d["flips"]))
        # 방어 직전(-3)에서 R8이 해소되면 국면이 바뀌어 입장이 올라간다
        edge = cio.decide(self.macro(), self.segs(US={"regime": "RED", "breadth": 0.2}), TH)
        self.assertTrue(any("R8 해소" in f and "→ 중립" in f for f in edge["flips"]))

    def test_conflict_and_hold_when_data_missing(self):
        d = cio.decide(self.macro(), self.segs(), TH)
        self.assertTrue(d["conflict"]["negative"] and d["conflict"]["positive"])
        miss = cio.decide({"regime": None}, {"US": {"regime": None, "breadth": None}}, TH)
        self.assertEqual(miss["parts"], [])
        self.assertIn("매크로 국면", miss["held"])
        self.assertEqual(miss["stance"], "neutral")  # 점수 0이지만 근거가 없다는 것을 held로 알린다

    def test_changes_vs_previous(self):
        a = cio.decide(self.macro(), self.segs(), TH)
        b = cio.decide(self.macro(), self.segs(US={"regime": "RED", "breadth": 0.2}), TH)
        out = cio.changes({"stance": a}, b)
        self.assertTrue(any("입장" in x for x in out) and any("US 시장 국면" in x for x in out))
        self.assertEqual(cio.changes(None, b), ["비교할 이전 메모가 없습니다"])


class DesksTest(unittest.TestCase):
    def test_korea_is_split_into_kospi_and_kosdaq(self):
        inp = {"rows": {"kr": [row("kr", "KOSDAQ", "RED", 0.87) for _ in range(3)] + [row("kr", "KOSPI", "YELLOW", 0.57) for _ in range(2)],
                        "us": [row("us", "US", "GREEN", 0.26, ok=False) for _ in range(5)]}}
        d, stats = desks.market_desk(inp)
        self.assertEqual((stats["KOSDAQ"]["regime"], stats["KOSPI"]["regime"]), ("RED", "YELLOW"))
        self.assertEqual(stats["US"]["ok"], 0)
        self.assertTrue(any("US" in g and "분석 가능 종목이 없습니다" in g for g in d["gaps"]))
        self.assertTrue(any("국면 불일치" in b for b in d["bullets"]))

    def test_macro_desk_flags_stale_and_lists_flips(self):
        m = {"regime": "risk_off", "regime_label": "리스크오프", "score": -3, "sepa_note": "관망", "generated_at": "2026-09-20 09:00 KST",
             "signals": [{"id": "R8", "group": "리스크심사", "message": "스프레드 확대", "score": -2}],
             "indicators": {"US10Y": {"name": "미 10년", "value": 5.2, "kind": "rate", "chg_1w": 6, "chg_1m": 45, "pct_1y": 98}}}
        d, info = desks.macro_desk({"macro": m, "rules": data.parse_rules(RULES)}, dt.datetime(2026, 10, 5, tzinfo=dt.timezone.utc))
        self.assertTrue(any("일 전" in g for g in d["gaps"]))
        self.assertEqual(info["flips"][0]["condition"], "HY_OAS.pct_1y >= 85")

    def test_macro_desk_handles_missing_data(self):
        d, info = desks.macro_desk({"macro": None, "rules": {}}, dt.datetime.now(dt.timezone.utc))
        self.assertIsNone(info["regime"])
        self.assertTrue(d["gaps"])


class MemoTest(unittest.TestCase):
    def test_build_on_empty_repo_does_not_crash_and_says_so(self):
        with tempfile.TemporaryDirectory() as t:
            m = memo.build("daily", dt.date(2026, 10, 5), root=Path(t), out_dir=Path(t) / "out")
            self.assertEqual(m["stance"]["parts"], [])
            self.assertTrue(any(d["gaps"] for d in m["desks"]))
            self.assertIn("투자 자문 메모", memo.to_markdown(m))

    def test_save_is_write_once_but_latest_always_updates(self):
        with tempfile.TemporaryDirectory() as t:
            out = Path(t)
            m = {"kind": "daily", "key": "2026-10-05", "date": "2026-10-05", "title": "t", "disclaimer": "d", "dataAsOf": {"macro": "x"},
                 "stance": {"label": "중립", "score": 0, "summary": ["a"], "parts": [], "changes": ["c"], "flips": []}, "desks": []}
            self.assertTrue(memo.save(m, out))
            m2 = dict(m, title="t2")
            self.assertFalse(memo.save(m2, out))
            import json
            self.assertEqual(json.loads((out / "latest.json").read_text())["title"], "t2")
            self.assertEqual(json.loads((out / "daily" / "2026-10-05.json").read_text())["title"], "t")
            self.assertFalse(memo.save(dict(m, key="2026-10-06"), out, archive=False))  # preview는 보관하지 않는다
            self.assertEqual(len(json.loads((out / "index.json").read_text())["memos"]), 1)


if __name__ == "__main__":
    unittest.main()
