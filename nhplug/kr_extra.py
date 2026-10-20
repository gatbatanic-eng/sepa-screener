"""�ѱ� ���� ���� ������ ����(��ȸ ����: nhplug.probe�� ��� ����� �״�� ����). (python -m nhplug.kr_extra --mode auto|full|flow)
full: ������ �������ð��Ѿס�PER/PBR �� research/nhplug/kr_extra.json (�� 1ȸ, �� 10��)
flow: �ѱ� �������� �ܱ��Ρ���������� ���ż� �� �Ϻ� ������ research/nhplug/flow_history/�� �����ϰ�, 5��20�ŷ��� �հ踦 research/nhplug/kr_flow.json�� ���� (����, �� 12��)
���� �ʵ� ����(2026-10-06, ���̹� ����� 5����10��): ���(gigwan)������(person)�� ���� ��ġ, �ܱ����� invest�� ����(���� ����), frgn_ntby_qty�� �ٸ� ��.
�׷��� �ܱ����� invest�� ���⡤�뷫�� ũ��θ� ����."""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import requests

from .probe import call, kr_period, rows, token

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "research" / "nhplug" / "kr_extra.json"
FLOW_OUT = ROOT / "research" / "nhplug" / "kr_flow.json"
CHECK = ROOT / "research" / "nhplug" / "probe" / "investor_check.json"
CHECK_CODES = ("005930", "000660", "247540", "035420", "005380")


def _i(v):
    try:
        return int(float(str(v).replace(",", "").replace("+", "")))
    except (TypeError, ValueError):
        return None


