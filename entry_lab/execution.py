"""Daily long simulation with next-session open and adverse same-bar ordering."""
import math


def simulate(frame, signal, *, cost_bp=10, hold=40, max_risk=.07, max_extension=.05):
    if (isinstance(cost_bp,bool) or not isinstance(cost_bp,(int,float)) or not math.isfinite(cost_bp)
            or not 0<=cost_bp<=1000 or isinstance(hold,bool) or not isinstance(hold,int) or hold<1):
        raise ValueError("invalid cost/holding policy")
    date = signal["signalDate"]
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing or date not in frame.index:
        return dict(status="SKIP",reason="missing/duplicate/unsorted signal session")
    expected=float(frame.loc[date,"Close"])
    if not isinstance(signal.get("entry"),(int,float)) or not math.isfinite(signal["entry"]) or abs(signal["entry"]-expected)>max(.001,expected*.000001):
        return dict(status="SKIP",reason="signal price does not match dated close")
    tail = frame.loc[frame.index > date].iloc[:hold]
    if tail.empty:
        return dict(status="PENDING", reason="no next session")
    first = tail.iloc[0]
    stop, pivot = signal.get("stop"), signal.get("pivot") or signal.get("entry")
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0
               for v in (stop, pivot, float(first.Open), float(first.Volume))):
        return dict(status="SKIP", reason="missing stop/pivot/next-open/volume")
    raw = float(first.Open)
    fee = cost_bp/10000
    entry = raw*(1+fee)
    if raw <= stop or (entry-stop)/entry > max_risk or raw/pivot-1 > max_extension:
        return dict(status="SKIP", reason="next-open invalidation/risk/chase")
    target = signal.get("target1")
    if target is not None and (target-entry)/(entry-stop) < 2:
        return dict(status="SKIP", reason="next-open resistance below 2R")
    path, reason, exit_price, end = [], "HORIZON", None, None
    for d, bar in tail.iterrows():
        if not all(math.isfinite(float(v)) and float(v)>0 for v in (bar.Open,bar.High,bar.Low,bar.Close,bar.Volume)):
            return dict(status="UNAVAILABLE", reason="missing holding session")
        if bar.High<max(bar.Open,bar.Close,bar.Low) or bar.Low>min(bar.Open,bar.Close):
            return dict(status="UNAVAILABLE",reason="invalid holding OHLC")
        end = str(d)[:10]
        # Opening events are known to precede intraday events.
        if float(bar.Open) <= stop:
            exit_price, reason = float(bar.Open), "GAP_STOP"
        elif target is not None and float(bar.Open) >= target:
            exit_price, reason = float(bar.Open), "GAP_TARGET"
        elif float(bar.Low) <= stop:
            exit_price, reason = stop, "STOP"
        elif target is not None and float(bar.High) >= target:
            exit_price, reason = target, "TARGET"
        else:
            path.append(dict(date=end, price=float(bar.Close)))
            continue
        # Do not include full exit-day highs/lows: order relative to exit unknown.
        path.append(dict(date=end, price=exit_price))
        break
    if exit_price is None:
        if len(tail) < hold:
            return dict(status="OPEN", entry=entry, entryDate=str(tail.index[0])[:10], path=path)
        exit_price = float(tail.Close.iloc[-1])
    net = exit_price*(1-fee)/entry-1
    prices = [raw]+[x["price"] for x in path]
    horizons = {}
    full = frame.loc[frame.index > date]
    for h in (5,20,40):
        horizons[str(h)] = float(full.Close.iloc[h-1])*(1-fee)/entry-1 if len(full)>=h else None
    return dict(status="CLOSED", entry=entry, rawEntry=raw, entryDate=str(tail.index[0])[:10],
                exitDate=end, exit=exit_price*(1-fee), reason=reason, returnPct=net*100,
                realizedR=(exit_price*(1-fee)-entry)/(entry-stop), horizons=horizons,
                mfeClosePct=(max(prices)/raw-1)*100, maeClosePct=(min(prices)/raw-1)*100,
                excursionBasis="observed closes and exit; not full intraday MFE/MAE", path=path)


def portfolio(trades, frames, sessions, *, fraction=.05, max_positions=20):
    """Cash-based independent strategy account; intraday exit cash not reused.

    Entry size is prior close equity. Fractional shares are a research assumption.
    Closed plus still-open fills are marked using exact session close prices.
    """
    cash=100.; prior=100.; peak=100.; mdd=0.; book={}; curve=[]; skips=[]
    orders={}
    for i,t in enumerate(trades):
        e=t["execution"]
        if e["status"] in ("CLOSED","OPEN"):
            orders.setdefault(e["entryDate"],[]).append((i,t))
    for d in sessions:
        day=str(d)[:10]
        for i,t in sorted(orders.get(day,[]),key=lambda pair:pair[1]["symbol"]):
            e=t["execution"]; debit=fraction*prior
            if len(book)>=max_positions or cash<debit or any(x["symbol"]==t["symbol"] for x in book.values()):
                skips.append(dict(date=day,symbol=t["symbol"],reason="cash/slots/symbol limit"));continue
            book[i]=dict(symbol=t["symbol"],quantity=debit/e["entry"],execution=e)
            cash-=debit
        for i,lot in list(book.items()):
            e=lot["execution"]
            if e.get("exitDate")==day:
                cash+=lot["quantity"]*e["exit"]; del book[i]
        value=cash
        for lot in book.values():
            f=frames[lot["symbol"]]
            if d not in f.index:
                return dict(status="UNAVAILABLE",reason="missing portfolio mark",date=day)
            value+=lot["quantity"]*float(f.loc[d,"Close"])
        if value<=0:
            return dict(status="INSOLVENT",equity=value,date=day)
        peak=max(peak,value);mdd=min(mdd,value/peak-1);prior=value
        curve.append(dict(date=day,equity=value,cash=cash,positions=len(book)))
    return dict(status="COMPLETE",initialEquity=100,endingEquity=prior,
                returnPct=prior-100,mddPct=mdd*100,curve=curve,skips=skips,
                assumptions="5% prior equity; max20; fractional shares; no intraday exit-cash reuse")
