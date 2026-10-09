"""Merge deterministic research shards; create compact reviewer artifacts."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import gzip
import json
from pathlib import Path
import random
import statistics
import pandas as pd
from zoneinfo import ZoneInfo
from entry_lab.study import summary, dump, SECTORS, CASES
from entry_lab.execution import simulate, portfolio
from entry_lab.strategies import evaluate


def finalize(input_path,shards,output_dir):
    results=[json.loads(p.read_text(encoding="utf-8")) for p in shards]
    first=results[0]; all_signals=[]; diagnostics={}; rejected=Counter()
    for r in results:
        if r["inputSHA256"]!=first["inputSHA256"] or r["sourceHashes"]!=first["sourceHashes"] or r["rules"]!=first["rules"]:
            raise ValueError("shard provenance mismatch")
        if set(diagnostics)&set(r["recomputedCaseDiagnostics"]):raise ValueError("duplicate shard symbols")
        all_signals+=r["signals"];diagnostics.update(r["recomputedCaseDiagnostics"]);rejected.update(r["rejections"])
    source=json.loads(input_path.read_text(encoding="utf-8"))
    if hashlib.sha256(input_path.read_bytes()).hexdigest()!=first["inputSHA256"]:raise ValueError("input hash mismatch")
    frames={s:pd.DataFrame(x["values"],columns=["Open","High","Low","Close","Volume"],index=pd.to_datetime(x["dates"])) for s,x in source["prices"].items()}
    observed=datetime.fromisoformat(source["observedAt"]).astimezone(ZoneInfo("America/New_York"))
    today=(observed.hour,observed.minute)>=(16,10)
    frames={s:f.loc[(f.index.date<observed.date())|((f.index.date==observed.date()) & today)] for s,f in frames.items()}
    bench=frames.pop("^GSPC")
    if set(diagnostics)!=set(frames):raise ValueError("incomplete shard coverage")
    strategies={};cases={};timing=[]
    for t in all_signals:
        t["execution"]=simulate(frames[t["symbol"]],t)
        e=t["execution"]
        if e["status"]=="CLOSED":
            e["benchmarkExcessPct"]=e["returnPct"]-float(bench.loc[e["exitDate"],"Close"]/bench.loc[e["entryDate"],"Open"]-1)*100
    for kind in ("sepa","aggressive","early","catalyst"):
        trades=[t for t in all_signals if t["kind"]==kind]
        stats=summary(trades)
        xs=[t["execution"] for t in trades if t["execution"]["status"]=="CLOSED"]
        stats.update(meanBenchmarkExcessPct=statistics.mean(x["benchmarkExcessPct"] for x in xs) if xs else None,
                     meanMFEObservedClosePct=statistics.mean(x["mfeClosePct"] for x in xs) if xs else None,
                     meanMAEObservedClosePct=statistics.mean(x["maeClosePct"] for x in xs) if xs else None,
                     executableRate=sum(t["execution"]["status"] in ("CLOSED","OPEN") for t in trades)/len(trades) if trades else None,
                     breakoutSuccessDefinition="net realized return >0 before stop/target/40-session exit")
        grouped={label:summary([t for t in trades if lo<=t["signalDate"]<=hi]) for label,lo,hi in [("in_sample","2022-01-01","2024-12-31"),("retrospective_holdout","2025-01-01","2026-10-08")]}
        cost={str(bp):summary([dict(t,execution=simulate(frames[t["symbol"]],t,cost_bp=bp)) for t in trades]) for bp in (0,10,25)}
        # Blocks by entry date, not independent ticker-trades, for uncertainty.
        blocks={}
        for t in trades:
            e=t["execution"]
            if e["status"]=="CLOSED":blocks.setdefault(e["entryDate"],[]).append(e["returnPct"])
        block_means=[statistics.mean(v) for v in blocks.values()];boots=[];rng=random.Random(31010)
        if len(block_means)>=10:
            for _ in range(1000):boots.append(statistics.mean(rng.choices(block_means,k=len(block_means))))
        boots.sort()
        strategies[kind]=dict(all=stats,periods=grouped,costSensitivitySameEpisodes=cost,
                              years={str(y):summary([t for t in trades if t["signalDate"].startswith(str(y))]) for y in range(2022,2027)},
                              sectors={sector:summary([t for t in trades if t["symbol"] in symbols.split()]) for sector,symbols in SECTORS.items()},
                              regimes={r:summary([t for t in trades if t["regime"]==r]) for r in ("RED","NON_RED")},
                              dateBlockMeanCI95=[boots[25],boots[974]] if boots else None,
                              portfolio=portfolio(trades,frames,bench.index[bench.index>=pd.Timestamp("2022-01-01")]))
    for symbol in CASES:
        trades=[t for t in all_signals if t["symbol"]==symbol and t["signalDate"]>="2026-08-01"]
        observations=diagnostics[symbol]
        # First meaningful up-session is descriptive, does not feed detectors.
        f=frames[symbol];changes=f.Close.pct_change()
        start=[str(d)[:10] for d,v in changes.items() if str(d)[:10]>="2026-08-01" and v>=.05]
        earliest={k:next((t for t in trades if t["kind"]==k),None) for k in strategies}
        early_dates=[t["signalDate"] for t in trades if t["kind"]=="early"]
        sepa_dates=[t["signalDate"] for t in trades if t["kind"]=="sepa"]
        paired=[]
        for d in early_dates:
            later=[s for s in sepa_dates if 0<=f.index.get_loc(pd.Timestamp(s))-f.index.get_loc(pd.Timestamp(d))<=40]
            if later: paired.append(f.index.get_loc(pd.Timestamp(min(later)))-f.index.get_loc(pd.Timestamp(d)))
        timing+=paired
        cases[symbol]=dict(firstDescriptiveUp5PctDate=start[0] if start else None,
                           descriptiveDefinition="first daily close gain >=5% since 2026-08-01; not predictive",
                           earliestResearchSignals=earliest,researchTrades=trades,leadSessionsVsLaterSepa=paired,
                           latestResearch=observations[-2:],
                           storedHistorical={k:[s for s in first["storedHistoricalSignals"][k] if s["code"]==symbol] for k in ("sepa","aggressive")},
                           unknownHistoricalVerdicts="absence of an archived episode is not evidence of NO-GO")
    report=dict(version=first["version"],generatedAt=datetime.now(timezone.utc).isoformat(),priceAsOf=str(bench.index[-1])[:10],
                inputSHA256=first["inputSHA256"],sourceCommit=first["sourceCommit"],sourceHashes=first["sourceHashes"],rules=first["rules"],
                sample=dict(n=len(frames),symbols=list(frames),randomSeed=31010,randomCount=20,sectorGroups=SECTORS),
                improvementProven=False,productionEligible=False,strategies=strategies,cases=cases,rejections=dict(rejected),
                leadTiming=dict(pairedN=len(timing),meanSessions=statistics.mean(timing) if timing else None,
                                limitation="only case early signals with later SEPA within40 sessions; unmatched opportunities excluded"),
                limitations=[s for s in first["warnings"] if not s.startswith("portfolio MDD")]+[
                    "bootstrap CI is descriptive; correlated dates, selection and multiple comparisons prevent significance claims",
                    "cost sensitivity reprices frozen baseline episodes; episode rediscovery is not rerun",
                    "portfolio equity is separate from independent trade mean; no dividends, verified actions or exchange calendar",
                    "intraday latest Yahoo bar excluded using conservative 16:10 ET cutoff; raw input preserved"])
    evidence_path=Path(__file__).resolve().parents[1]/"docs/entry_lab/catalyst-evidence.json"
    events=json.loads(evidence_path.read_text(encoding="utf-8"))
    supplemental={}
    for symbol,event in events.items():
        f=frames[symbol]
        supplemental[symbol]=[evaluate(f,bench.Close,as_of=str(d)[:10],strategy="catalyst",event=event)
                               for d in f.index if event["publishedDate"]<=str(d)[:10]]
    report["officialCatalystSupplement"]=dict(evidence=events,observations=supplemental,
         limitation="separate primary-source case check; not a catalyst-universe performance comparison")
    archived={s:[] for s in CASES}
    root=Path(__file__).resolve().parents[1]
    for path in sorted((root/"research/snapshots/us").glob("*.json.gz")):
        snap=json.loads(gzip.decompress(path.read_bytes()))
        groups=snap.get("groups",[])
        config=snap.get("strategy",{}).get("config",{})
        is_sepa=("GO" in groups and "TREND" in groups) or (not groups and all(k in config for k in ("trend","setup","pivot","swing","rs","entry")))
        family="aggressive" if "AGGR_GO" in groups else "sepa" if is_sepa else None
        if family is None:continue
        for row in snap.get("rows",[]):
            if row.get("code") not in archived:continue
            archived[row["code"]].append(dict(family=family,snapshotPath=str(path.relative_to(root)),
               snapshotSHA256=hashlib.sha256(path.read_bytes()).hexdigest(),
               recordedAt=snap.get("recordedAt"),sourceCommit=snap.get("sourceCommit"),
               strategySeriesId=snap.get("strategySeriesId"),
               row={k:row.get(k) for k in ("code","priceAsOf","close","status","entryVerdict","entryState","entryReason",
                    "trendOk","setupReady","baseLength","atrContraction","dryupRatio","initRisk","structStop",
                    "aggressiveGo","aggressiveWatch","initialRiskPct","referenceStop","breakoutLevel","reason","checks")}))
    for s,observations in archived.items():report["cases"][s]["archivedDecisions"]=observations
    report["implementationHashes"]={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in sorted((root/"entry_lab").glob("*.py"))}
    output_dir.mkdir(parents=True,exist_ok=True)
    dump(output_dir/"report-full.json",report)
    # Compact public review payload; full artifacts remain local and hash-linked.
    compact=json.loads(json.dumps(report,default=str))
    compact["fullArtifactSHA256"]=hashlib.sha256((output_dir/"report-full.json").read_bytes()).hexdigest()
    for v in compact["strategies"].values():
        account=v["portfolio"]
        account["skipCount"]=len(account.pop("skips",[]));account.pop("curve",None)
    def trim_trade(t):
        if t:t["execution"].pop("path",None)
        return t
    for c in compact["cases"].values():
        c["earliestResearchSignals"]={k:trim_trade(t) for k,t in c["earliestResearchSignals"].items()}
        c["researchTrades"]=[trim_trade(t) for t in c["researchTrades"]]
        c["storedHistorical"]={k:[{x:s.get(x) for x in ("id","date","group","originalClose","strategySeriesId")} for s in rows] for k,rows in c["storedHistorical"].items()}
        samples=c["archivedDecisions"]
        selected=[]
        for family in ("sepa","aggressive"):
            candidates=[x for x in samples if x["family"]==family]
            if candidates:
                latest=max(candidates,key=lambda x:x["row"].get("priceAsOf") or "")
                selected.append(latest)
                selected.extend([x for x in candidates if x["row"].get("priceAsOf")=="2026-09-22"][:1])
        c["archivedDecisionCount"]=len(samples);c["archivedDecisions"]=selected
    supplement=compact["officialCatalystSupplement"]
    for s,obs in supplement["observations"].items():
        supplement["observations"][s]=[x for x in obs if x["verdict"]=="CANDIDATE"]+obs[-1:]
    compact["limitations"].append("compact page shows selected archived rows; full report retains all archived decisions and equity paths")
    compact["limitations"].append("common 252-bar warmup excludes earlier short-history/IPO early entries")
    dump(output_dir/"report.json",compact)
    dump(output_dir/"episodes.json",dict(inputSHA256=first["inputSHA256"],signals=all_signals))
    print(json.dumps({k:v["all"] for k,v in strategies.items()},ensure_ascii=True))


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--input",type=Path,required=True);p.add_argument("--shards",type=Path,nargs='+',required=True);p.add_argument("--output",type=Path,required=True)
    a=p.parse_args();finalize(a.input,a.shards,a.output)
