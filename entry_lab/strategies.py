"""Causal prefix evaluation; thresholds fixed before this study's results.

OHLC must be split-consistent. Data/portfolio approval is a separate boundary.
Targets are observed overhead resistance, never an invented 2R price.
"""
from datetime import date
import math
import pandas as pd

VERSION = "3.0.1-research"
RULES = dict(pivotDays=5, maxRisk=.07, minStopATR=.75, stopBufferATR=.25,
             maxExtension=.03, minRR=2, volumeExpansion=1.2, qualityMin=2,
             impulseReturn=.15, impulseVolume=2, resetMinDays=5, resetMaxDays=30)


def evaluate(frame, benchmark, *, as_of, strategy="early", event=None):
    """Only bars dated <= as_of are read. Returns DEFER on incomplete evidence."""
    if strategy not in ("early", "catalyst"):
        raise ValueError("unknown strategy")
    out = dict(strategyId=f"{strategy}:{VERSION}", version=VERSION,
               experimental=True, productionEligible=False, signalDate=as_of,
               priceAsOf=None, verdict="DEFER", grade="F", reasons=[], quality={},
               entry=None, stop=None, target1=None, target2=None, rr=None)
    try:
        date.fromisoformat(as_of)
        f = frame.loc[frame.index <= pd.Timestamp(as_of)].copy()
        if len(f) < 65 or f.index.has_duplicates or not f.index.is_monotonic_increasing:
            raise ValueError("missing/duplicate/unsorted history")
        if str(f.index[-1])[:10] != as_of:
            raise ValueError("stale price date")
        f = f[["Open", "High", "Low", "Close", "Volume"]].astype(float)
        if not all(math.isfinite(v) for v in f.to_numpy().ravel()):
            raise ValueError("nonfinite OHLCV")
        if ((f.Low <= 0) | (f.Volume <= 0) | (f.High < f[["Open", "Close", "Low"]].max(axis=1))
                | (f.Low > f[["Open", "Close"]].min(axis=1))).any():
            raise ValueError("invalid OHLCV")
        if not isinstance(benchmark,pd.Series):
            raise ValueError("invalid benchmark type")
        past_benchmark=benchmark.loc[benchmark.index<=pd.Timestamp(as_of)]
        if past_benchmark.index.has_duplicates or not past_benchmark.index.is_monotonic_increasing:
            raise ValueError("duplicate/unsorted benchmark")
        b = past_benchmark.reindex(f.index)
        if (not all(math.isfinite(float(v)) for v in b.tail(21))
                or b.tail(21).isna().any() or (b.tail(21) <= 0).any()):
            raise ValueError("missing same-date benchmark")
        out["priceAsOf"] = as_of
        p = f.iloc[:-1]
        c = float(f.Close.iloc[-1])
        tr = pd.concat([p.High-p.Low, (p.High-p.Close.shift()).abs(),
                        (p.Low-p.Close.shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.tail(20).mean())
        base = p.tail(5)
        pivot = float(base.High.max())
        support = float(base.Low.min())
        stop = support - RULES["stopBufferATR"] * atr
        risk = c-stop
        sma20 = float(p.Close.tail(20).mean())
        quality = dict(higherLow=support > float(p.Low.iloc[-10:-5].min()),
                       compression=(pivot-support) <= 3*atr,
                       sellingVolumeDown=float(base.Volume.mean()) < float(p.Volume.tail(20).mean()),
                       volumeExpansion=float(f.Volume.iloc[-1]) >= RULES["volumeExpansion"]*float(p.Volume.tail(20).mean()),
                       rsImproving=float(f.Close.iloc[-1]/f.Close.iloc[-21]) > float(b.iloc[-1]/b.iloc[-21]))
        reasons = []
        if not (c > sma20 and sma20 >= float(p.Close.iloc[-25:-5].mean())):
            reasons.append("trend recovery unconfirmed")
        if c <= pivot:
            reasons.append("short pivot not broken")
        if c/pivot-1 > RULES["maxExtension"]:
            reasons.append("chase extension")
        if not (stop > 0 and RULES["minStopATR"]*atr <= risk <= RULES["maxRisk"]*c):
            reasons.append("structural stop too tight or too distant")
        if sum(quality.values()) < RULES["qualityMin"]:
            reasons.append("quality evidence insufficient")
        # Local peaks confirmed by two following bars, all in the prior prefix.
        highs = p.High.to_numpy()
        resistances = sorted({float(highs[i]) for i in range(max(2,len(p)-120),len(p)-2)
                              if highs[i] >= max(highs[i-2:i+3]) and highs[i] > c})
        target = resistances[0] if resistances else None
        rr = (target-c)/risk if target is not None and risk > 0 else None
        if rr is None or rr < RULES["minRR"]:
            reasons.append("observed resistance lacks 2R room")
        breaches = int((p.Low.tail(20) < support).sum())
        impulse = None
        if strategy == "catalyst":
            for i in range(len(f)-RULES["resetMaxDays"]-1, len(f)-RULES["resetMinDays"]):
                if i < 20:
                    continue
                if (f.Close.iloc[i]/f.Close.iloc[i-1]-1 >= RULES["impulseReturn"]
                        and f.Volume.iloc[i] >= RULES["impulseVolume"]*f.Volume.iloc[i-20:i].mean()):
                    impulse = i
            if impulse is None:
                reasons.append("no prior impulse and reset")
            else:
                reset = f.iloc[impulse+1:-1]
                if reset.empty or reset.Low.min() >= f.Close.iloc[impulse] or reset.Volume.mean() >= f.Volume.iloc[impulse]:
                    reasons.append("first pullback/dry-up unconfirmed")
            # Price jump alone does not prove clinical/earnings/news catalyst.
            if not (isinstance(event, dict) and event.get("verified") is True
                    and isinstance(event.get("source"), str) and event["source"].startswith("https://")
                    and isinstance(event.get("publishedDate"), str)
                    and impulse is not None and event["publishedDate"] <= str(f.index[impulse])[:10]
                    and event.get("date") == str(f.index[impulse])[:10]):
                reasons.append("dated official catalyst evidence missing")
        out.update(entry=c, stop=stop, pivot=pivot, stopBasis="prior 5-session low minus 0.25 prior ATR20",
                   atr=atr, stopATR=risk/atr if atr > 0 else None, supportBreachCount20=breaches,
                   target1=target, target2=resistances[1] if len(resistances)>1 else None,
                   targetBasis="confirmed prior 120-session swing highs", rr=rr,
                   riskPct=risk/c*100, quality=quality, reasons=reasons,
                   impulseDate=str(f.index[impulse])[:10] if impulse is not None else None,
                   verdict="CANDIDATE" if not reasons else "DEFER",
                   grade="B" if not reasons else "D", largestRisk=reasons[0] if reasons else "unvalidated research expectancy")
    except (ValueError, TypeError, KeyError, IndexError, ZeroDivisionError) as exc:
        out["reasons"] = [str(exc)]
    return out
