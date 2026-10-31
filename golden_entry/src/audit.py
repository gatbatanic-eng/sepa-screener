"""Phase 1 감사 실행기. 수집 가능한 것만 수집해 data/cache/audit_status.json 에 결과를 남긴다."""
import json, datetime as dt
import pandas as pd
from src import data

def main():
    st = {"run_at": dt.datetime.now().isoformat(timespec="seconds"), "snapshots_ok": 0, "snapshots_fail": 0}
    for y in range(2013, 2027):
        d = f"{y}1228"
        for m in data.CFG["collect"]["markets"]:
            df = data.ticker_snapshot(d, m)
            st["snapshots_ok" if df is not None else "snapshots_fail"] += 1
    dl = data.delisting_list(); st["delisting_rows"] = None if dl is None else len(dl)
    for code, name in (("1001", "KOSPI"), ("2001", "KOSDAQ")):
        ix = data.index_ohlcv(code, name, "2013-01-01", dt.date.today().isoformat())
        st[f"index_{name}_rows"] = None if ix is None else len(ix)
    json.dump(st, open(data.CACHE / "audit_status.json", "w"), ensure_ascii=False, indent=1)
    print(st)

if __name__ == "__main__": main()