def _f(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def universe() -> list[tuple[str, str]]:
    rows_ = json.loads((ROOT / "docs" / "data" / "latest_kr.json").read_text(encoding="utf-8"))
    return [(r["code"], "4" if r.get("market") == "KOSDAQ" else "1") for r in rows_ if r.get("code")]


def investor(tok: str, code: str) -> list[dict]:
    r = call(tok, "/krstock/quote/v1/currentInvestor", {"market_cd": "KRX", "iem_cd": code, "array_cnt": "030"})
    got = rows(r, "Output_0")
    if not got:
        raise RuntimeError("NHPLUG investor output empty")
    if not any(len(str(x.get("bsop_date1") or "")) == 8 and
               all(_i(x.get(k)) is not None for k in ("invest", "gigwan", "person")) for x in got):
        raise RuntimeError("NHPLUG investor dates/flow fields missing")
    return got


def summarize_flow(inv: list[dict], kst_today: str | None = None) -> dict:
    """�ֱټ� ���� ����(ù ���� �ֽ�). ����(KST) ��¥ ���� �� ���� ���� ���� ���� �� �־� ����.
    frgn=invest(�ܱ���, �ٻ�), inst=gigwan(���), indiv=person(����). 5��20�ŷ��� ���ż� ���� �հ�."""
    if kst_today:
        inv = [x for x in inv if str(x.get("bsop_date1")) != kst_today]
    out = {"days": len(inv), "asOf": str(inv[0].get("bsop_date1")) if inv else None}
    for key, name in (("invest", "frgn"), ("gigwan", "inst"), ("person", "indiv")):
        vals = [_i(x.get(key)) for x in inv]
        for n in (5, 20):
            part = [v for v in vals[:n] if v is not None]
            out[f"{name}{n}"] = sum(part) if len(part) == n else None
    return out


def naver_trend(code: str) -> dict[str, dict]:
    """���̹� ����� ������ ����(���ڡ�ܱ���/���/���� ���Ÿŷ�). �������̶� �����ϸ� �� ��."""
    try:
        r = requests.get(f"https://m.stock.naver.com/api/stock/{code}/trend", timeout=20,
                         headers={"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"})
        r.raise_for_status()
        return {x["bizdate"]: {"foreign": _i(x.get("foreignerPureBuyQuant")), "organ": _i(x.get("organPureBuyQuant")),
                               "person": _i(x.get("individualPureBuyQuant"))} for x in r.json()}
    except Exception as e:  # noqa: BLE001 - ������
        return {"error": repr(e)[:200]}


def check_fields(tok: str) -> dict:
    res = {}
    for code in CHECK_CODES:
        inv, nv = investor(tok, code), None
        nv = naver_trend(code)
        if "error" in nv:
            res[code] = nv
            continue
        stat = {"days": 0, "frgnA==naver": 0, "frgnB==naver": 0, "gigwan==naverOrgan": 0, "person==naverPerson": 0}
        samples = []
        for x in inv:
            n = nv.get(str(x.get("bsop_date1")))
            if not n:
                continue
            stat["days"] += 1
            stat["frgnA==naver"] += _i(x.get("frgn_ntby_qty")) == n["foreign"]
            stat["frgnB==naver"] += _i(x.get("invest")) == n["foreign"]
            stat["gigwan==naverOrgan"] += _i(x.get("gigwan")) == n["organ"]
            stat["person==naverPerson"] += _i(x.get("person")) == n["person"]
            if len(samples) < 3:
                samples.append({"date": x.get("bsop_date1"), "nh": {k: x.get(k) for k in ("frgn_ntby_qty", "invest", "gigwan", "person")}, "naver": n})
        res[code] = {**stat, "samples": samples}
    return res


def run_full(tok: str) -> int:
    stocks, errors = {}, 0
    uni = universe()
    for i, (code, mk) in enumerate(uni):
        try:
            p = kr_period(tok, code, mk, dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d"), 1)
            o0 = ((p["data"] or {}).get("Output_0")) or {}
            if isinstance(o0, list):
                o0 = o0[0] if o0 else {}
            stocks[code] = {"name": str(o0.get("iem_nm") or "").lstrip("*#"), "sector": o0.get("bstp_kor_isnm"), "sectorCode": o0.get("bstp_cls_code"),
                            "marcapEok": _i(o0.get("hts_avls")), "per": _f(o0.get("per")), "pbr": _f(o0.get("pbr")), "asOf": str(o0.get("bsop_date") or "")}
        except Exception as e:  # noqa: BLE001 - ���� �ϳ� ���а� ��ü�� ���� �ʰ�
            errors += 1
            stocks[code] = {"error": repr(e)[:120]}
        if i % 100 == 0:
            print(f"full {i}/{len(uni)} ���� {errors}", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"schemaVersion": 2, "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "stocks": stocks}, ensure_ascii=False), encoding="utf-8")
    print("����:", OUT, "����", len(stocks), "����", errors)
    return 0 if errors < len(stocks) * 0.2 else 1


SECTOR_EXTRA = ROOT / "research" / "nhplug" / "kr_sector_extra.json"
SECTOR_REFRESH_DAYS = 30


def sector_topup_codes(root: Path = ROOT) -> list[tuple[str, str]]:
    """���� ���Ϲ��� �������� ���� �Ͼ����(�򶧱�) ������ ���� ���� �� ������ ���� �𸣴� ����(�Ǵ� 30�� �Ѱ� ���� ����)."""
    try:
        base = {c for c, _ in universe()}
        top = json.loads((root / "docs" / "research" / "funnel_kr.json").read_text(encoding="utf-8")).get("top") or []
    except (OSError, ValueError):
        return []
    have = {}
    try:
        have = json.loads(SECTOR_EXTRA.read_text(encoding="utf-8")).get("stocks") or {}
    except (OSError, ValueError):
        pass
    today = dt.datetime.now(dt.timezone.utc).date()
    out = []
    for t in top:
        code = str(t.get("symbol"))
        if code in base:
            continue
        v = have.get(code)
        if v and v.get("sector") and (today - dt.date.fromisoformat(v["fetchedAt"])).days < SECTOR_REFRESH_DAYS:
            continue
        out.append((code, "4" if str(t.get("exchange") or "").upper().startswith("KOSDAQ") else "1"))
    return out


def run_sector_topup(tok: str) -> int:
    """�򶧱� ���� �ĺ� �� ���� ���Ͽ� ���� ���� �޾� kr_sector_extra.json�� ���Ѵ�(ȣ�� ���� ��). kr_extra.json�� �ǵ帮�� �ʴ´�."""
    todo = sector_topup_codes()
    if not todo:
        return 0
    try:
        stocks = json.loads(SECTOR_EXTRA.read_text(encoding="utf-8")).get("stocks") or {}
    except (OSError, ValueError):
        stocks = {}
    today = dt.datetime.now(dt.timezone.utc)
    errors = 0
    for code, mk in todo:
        try:
            p = kr_period(tok, code, mk, today.strftime("%Y%m%d"), 1)
            o0 = ((p["data"] or {}).get("Output_0")) or {}
            if isinstance(o0, list):
                o0 = o0[0] if o0 else {}
            stocks[code] = {"name": str(o0.get("iem_nm") or "").lstrip("*#"), "sector": o0.get("bstp_kor_isnm"), "sectorCode": o0.get("bstp_cls_code"),
                            "fetchedAt": today.date().isoformat()}
        except Exception as e:  # noqa: BLE001
            errors += 1
            print("���� ���� ����", code, repr(e)[:80])
    SECTOR_EXTRA.parent.mkdir(parents=True, exist_ok=True)
    SECTOR_EXTRA.write_text(json.dumps({"schemaVersion": 1, "generatedAt": today.isoformat(), "stocks": stocks}, ensure_ascii=False), encoding="utf-8")
    print("���� ���� ����:", SECTOR_EXTRA, "����", len(stocks), "�̹� ��û", len(todo), "����", errors)
    return 0


def run_flow(tok: str) -> int:
    from .flowstore import FlowStore
    kst_today = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=9)).strftime("%Y%m%d")
    codes = [c for c, _ in universe()]
    store = FlowStore()
    stocks, errors, added, error_samples = {}, 0, 0, []
    for i, code in enumerate(codes):
        try:
            inv = investor(tok, code)
            added += store.merge(code, inv, exclude_date=kst_today)
            summary = summarize_flow(inv, kst_today)
            if not summary.get("asOf") or summary.get("days", 0) < 1:
                raise RuntimeError("No completed-day investor rows")
            stocks[code] = {**summary, "forRate": _f(inv[0].get("for_rate")) if inv else None}
        except Exception as e:  # noqa: BLE001
            errors += 1
            if len(error_samples) < 10:
                error_samples.append({"code": code, "type": type(e).__name__, "message": str(e)[:160]})
        if i % 100 == 0:
            print(f"flow {i}/{len(codes)} ���� {errors}", flush=True)
    # Publish only a sufficiently complete generation. Preserve the last known
    # generation and history if the service returns HTTP 200 with no outputs.
    good = sum(bool(v.get("asOf")) for v in stocks.values())
    status = {"generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "kstToday": kst_today,
              "requested": len(codes), "received": good, "errors": errors,
              "status": "ok" if codes and good >= len(codes) * .8 else "failed",
              "previousRetained": not (codes and good >= len(codes) * .8), "errorSamples": error_samples}
    FLOW_OUT.parent.mkdir(parents=True, exist_ok=True)
    (FLOW_OUT.parent / "flow_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    if status["status"] != "ok":
        print("���� ���� �ҿ���: ���� ���롤���� �̷� ����", good, "/", len(codes), flush=True)
        return 1
    store.save()
    print("���� �̷� �� ��", added, "������", len(store.sessions()))
    kr_bars: list[str] = []  # �ѱ� ���� ���� �ֱ� ��¥��(��ǥ ���� �Ϻ� 20��). ������ ������ ����(agents/freshness.py)
    try:
        bars = rows(kr_period(tok, "005930", "1", dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d"), 20))
        kr_bars = [str(b.get("bsop_date")) for b in bars if b.get("bsop_date")]
    except Exception as e:  # noqa: BLE001
        print("������ �� ��ȸ ����:", repr(e)[:120])
    FLOW_OUT.parent.mkdir(parents=True, exist_ok=True)
    FLOW_OUT.write_text(json.dumps({"schemaVersion": 1, "krBars": kr_bars, "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "kstToday": kst_today,
                                    "note": "frgn=invest(�ܱ���, ���̹��� ���������� ����), inst=���, indiv=����. ���ż� ���� �հ�. ����(KST) �� ����",
                                    "stocks": stocks}, ensure_ascii=False), encoding="utf-8")
    print("����:", FLOW_OUT, "����", len(stocks), "/", len(codes), "����", errors)
    return 0 if errors < max(1, len(codes)) * 0.2 else 1


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["auto", "full", "flow"], default="auto")
    mode = ap.parse_args().mode
    if mode == "auto":  # �����(UTC)�̰ų� ���� ������ ���ų� 8�� �Ѱ� �������� ������, �ƴϸ� ���޸�
        stale = True
        if OUT.exists():
            try:
                g = dt.datetime.fromisoformat(json.loads(OUT.read_text(encoding="utf-8"))["generatedAt"])
                stale = (dt.datetime.now(dt.timezone.utc) - g).days >= 8
            except (OSError, ValueError, KeyError):
                stale = True
        mode = "full" if stale or dt.datetime.now(dt.timezone.utc).weekday() == 5 else "flow"
    print("mode:", mode)
    tok = token()
    rc = run_full(tok) if mode == "full" else 0
    rc = max(rc, run_flow(tok))  # ������ ���� ������ ������ ���� �޴´�
    try:
        run_sector_topup(tok)  # �����ص� ���� ���� ���(���� �ڵ�)���� ���� ����
    except Exception as e:  # noqa: BLE001
        print("���� ���� �ǳʶ�:", repr(e)[:120])
    sys.exit(rc)


if __name__ == "__main__":
    main()


