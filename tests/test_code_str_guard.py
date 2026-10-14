import csv
import json
import tempfile
import unittest
from pathlib import Path

import generate_dashboard as gd
from daily_picks import main as dp
from daily_picks import pool as P


class KoreanCodeStringTest(unittest.TestCase):
    def test_dashboard_reads_kr_codes_as_zero_padded_strings(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            with open(out / "latest_kr_full.csv", "w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["종목코드", "종목명", "시장"])
                w.writerows([["005930", "삼성전자", "KOSPI"], ["000660", "SK하이닉스", "KOSPI"], ["247540", "에코프로비엠", "KOSDAQ"]])   # 전부 숫자 코드
            old = gd.OUTPUT_DIR
            gd.OUTPUT_DIR = out
            try:
                rows = gd.load_fresh_rows("kr")
            finally:
                gd.OUTPUT_DIR = old
            self.assertEqual([r["code"] for r in rows], ["005930", "000660", "247540"])
            self.assertTrue(all(isinstance(r["code"], str) for r in rows))

    def test_us_symbols_stay_strings(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            with open(out / "latest_us_full.csv", "w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["종목코드", "종목명", "시장"])
                w.writerows([["AAPL", "Apple", "US"], ["0700", "숫자 티커", "US"]])
            old = gd.OUTPUT_DIR
            gd.OUTPUT_DIR = out
            try:
                rows = gd.load_fresh_rows("us")
            finally:
                gd.OUTPUT_DIR = old
            self.assertEqual([r["code"] for r in rows], ["AAPL", "0700"])             # 미국은 0 채움 없이 원문 유지

    def test_daily_picks_refuses_broken_kr_codes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs" / "data").mkdir(parents=True)
            rows = [{"code": 5930 + i, "name": "x", "status": "OK"} for i in range(20)]                  # 숫자로 깨진 코드
            (root / "docs" / "data" / "latest_kr.json").write_text(json.dumps(rows), encoding="utf-8")
            with self.assertRaises(ValueError):
                P.load("kr", root)
            ok = [{"code": f"{5930 + i:06d}", "name": "x", "status": "OK"} for i in range(20)]
            (root / "docs" / "data" / "latest_kr.json").write_text(json.dumps(ok), encoding="utf-8")
            self.assertEqual(len(P.load("kr", root)["sepaRows"]), 20)


if __name__ == "__main__":
    unittest.main()
