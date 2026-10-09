"""Read-only retrospective study. Run with --collect once, then replay saved inputs.

Current survivor universe/subset RS are explicit limitations; not historical
production verdicts. Corporate actions and data availability are unverified.
"""
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import statistics
import subprocess
import os
import tempfile
from zoneinfo import ZoneInfo
import pandas as pd
from aggressive_screen import evaluate as aggressive
from sepa.config import SepaConfig
from sepa.pipeline import evaluate_stock_v2
from sepa.rs import RsV2
from entry_lab.strategies import evaluate, RULES, VERSION
from entry_lab.execution import simulate

ROOT=Path(__file__).resolve().parents[1]
CASES="MRNA AMD BE CRDO FFIV KEYS LITE A SNDK DDOG".split()
SECTORS={"technology":"AAPL MSFT NVDA CRM ORCL INTC", "healthcare":"JNJ PFE UNH ABBV",
         "financial":"JPM BAC GS C", "consumer":"WMT COST AMZN TSLA",
         "energy":"XOM CVX OXY", "industrial":"CAT GE BA",
         "utilities":"NEE DUK", "materials":"NEM FCX"}


def dump(path,value):
    path=Path(path)
    payload=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(",",":"),default=str)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,prefix='.entry-lab-',delete=False) as h:
            temporary=Path(h.name);h.write(payload);h.flush();os.fsync(h.fileno())
        os.link(temporary,path)  # exclusive publication; existing artifacts are untouched
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


def collect(path):
    import yfinance as yf
    universe=sorted({r["code"] for r in json.loads((ROOT/"docs/research/aggressive_us.json").read_text(encoding="utf-8"))["latestRows"]})
    fixed=set(CASES+[s for group in SECTORS.values() for s in group.split()])
    symbols=sorted(fixed|set(random.Random(31010).sample([s for s in universe if s not in fixed],20)))
    result=dict(observedAt=datetime.now(timezone.utc).isoformat(),source="Yahoo/yfinance auto_adjust=False repair=False",
                splitBasis="provider retrospectively split-adjusted; independent action verification absent",
                symbols=symbols,selectionSeed=31010,prices={},failures=[],universe=universe)
    for sym in symbols+["^GSPC"]:
        try:
            f=yf.download(sym.replace(".","-"),start="2021-01-01",end="2026-10-10",auto_adjust=False,
                          repair=False,progress=False,threads=False)
            if isinstance(f.columns,pd.MultiIndex): f.columns=f.columns.get_level_values(0)
            f=f[["Open","High","Low","Close","Volume"]]
            if f.empty or f.isna().any(axis=None): raise ValueError("empty or null feed")
            result["prices"][sym]=dict(dates=[str(d)[:10] for d in f.index],values=f.values.tolist())
        except Exception as e:
            result["failures"].append(dict(symbol=sym,error=type(e).__name__+": "+str(e)[:160]))
    dump(path,result)


def summary(trades):
    closed=[t for t in trades if t["execution"]["status"]=="CLOSED"]
    filled=[t for t in trades if t["execution"]["status"] in ("CLOSED","OPEN")]
    xs=[t["execution"]["returnPct"] for t in closed]
    return dict(signals=len(trades),executionStatuses=dict(Counter(t["execution"]["status"] for t in trades)),
                closed=len(xs),winRate=sum(x>0 for x in xs)/len(xs) if xs else None,
                stopRate=sum(t["execution"]["reason"] in ("STOP","GAP_STOP") for t in closed)/len(xs) if xs else None,
                meanReturnPct=statistics.mean(xs) if xs else None,
                averageLossPct=statistics.mean([x for x in xs if x<0]) if any(x<0 for x in xs) else None,
                meanRealizedR=statistics.mean(t["execution"]["realizedR"] for t in closed) if closed else None,
                meanPlannedRR=statistics.mean(t["rr"] for t in trades if t.get("rr") is not None) if any(t.get("rr") is not None for t in trades) else None,
                horizons={str(h):dict(n=len(v),meanPct=statistics.mean(v)*100 if v else None)
                          for h in (5,20,40) for v in [[t["execution"].get("horizons",{}).get(str(h)) for t in filled if t["execution"].get("horizons",{}).get(str(h)) is not None]]})


