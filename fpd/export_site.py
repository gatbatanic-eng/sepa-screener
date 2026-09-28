"""Publish one atomic, schema-versioned Site view from verified PIT artifacts.
No network calls, credentials, forward filling, or changes to frozen research rules.
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from . import DATASET_ID, RESEARCH_ID
from .storage import read_gzip_json, write_replaceable_json

ROOT = Path(__file__).resolve().parents[1]


def build_view(latest, derived, signal, raw, history, monthly, pipeline=None, dataset_id=DATASET_ID, research_id=RESEARCH_ID):
    for doc in (latest, derived, signal, raw, monthly):
        if doc.get('datasetId') != dataset_id:
            raise ValueError('Dataset mismatch: refusing mixed research data')
    for doc in (derived, signal, raw):
        if doc.get('snapshotDate') != latest['snapshotDate']:
            raise ValueError('Snapshot date mismatch: refusing mixed publication')
        if doc.get('researchDefinitionHash') != latest['researchDefinitionHash']:
            raise ValueError('Research definition mismatch')
    if latest.get('researchId') != research_id:
        raise ValueError('Unexpected research model')
    history = sorted((s for s in history if s.get('datasetId') == dataset_id), key=lambda s:s['snapshotDate'])
    scored = {r['ticker']: r for r in signal.get('rows', [])}
    rows = []
    for ticker, meta in sorted(raw.get('universe', {}).items()):
        score = scored.get(ticker, {})
        period = score.get('periodEnd')
        observation = next((r for r in raw.get('observations', {}).get(ticker, []) if r.get('periodEnd') == period), None)
        item = next((r for r in derived.get('symbols', {}).get(ticker, []) if r.get('periodEnd') == period), {})
        windows = item.get('lookbacks', {})
        rows.append({
            'ticker': ticker, 'name': meta.get('name') or ticker, 'market': 'US',
            'periodEnd': period, 'forwardOrdinal': 'F1' if observation else None,
            'consensusStatus': 'collected' if observation else 'provider_unavailable',
            'failure': {'status':raw['failures'][ticker].get('status')} if ticker in raw.get('failures', {}) else None,
            'epsCurrent': observation['eps'].get('avg') if observation else None,
            'revenueCurrent': observation['revenue'].get('avg') if observation else None,
            'epsAnalysts': item.get('epsAnalysts'), 'revenueAnalysts': item.get('revenueAnalysts'),
            'consensusRevisions': {f'{metric}_R{days}': windows.get(f'{days}D', {}).get(field)
                for metric, field in [('EPS','epsRevisionRaw'),('Revenue','revenueRevisionRaw')]
                for days in (7,30,60,90)},
            'lookbacks': windows, 'acceleration': item.get('acceleration', {}),
            'fpdCRZ': score.get('fpdCRZ'), 'rvCompositeRZ': score.get('rvCompositeRZ'),
            'pv30': score.get('pv30'), 'rs30': score.get('rs30'),
            'snapshotDate': raw['snapshotDate'], 'recordedAt': raw['recordedAt'],
        })
    return {
        'schemaVersion': 'fpd-site-v1', 'revisionUnit': 'ratio',
        'researchId': research_id, 'datasetId': dataset_id,
        'researchDefinitionHash': latest['researchDefinitionHash'],
        'snapshotDate': latest['snapshotDate'], 'recordedAt': raw['recordedAt'],
        'status': signal.get('status'), 'collectionStatus': latest.get('status'),
        'source': {'provider':raw.get('provider'), 'githubRunId':raw.get('githubRunId'),
                   'sourceCommit':raw.get('sourceCommit')},
        'pipeline': pipeline or {'status':'existing_verified_snapshot'},
        'manifest': latest.get('manifest', {}), 'signalManifest':signal.get('manifest', {}),
        'history': [{'snapshotDate':s['snapshotDate'], 'recordedAt':s.get('recordedAt'),
                     'observed':s.get('manifest',{}).get('symbolsObserved'),
                     'failed':s.get('manifest',{}).get('symbolsFailed')} for s in history],
        'rawSnapshots':len(history),
        'firstSnapshotDate':history[0]['snapshotDate'] if history else None,
        'confirmedMonthlyCohorts':monthly.get('confirmedMonthlyCohorts', 0),
        'outcomeHorizonsSessions':[65,130,252,504], 'rows':rows,
    }


def publish(root=ROOT):
    public = root / 'docs/data/fpd'
    read = lambda name: json.loads((public / name).read_text())
    latest = read('latest_us.json')
    history = [read_gzip_json(p) for p in sorted((root/'research/fpd/raw/us').rglob('*.json.gz'))]
    raw = next(s for s in history if s.get('datasetId')==DATASET_ID and s['snapshotDate']==latest['snapshotDate'])
    pipeline = {'status':os.environ.get('FPD_COLLECTION_RESULT','existing_verified_snapshot'),
                'publicationRunId':os.environ.get('GITHUB_RUN_ID'),
                'publishedAt':datetime.now(timezone.utc).isoformat()}
    view = build_view(latest,read('derived_latest_us.json'),read('signal_latest_us.json'),raw,history,read('monthly_research_us.json'),pipeline)
    write_replaceable_json(public / 'site_latest_us.json',view)
    print(f"Site view: {len(view['rows'])} symbols, {view['rawSnapshots']} snapshots, as of {view['snapshotDate']}")
    return view


if __name__ == '__main__':
    publish()
