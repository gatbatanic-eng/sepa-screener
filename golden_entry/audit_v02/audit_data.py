"""Offline integrity audit. Never repairs, drops, trades or changes source prices."""
from __future__ import annotations
import argparse
import hashlib
import json
import platform
from pathlib import Path
import numpy as np
import pandas as pd
import pyarrow

VERSION = '0.2.0'
PRICE = ['Open', 'High', 'Low', 'Close']


def hashes(path):
    raw = path.read_bytes()
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
            'git_blob_sha1': hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()}


def verify_sources(data_dir, tree):
    if tree.get('truncated'):
        raise ValueError('truncated source tree')
    entries = {Path(e['path']).name: e for e in tree['tree']
               if e['path'].startswith('golden_entry/data_store/') and e['type'] == 'blob'}
    required = ['listing.parquet', 'delisting.parquet', 'index.parquet', 'fetch_status.parquet']
    required += [f'ohlcv_{y}.parquet' for y in range(2020, 2027)]
    records = []
    for name in required:
        e = entries[name]
        h = hashes(data_dir/name)
        if h['bytes'] != e['size'] or h['git_blob_sha1'] != e['sha']:
            raise ValueError('source mismatch: '+name)
        records.append({'file': name, **h})
    return records


def row_flags(px):
    f = pd.DataFrame(index=px.index)
    finite = pd.Series(np.isfinite(px[PRICE+['Volume']].to_numpy()).all(axis=1), index=px.index)
    f['nonfinite'] = ~finite
    f['zero_volume'] = px.Volume.eq(0)
    f['halt_pattern'] = px[['Open','High','Low']].eq(0).all(axis=1) & f.zero_volume
    f['nonpositive_price'] = px[PRICE].le(0).any(axis=1)
    f['negative_volume'] = px.Volume.lt(0)
    f['ohlc_inconsistent'] = ((px.High < px[['Open','Close','Low']].max(axis=1)) |
                              (px.Low > px[['Open','Close']].min(axis=1))) & ~f.halt_pattern
    # Missing values stay missing; no forward filling before percent changes.
    ret = px.groupby('ticker', sort=False).Close.pct_change(fill_method=None)
    f['jump_over_30pct'] = ret.abs().gt(.30)
    f['mechanically_valid_bar'] = ~(f.nonfinite | f.nonpositive_price |
        f.negative_volume | f.ohlc_inconsistent | f.zero_volume)
    return f, ret


def calendar_differences(px, calendars):
    """Compare date sets rather than counts. Duplicate/missing cancellation is impossible."""
    rows = []
    for (ticker, market), g in px.groupby(['ticker','market'], sort=True):
        actual = pd.DatetimeIndex(g.Date.dropna().unique()).sort_values()
        if actual.empty:
            rows.append({'ticker':ticker,'market':market,'issue':'NO_VALID_DATE','Date':None})
            continue
        cal = calendars.get(market)
        if cal is None:
            rows.append({'ticker':ticker,'market':market,'issue':'NO_MARKET_CALENDAR','Date':None})
            continue
        expected = cal[(cal >= actual.min()) & (cal <= actual.max())]
        for d in expected.difference(actual):
            rows.append({'ticker':ticker,'market':market,'issue':'MISSING_IN_OBSERVED_SPAN','Date':d})
        for d in actual.difference(cal):
            rows.append({'ticker':ticker,'market':market,'issue':'OUTSIDE_INDEX_CALENDAR','Date':d})
    return pd.DataFrame(rows, columns=['ticker','market','issue','Date'])


def zero_runs(px, zero):
    change = px.ticker.ne(px.ticker.shift()) | zero.ne(zero.shift())
    ids = change.cumsum()
    z = px.loc[zero, ['ticker','market','Date']].copy()
    z['run_id'] = ids[zero].to_numpy()
    return z.groupby(['ticker','market','run_id']).agg(
        start=('Date','min'), end=('Date','max'), rows=('Date','size')).reset_index()


def terminal_gap(calendar, last, delisting_date):
    # Delisting day is not presumed to be a tradable final session.
    return int(((calendar > last) & (calendar < delisting_date)).sum())


def classification(status, listing, delisting):
    rows=[]
    for t in status.ticker:
        cur=listing[listing.Code.eq(t)]
        old=delisting[delisting.Symbol.eq(t)]
        if len(cur):
            kind, group, decision, reason = '', '', 'REVIEW_CURRENT_CLASSIFICATION', 'current listing lacks security-group history'
            name=str(cur.iloc[0].Name)
        elif len(old)==1:
            r=old.iloc[0];kind=str(r.Kind);group=str(r.SecuGroup);name=str(r.Name)
            if group=='수익증권':
                decision,reason='EXCLUDE_NON_EQUITY','security-group identifies beneficiary certificate'
            elif group=='주권' and kind=='보통주':
                decision,reason='PROVISIONAL_EQUITY','master classification; historical status still unverified'
            else:
                decision,reason='REVIEW_ASSET_POLICY','foreign/DR/investment/ship category requires explicit scope'
        else:
            kind,group,name,decision,reason='','','','REVIEW_IDENTITY','missing or ambiguous delisting master'
        rows.append({'ticker':t,'name':name,'security_group':group,'kind':kind,
                     'classification':decision,'reason':reason,'current_rows':len(cur),'delisting_rows':len(old)})
    return pd.DataFrame(rows)


