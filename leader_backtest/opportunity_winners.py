"""Research-only KR winner/failure study; never changes live rules.

Frozen analysis protocol: six features, winner minus failure means, Holm
correction, 20-session calendar block bootstrap and ticker-cluster sensitivity.
2026 is a previously viewed temporal holdout, not a blind test.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from .main import ROOT, fetch_ohlcv
from .rules import features
from .opportunity import classify
from .opportunity_v2 import candidate_filters
from .opportunity_run import _universe

KEYS = ['rs', 'rsi', 'volRatio', 'pivDist', 'trend', 'risk']
V2 = ['v2_leader', 'v2_breakout', 'v2_pullback', 'v2_controlled_risk']
DEST = ROOT / 'research/leader_backtest/opportunity_winners'
SEED = 20261109

def episodes(panel, mask, horizon=20):
    """Select before checking future availability; session is original bar index."""
    chosen = []
    for _, g in panel.sort_values(['code', 'session']).groupby('code'):
        last = -horizon - 1
        for i, r in g.iterrows():
            if bool(mask.loc[i]) and r.session - last > horizon:
                chosen.append(i)
                last = r.session
    return panel.loc[chosen].copy()

def label(excess):
    return np.select([excess >= 10, excess <= -5], ['winner', 'failure'], default='neutral')

def holm(ps):
    a = np.asarray(ps, float)
    order = np.argsort(a)
    out = np.empty(len(a))
    out[order] = np.minimum(1, np.maximum.accumulate(a[order] * np.arange(len(a), 0, -1)))
    return out.tolist()

def contrast(p, key, calendar, n=2000, block=20, ticker=False):
    """Ratio-of-sums preserves episode weighting and contemporaneous dependence.

    Null-centered bootstrap p is approximate, not an exact randomization test.
    Empty calendar dates remain zero-count rows (no compressed time axis).
    """
    q = p[p.group.isin(['winner', 'failure'])].copy().reset_index(drop=True)
    x = pd.to_numeric(q[key], errors='coerce').replace([np.inf, -np.inf], np.nan)
    q = q.loc[x.notna()].copy(); x = x.loc[q.index].astype(float)
    for group, prefix in [('winner', 'w'), ('failure', 'f')]:
        m = q.group.eq(group)
        q[prefix+'sum'] = x.where(m, 0)
        q[prefix+'n'] = m.astype(int)
    cols = ['wsum', 'wn', 'fsum', 'fn']
    a = (q.groupby('code')[cols].sum() if ticker else
         q.groupby('date')[cols].sum().reindex(calendar, fill_value=0)).to_numpy(float)
    total = a.sum(axis=0)
    if total[1] == 0 or total[3] == 0 or len(a) < 2:
        return {'difference': None, 'ci95': None, 'p': 1.0}
    observed = total[0]/total[1] - total[2]/total[3]
    rng = np.random.default_rng(SEED)
    draws = []
    for _ in range(n):
        if ticker:
            idx = rng.integers(len(a), size=len(a))
        else:
            starts = rng.integers(len(a), size=int(np.ceil(len(a)/block)))
            idx = ((starts[:, None]+np.arange(block)) % len(a)).ravel()[:len(a)]
        s = a[idx].sum(axis=0)
        if s[1] and s[3]:
            draws.append(s[0]/s[1]-s[2]/s[3])
    draws = np.asarray(draws)
    return {'difference': float(observed), 'ci95': np.quantile(draws, [.025,.975]).tolist(),
            'p': float((1+np.sum(np.abs(draws-observed)>=abs(observed)))/(len(draws)+1))}

def summary(p):
    x = p.excess.to_numpy(float)
    if not len(x): return {'n': 0}
    return {'n': len(p), 'codes': p.code.nunique(), 'mean_pp': float(x.mean()),
            'median_pp': float(np.median(x)), 'p10_pp': float(np.quantile(x,.1)),
            'winner_pct': float(100*np.mean(x>=10)), 'failure_pct': float(100*np.mean(x<=-5)),
            'positive_pct': float(100*np.mean(x>0)),
            'cost_mean_pp': {str(bp): float(x.mean()-bp/100) for bp in [0,20,50,100]}}

def feature_report(p, calendar):
    out = {}
    for k in KEYS:
        out[k] = {'groups': {g: {'n': int(pd.to_numeric(q[k]).notna().sum()),
                                  'mean': float(q[k].astype(float).mean()),
                                  'median': float(q[k].astype(float).median())}
                            for g,q in p.groupby('group')},
                  'date_block': contrast(p,k,calendar),
                  'ticker_cluster': contrast(p,k,calendar,ticker=True)}
    adjusted = holm([out[k]['date_block']['p'] for k in KEYS])
    for k,a in zip(KEYS, adjusted): out[k]['holm_p'] = a
    return out

def fit_rule(train):
    """Single-feature search, train only. At least 20% retained and 100 rows.

    Score is mean excess minus baseline; selection optimism is NOT a p-value.
    """
    choices = []
    for key in KEYS:
        for threshold in sorted(set(train[key].astype(float).quantile([.25,.5,.75]))):
            for direction in ['ge','le']:
                m = apply_rule(train, {'key':key,'threshold':threshold,'direction':direction})
                if m.sum() >= max(100, .2*len(train)) and m.sum() < .95*len(train):
                    choices.append((float(train.loc[m,'excess'].mean()),key,float(threshold),direction,int(m.sum())))
    if not choices: return None
    score,key,threshold,direction,count = max(choices)
    return {'key':key,'threshold':threshold,'direction':direction,'train_mean_pp':score,
            'train_n':count,'searched_candidates':len(choices)}

def apply_rule(p, rule):
    if rule is None: return pd.Series(False, index=p.index)
    x = p[rule['key']].astype(float)
    return x.ge(rule['threshold']) if rule['direction']=='ge' else x.le(rule['threshold'])

def uplift(p, mask, calendar):
    paired = pd.concat([p.loc[mask].assign(group='winner'),p.assign(group='failure')])
    return contrast(paired, 'excess', calendar)

def analyze(p, calendar):
    p = p.copy(); p['group'] = label(p.excess)
    years = {}
    for y,q in p.groupby(p.date.dt.year):
        cal = calendar[calendar.year==y]
        years[str(y)] = {'summary':summary(q), 'features':feature_report(q,cal)}
    # Purge training outcomes that cross the split; no test labels enter fit.
    folds = {}
    for year in [2025,2026]:
        split = pd.Timestamp(f'{year}-01-01')
        train = p[(p.date<split)&(p.exit_date<split)]
        test = p[(p.date>=split)&(p.date<pd.Timestamp(f'{year+1}-01-01'))]
        rule = fit_rule(train)
        folds[str(year)] = {'rule':rule,'train':summary(train),'test_baseline':summary(test),
                            'test_selected':summary(test[apply_rule(test,rule)]),
                            'uplift_block':uplift(test,apply_rule(test,rule),calendar[calendar.year==year])}
    comparisons = {}
    # Apply all filters to SAME baseline episodes: isolates selection effects.
    for name in V2:
        comparisons[name] = {str(y):summary(q[q[name]]) for y,q in p.groupby(p.date.dt.year)}
        comparisons[name]['all'] = summary(p[p[name]])
        comparisons[name]['uplift_block'] = uplift(p,p[name],calendar)
    return {'summary':summary(p),'groups':{g:summary(q) for g,q in p.groupby('group')},
            'features':feature_report(p,calendar),'years':years,'walk_forward':folds,
            'v2_same_episodes':comparisons}

def main():
    import yfinance as yf
    DEST.mkdir(parents=True, exist_ok=True)
    universe = _universe('kr')
    data = fetch_ohlcv(list(universe), start='2023-01-01')
    if len(data) < .9*len(universe): raise RuntimeError('Less than 90% universe coverage')
    benchmarks = {}
    for key,symbol in [('ks','^KS11'),('kq','^KQ11')]:
        raw = yf.download(symbol,start='2023-01-01',auto_adjust=True,progress=False)
        c = raw['Close']; c = c.iloc[:,0] if isinstance(c,pd.DataFrame) else c
        c.index = pd.DatetimeIndex(c.index).tz_localize(None)
        benchmarks[key] = c.dropna()
    frames = []
    # Freeze raw input snapshot for exact reruns; exclude unclosed 2026-11-09 bar.
    cutoff = pd.Timestamp(os.getenv('OW_CUTOFF','2026-11-07'))
    for code,bars in data.items():
        bars = bars[bars.index<cutoff].copy(); data[code] = bars
        bars.to_csv(DEST / (code+'.csv'))
        f = features(bars); f['date']=f.index; f['code']=code
        f['session']=np.arange(len(f)); frames.append(f.reset_index(drop=True))
    for k,b in benchmarks.items():
        benchmarks[k] = b[b.index<cutoff]; benchmarks[k].to_csv(DEST/('benchmark_'+k+'.csv'))
    panel = pd.concat(frames,ignore_index=True)
    panel['rs'] = panel.groupby('date').ret252.rank(pct=True)*100
    panel = classify(panel)
    panel = pd.concat([panel,candidate_filters(panel)],axis=1)
    panel = panel[panel.date>=pd.Timestamp('2024-01-01')]
    # Selection precedes ALL forward-return availability checks.
    e = episodes(panel, panel.opportunity)
    rows = []
    excluded = {'immature':0,'benchmark_missing':0}
    for _,r in e.iterrows():
        bars=data[r.code]; i=int(r.session)
        if i+21>=len(bars): excluded['immature']+=1; continue
        entry,exit_ = bars.index[i+1],bars.index[i+21]
        b=benchmarks['kq' if r.code.endswith('.KQ') else 'ks']
        if entry not in b.index or exit_ not in b.index:
            excluded['benchmark_missing']+=1; continue
        ret=100*(bars.close.iloc[i+21]/bars.close.iloc[i+1]-1)
        bench=100*(b.loc[exit_]/b.loc[entry]-1)
        row=r.to_dict(); row.update(entry_date=entry,exit_date=exit_,return_pp=float(ret),benchmark_pp=float(bench),excess=float(ret-bench))
        rows.append(row)
    p=pd.DataFrame(rows)
    if p.empty: raise RuntimeError('No matured episodes')
    p.to_csv(DEST/'episodes.csv',index=False)
    calendar=benchmarks['ks'].index.union(benchmarks['kq'].index)
    calendar=calendar[(calendar>=p.date.min())&(calendar<=p.date.max())]
    result=analyze(p,calendar)
    result['audit']={'requested_universe':len(universe),'downloaded':len(data),'missing':sorted(set(universe)-set(data)),
                     'pre_maturity_episodes':len(e),'excluded':excluded,'cutoff_exclusive':str(cutoff.date()),
                     'previous_reference_n':5289,'delta_reference':len(p)-5289,
                     'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                     'ran_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
                     'input_hashes':{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in DEST.glob('*.csv')}}
    result['limitations']=[
        'Current-constituent universe and download availability cause survivorship/selection bias; not corrected.',
        'Next-session CLOSE to 20 sessions later CLOSE; not next-open executable backtest.',
        'Adjusted historical OHLCV may be revised. Raw snapshot and hashes archived.',
        'Episode count need not equal 5289: fixed cutoff, feed revisions, true session cooldown, selection before maturity.',
        'Features use trailing data; cross-sectional RS uses current survivors. No historical constituent reconstruction.',
        '2026 previously inspected: temporal OOS only, not blind. V2 was designed after viewing historical data.',
        'Bootstrap dependence adjustment approximate; ticker and date resampling separately do not solve all dependence.',
        'Feature p values are retrospective association, not causal or validated filter alpha; annual tests exploratory.',
        'Flat roundtrip 20/50/100bp sensitivity only; no actual spread, impact, taxes, portfolio capacity or capital returns.',
        'No historical flows or sector features added; missing history is not replaced with current values.'
    ]
    (DEST/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))

if __name__=='__main__': main()