def run(input_path,output_path,shard=None):
    source=json.loads(input_path.read_text(encoding="utf-8"))
    frames={s:pd.DataFrame(x["values"],columns=["Open","High","Low","Close","Volume"],
                         index=pd.to_datetime(x["dates"])) for s,x in source["prices"].items()}
    observed=datetime.fromisoformat(source["observedAt"]).astimezone(ZoneInfo("America/New_York"))
    # Conservative 16:10 ET cutoff also delays early-close sessions; never admits
    # a partially formed current-day Yahoo bar. Original raw input is preserved.
    include_today=(observed.hour,observed.minute)>=(16,10)
    frames={s:f.loc[(f.index.date<observed.date()) |
                   ((f.index.date==observed.date()) & include_today)] for s,f in frames.items()}
    benchmark=frames.pop("^GSPC").Close
    cfg=SepaConfig()
    # Exactly the original 21/63/126/252 weights, but on this declared subset.
    closes=pd.DataFrame({s:f.Close for s,f in frames.items()})
    ranks={k:(closes.pct_change(k,fill_method=None).sub(benchmark.pct_change(k),axis=0)).rank(axis=1,pct=True)*100 for k in (21,63,126,252)}
    score=sum(ranks[k]*w for k,w in [(21,.1),(63,.4),(126,.3),(252,.2)])
    all_signals=[]; diagnoses={}; rejected=Counter(); regimes={}
    for d in benchmark.index:
        if str(d)[:10]<"2022-01-01": continue
        regimes[str(d)[:10]]="RED" if benchmark.loc[d]<benchmark.loc[:d].tail(200).mean() else "NON_RED"
    for symbol_number,(symbol,f) in enumerate(frames.items()):
        if shard and symbol_number % shard[1] != shard[0]: continue
        print("evaluate",symbol,flush=True)
        stop_until={}; series=[]
        for pos in range(252,len(f)):
            d=f.index[pos]; day=str(d)[:10]
            if day<"2022-01-01" or d not in score.index: continue
            prefix=f.iloc[:pos+1]; c=float(prefix.Close.iloc[-1]); p=prefix.Close
            a50=float(p.tail(50).mean()); a150=float(p.tail(150).mean()); a200=float(p.tail(200).mean())
            hi=float(prefix.High.tail(252).max()); lo=float(prefix.Low.tail(252).min())
            cond=[c>a150 and c>a200,a150>a200,a200>p.iloc[-220:-20].mean(),
                  a50>a150 and a50>a200,c>a50,c>=lo*cfg.trend.low_52w_mult,c>=hi*cfg.trend.high_52w_mult]
            rsvalue=score.at[d,symbol]
            rsold=score.iloc[score.index.get_loc(d)-20][symbol]
            rs=RsV2(rs_score=float(rsvalue) if pd.notna(rsvalue) else None,
                    rs_change_20d=float(rsvalue-rsold) if pd.notna(rsvalue) and pd.notna(rsold) else None,
                    rs_line_new_high=bool((p/benchmark.reindex(p.index)).iloc[-1]>=(p/benchmark.reindex(p.index)).tail(126).max()))
            # When TREND fails, original entry priority guarantees TREND_FAIL or FAILED, never GO.
            if all(cond) and rs.rs_score is not None and rs.rs_score>=cfg.rs.rs_min:
                old=evaluate_stock_v2(prefix,cfg,cond_1_7=cond,close_today=c,high_52w=hi,rs=rs)
            else:
                old=dict(entry_state="TREND_FAIL",entry_state_reason=["original TREND conditions or subset RS fail"])
            ag=aggressive(dict(code=symbol,status="OK",inUniverse=True,rsScore=rs.rs_score,
                               regime=regimes.get(day)),prefix,"us")
            signals=[]
            for kind in ("early","catalyst"):
                x=evaluate(prefix,benchmark,as_of=day,strategy=kind)
                rejected.update(f"{kind}:{r}" for r in x["reasons"])
                if x["verdict"]=="CANDIDATE": signals.append(dict(x,kind=kind))
                if symbol in CASES and day>="2026-08-01":
                    series.append(dict(date=day,strategy=kind,verdict=x["verdict"],reasons=x["reasons"],entry=x["entry"],stop=x["stop"],rr=x["rr"],pivot=x.get("pivot"),
                                       originalSepa=old,originalAggressive=ag))
            if old["entry_state"] in ("GO_BREAKOUT","GO_PULLBACK"):
                signals.append(dict(kind="sepa",signalDate=day,entry=c,stop=old.get("structural_stop_price"),pivot=old.get("pivot_price"),rr=None))
            if ag.get("aggressiveGo") is True:
                signals.append(dict(kind="aggressive",signalDate=day,entry=c,stop=ag.get("referenceStop"),pivot=ag.get("breakoutLevel"),rr=None))
            for signal in signals:
                kind=signal["kind"]
                if day<=stop_until.get(kind,""): continue
                e=simulate(f,signal)
                if e["status"]=="CLOSED": stop_until[kind]=e["exitDate"]
                elif e["status"]=="OPEN": stop_until[kind]="9999-12-31"
                else: stop_until[kind]=str(f.index[min(pos+1,len(f)-1)])[:10]
                if e["status"]=="CLOSED":
                    ei=f.index.get_loc(pd.Timestamp(e["entryDate"])); xi=f.index.get_loc(pd.Timestamp(e["exitDate"]))
                    e["benchmarkExcessPct"]=e["returnPct"]-(float(benchmark.loc[f.index[xi]]/benchmark.loc[f.index[ei]]-1)*100)
                all_signals.append(dict(signal,symbol=symbol,execution=e,regime=regimes.get(day),provenance="RECOMPUTED_SUBSET_RS"))
        diagnoses[symbol]=series
    stored={}
    for name,file in [("sepa","us.json"),("aggressive","aggressive_us.json")]:
        feed=json.loads((ROOT/"docs/research"/file).read_text(encoding="utf-8"))
        stored[name]=[s for s in feed["signals"] if s["code"] in CASES]
    result=dict(version=VERSION,rules=RULES,sourceCommit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
                inputSHA256=hashlib.sha256(input_path.read_bytes()).hexdigest(),inputMetadata={k:v for k,v in source.items() if k!="prices"},
                warnings=["retrospective provider prices; corporate actions and historical availability unverified",
                          "current survivors and fixed cases plus seeded random sample; subset RS != historical production RS",
                          "price returns exclude dividends; daily liquidity/halts/calendar incompleteness",
                          "2022-2024 retrospective in-sample, 2025-2026 retrospective holdout; both observed at design time, not prospective OOS",
                          "catalyst trading deferred absent dated official evidence; no automatic production promotion",
                          "portfolio MDD unavailable: independent trade simulation is not an account equity curve"],
                improvementProven=False,productionEligible=False,storedHistoricalSignals=stored,recomputedCaseDiagnostics=diagnoses,
                signals=all_signals,rejections=dict(rejected),strategies={},
                sourceHashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/"aggressive_screen.py",*sorted((ROOT/"sepa").glob("*.py"))]})
    for kind in ("sepa","aggressive","early","catalyst"):
        trades=[t for t in all_signals if t["kind"]==kind]
        result["strategies"][kind]=dict(all=summary(trades),
             periods={label:summary([t for t in trades if lo<=t["signalDate"]<=hi]) for label,lo,hi in [("in_sample","2022-01-01","2024-12-31"),("retrospective_holdout","2025-01-01","2026-10-09")]},
             years={str(y):summary([t for t in trades if t["signalDate"].startswith(str(y))]) for y in range(2022,2027)},
             sectors={sector:summary([t for t in trades if t["symbol"] in symbols.split()]) for sector,symbols in SECTORS.items()},
             regimes={r:summary([t for t in trades if t["regime"]==r]) for r in ("RED","NON_RED")})
    dump(output_path,result)
    print(json.dumps(result["strategies"],ensure_ascii=True),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--input",type=Path,required=True)
    parser.add_argument("--output",type=Path);parser.add_argument("--collect",action="store_true")
    parser.add_argument("--shard",help="index/count; ranks still use entire sample")
    args=parser.parse_args()
    if args.collect: collect(args.input)
    if args.output: run(args.input,args.output,tuple(map(int,args.shard.split('/'))) if args.shard else None)