def audit(data_dir, tree_path, output, events_path=None):
    # Validate every immutable input before reading or producing reports.
    tree=json.loads(tree_path.read_text())
    manifest=verify_sources(data_dir,tree)
    if events_path:
        h=hashes(events_path)
        expected=tree.get('event_input')
        if expected is None or h['bytes']!=expected['size'] or h['git_blob_sha1']!=expected['sha']:
            raise ValueError('event input mismatch')
        manifest.append({'file':events_path.name,**h})
    if output.exists():
        raise FileExistsError('choose a fresh output directory; existing audit is immutable')
    frames=[pd.read_parquet(data_dir/f'ohlcv_{y}.parquet') for y in range(2020,2027)]
    required={'Date','ticker','market',*PRICE,'Volume'}
    for y,f in zip(range(2020,2027),frames):
        if not required.issubset(f):raise ValueError('missing columns in '+str(y))
        if not pd.to_datetime(f.Date).dt.year.eq(y).all():raise ValueError('year partition mismatch')
    px=pd.concat(frames,ignore_index=True).sort_values(['ticker','Date'],kind='stable').reset_index(drop=True)
    if px[['ticker','market','Date']].isna().any(axis=None):raise ValueError('null identity/date')
    status=pd.read_parquet(data_dir/'fetch_status.parquet')
    listing=pd.read_parquet(data_dir/'listing.parquet')
    dl=pd.read_parquet(data_dir/'delisting.parquet')
    ix=pd.read_parquet(data_dir/'index.parquet')
    for frame,col in [(px,'ticker'),(status,'ticker'),(listing,'Code'),(dl,'Symbol')]:
        frame[col]=frame[col].astype(str)
    flag,ret=row_flags(px)
    calendars={name:pd.DatetimeIndex(g.Date.unique()).sort_values() for name,g in ix.groupby('index')}
    cd=calendar_differences(px,calendars)
    dup=px[px.duplicated(['ticker','Date'],keep=False)]
    counts=px.groupby('ticker').agg(actual_rows=('Date','size'),actual_first=('Date','min'),actual_last=('Date','max'))
    reconciliation=status.merge(counts,left_on='ticker',right_index=True,how='outer',indicator=True)
    reconciliation['actual_rows']=reconciliation.actual_rows.fillna(0).astype(int)
    reconciliation['row_count_match']=reconciliation.rows.eq(reconciliation.actual_rows)
    reconciliation['first_match']=pd.to_datetime(reconciliation['first']).eq(reconciliation.actual_first) | (reconciliation.rows.eq(0)&reconciliation.actual_rows.eq(0))
    reconciliation['last_match']=pd.to_datetime(reconciliation['last']).eq(reconciliation.actual_last) | (reconciliation.rows.eq(0)&reconciliation.actual_rows.eq(0))
    classes=classification(status,listing,dl)
    runs=zero_runs(px,flag.zero_volume)
    jumps=px.loc[flag.jump_over_30pct].copy()
    jumps['return_fraction']=ret[flag.jump_over_30pct]
    jumps['previous_date']=px.groupby('ticker').Date.shift()[flag.jump_over_30pct]
    jumps['previous_close']=px.groupby('ticker').Close.shift()[flag.jump_over_30pct]
    jumps['candidate_event_receipts']=''
    jumps['candidate_event_types']=''
    if events_path:
        ev=pd.read_csv(events_path,dtype=str)
        ev['rcept']=pd.to_datetime(ev.rcept)
        for i,r in jumps.iterrows():
            e=ev[ev.stock_code.eq(r.ticker)&ev.rcept.le(r.Date)&ev.rcept.ge(r.Date-pd.Timedelta(days=150))]
            jumps.at[i,'candidate_event_receipts']='|'.join(sorted(e.rcept_no.unique()))
            jumps.at[i,'candidate_event_types']='|'.join(sorted(e.type.unique()))
    jumps['resolution']='UNRESOLVED_NOT_A_CONFIRMED_ADJUSTMENT'
    jumps['audit_policy']='RETAIN_RAW_AND_REVIEW_FEATURES_AND_OPEN_POSITIONS'
    # These are ex-post audit annotations, never a historical signal/universe filter.
    jumps['provenance_scope']='EX_POST_DATA_AUDIT_ONLY'
    tails=[]
    for r in dl[dl.Symbol.isin(status.ticker)].itertuples():
        t=str(r.Symbol)
        if t not in counts.index:continue
        end=pd.Timestamp(r.DelistingDate);last=counts.loc[t,'actual_last'];cal=calendars.get(str(r.Market),pd.DatetimeIndex([]))
        last_trade=px.loc[(px.ticker==t)&px.Volume.gt(0),'Date'].max()
        tails.append({'ticker':t,'name':str(r.Name),'security_group':str(r.SecuGroup),'delisting_date':end,
            'last_observation':last,'missing_sessions_before_delist':terminal_gap(cal,last,end),
            'observations_after_delist':int(((px.ticker==t)&(px.Date>end)).sum()),
            'last_positive_volume':last_trade,
            'index_sessions_after_last_positive_volume':int(((cal>last_trade)&(cal<end)).sum()) if pd.notna(last_trade) else None,
            'resolution':'UNVERIFIED_TERMINAL_EXECUTION'})
    tail=pd.DataFrame(tails)
    byyear=[]
    for year,ind in px.groupby(px.Date.dt.year).groups.items():
        x=px.loc[ind];ff=flag.loc[ind]
        byyear.append({'year':int(year),'rows':len(x),'symbols':int(x.ticker.nunique()),
                       **{c:int(ff[c].sum()) for c in ff if c!='mechanically_valid_bar'}})
    summary={'audit_version':VERSION,'source_commit':tree['sha'],
        'runtime':{'python':platform.python_version(),'pandas':pd.__version__,'numpy':np.__version__,'pyarrow':pyarrow.__version__},
        'rows':len(px),'symbols':int(px.ticker.nunique()),'first':str(px.Date.min().date()),'last':str(px.Date.max().date()),
        'duplicate_key_rows':len(dup),'calendar_issues':cd.issue.value_counts().to_dict(),
        'index_duplicate_rows':int(ix.duplicated(['index','Date'],keep=False).sum()),
        'status_mismatch_rows':int((~reconciliation[['row_count_match','first_match','last_match']].all(axis=1)).sum()),
        'flags':{c:int(flag[c].sum()) for c in flag},'classification':classes.classification.value_counts().to_dict(),
        'zero_volume_runs':len(runs),'zero_runs_at_least_5':int(runs.rows.ge(5).sum()),
        'symbols_zero_run_at_least_5':int(runs.loc[runs.rows.ge(5),'ticker'].nunique()),
        'max_zero_run':int(runs.rows.max()),'jump_symbols':int(jumps.ticker.nunique()),
        'jumps_with_candidate_event':int(jumps.candidate_event_receipts.ne('').sum()),
        'delisting_tail_rows':len(tail),'delisting_tail_gaps':int(tail.missing_sessions_before_delist.gt(0).sum()),
        'delisted_with_zero_volume_tail':int(tail.index_sessions_after_last_positive_volume.gt(0).sum()),
        'symbols_with_prices_after_delist':int(tail.observations_after_delist.gt(0).sum()),
        'markets_per_symbol_conflicts':int(px.groupby('ticker').market.nunique().gt(1).sum()),
        'years':byyear,'approval':'DATA_NOT_APPROVED_FOR_PERFORMANCE_BACKTEST',
        'performance_experiments':0,'source_files':manifest,
        'limitations':['Index dates are a provider-derived calendar, not an independently verified exchange calendar.',
            'Current master classifications and delisting records are ex-post audit evidence only.',
            'Corporate action matches use broad receipt windows; adjustment accuracy and event effectiveness are unverified.',
            'Mechanically valid bars are not verified tradable prices or approved historical adjustment factors.',
            'No historical management/halts, market-transfer, original listing-age or full point-in-time universe verification.']}
    output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    pd.DataFrame(manifest).to_csv(output/'source_manifest.csv',index=False)
    for name,df in [('calendar_issues',cd),('duplicate_keys',dup),('status_reconciliation',reconciliation),
        ('security_classification',classes),('zero_volume_runs',runs),('corporate_action_exceptions',jumps),('delisting_tails',tail)]:
        df.to_csv(output/(name+'.csv'),index=False)
    issues = flag.nonfinite | flag.negative_volume | flag.ohlc_inconsistent | (flag.nonpositive_price & ~flag.halt_pattern)
    pd.concat([px.loc[issues],flag.loc[issues]],axis=1).to_csv(output/'invalid_bar_details.csv',index=False)
    # Compact full-row audit flags. No price is modified and no bar is removed.
    pd.concat([px[['ticker','market','Date']],flag],axis=1).to_parquet(output/'row_flags.parquet',index=False)
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir',type=Path,required=True)
    p.add_argument('--source-tree',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--events',type=Path)
    a=p.parse_args()
    result=audit(a.data_dir,a.source_tree,a.output,a.events)
    print(json.dumps({k:v for k,v in result.items() if k not in ('source_files','years')},ensure_ascii=False,indent=2))
