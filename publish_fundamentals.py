"""Public DART fundamentals for KR trend observations; never exports credentials.

Snapshots are first-observed payloads, not historical point-in-time backtests.
"""
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parent
REPORTS = {1: '11013', 2: '11012', 3: '11014', 4: '11011'}
ACCOUNTS = {
    'revenue': (['ifrs-full_Revenue', 'ifrs_Revenue'], ['매출액', '수익(매출액)', '영업수익']),
    'operatingProfit': (['dart_OperatingIncomeLoss'], ['영업이익', '영업이익(손실)', '영업손익']),
    'netIncome': (['ifrs-full_ProfitLoss', 'ifrs_ProfitLoss'], ['당기순이익', '당기순이익(손실)', '분기순이익', '반기순이익']),
    'operatingCashFlow': (['ifrs-full_CashFlowsFromUsedInOperatingActivities'], ['영업활동현금흐름', '영업활동으로인한현금흐름']),
    'cash': (['ifrs-full_CashAndCashEquivalents'], ['현금및현금성자산']),
    'liabilities': (['ifrs-full_Liabilities'], ['부채총계']),
    'equity': (['ifrs-full_Equity'], ['자본총계']),
}

def number(value):
    s = str(value or '').replace(',', '').strip()
    if re.fullmatch(r'\(\d+(?:\.\d+)?\)', s):
        s = '-' + s[1:-1]
    return float(s) if re.fullmatch(r'-?\d+(?:\.\d+)?', s) else None

def account(rows, key):
    ids, names = ACCOUNTS[key]
    sj = ['CF'] if key == 'operatingCashFlow' else ['BS'] if key in ['cash', 'liabilities', 'equity'] else ['IS', 'CIS']
    selected = [r for r in rows if r.get('sj_div') in sj and r.get('currency', 'KRW') == 'KRW']
    for ident in ids:
        found = [r for r in selected if r.get('account_id') == ident]
        if len(found) == 1:
            return found[0]
    found = [r for r in selected if re.sub(r'\s+', '', r.get('account_nm', '')) in names]
    return found[0] if len(found) == 1 else {}

def growth(current, previous):
    if current is None or previous is None:
        return {'pct': None, 'label': '비교자료 없음'}
    if previous <= 0:
        return {'pct': None, 'label': '흑자전환' if previous < 0 < current else '적자지속' if current < 0 and previous < 0 else '비교기준 0 이하'}
    return {'pct': round((current / previous - 1) * 100, 2), 'label': '적자전환' if current < 0 else ''}

def normalize(reports):
    result = []
    for (year, quarter), report in sorted(reports.items()):
        rows, basis = report['rows'], report['basis']
        rec = {'period': f'{year} Q{quarter}', 'year': year, 'quarter': quarter, 'basis': basis,
               'receipt': rows[0].get('rcept_no', ''), 'currency': 'KRW'}
        receipt = rec['receipt']
        rec['filedAt'] = f'{receipt[:4]}-{receipt[4:6]}-{receipt[6:8]}' if re.fullmatch(r'\d{14}', receipt) else None
        rec['sourceUrl'] = 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=' + receipt
        for key in ACCOUNTS:
            row = account(rows, key)
            rec[key] = number(row.get('thstrm_amount'))
            # Q4 income = annual minus nine-month cumulative, on the SAME basis.
            if quarter == 4 and key in ['revenue', 'operatingProfit', 'netIncome']:
                prior = reports.get((year, 3), {})
                ytd = number(account(prior.get('rows', []), key).get('thstrm_add_amount'))
                rec[key] = rec[key] - ytd if rec[key] is not None and ytd is not None and prior.get('basis') == basis else None
        rec['operatingMargin'] = rec['operatingProfit'] / rec['revenue'] * 100 if rec['revenue'] and rec['operatingProfit'] is not None and rec['revenue'] > 0 else None
        rec['debtToEquity'] = rec['liabilities'] / rec['equity'] * 100 if rec['equity'] and rec['liabilities'] is not None and rec['equity'] > 0 else None
        # Cash flow remains YTD, balance-sheet amounts remain period-end.
        for key in ['revenue', 'operatingProfit', 'netIncome']:
            prior = next((p for p in result if p['year'] == year - 1 and p['quarter'] == quarter and p['basis'] == basis), {})
            rec[key + 'YoY'] = growth(rec[key], prior.get(key))
        result.append(rec)
    return result[-8:]

class Dart:
    def __init__(self, key):
        self.key = key

    def request(self, endpoint, **params):
        url = 'https://opendart.fss.or.kr/api/' + endpoint + '?' + urllib.parse.urlencode({'crtfc_key': self.key, **params})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=40) as response:
                    data = response.read()
                break
            except Exception:
                if attempt == 2:
                    raise RuntimeError('DART network request failed') from None
                time.sleep(2)
        if endpoint.endswith('.xml'):
            return data
        obj = json.loads(data)
        if obj.get('status') == '013':
            return []
        if obj.get('status') != '000':
            raise RuntimeError('DART status ' + str(obj.get('status', 'unknown')))
        return obj.get('list', [])

    def corporations(self):
        raw = self.request('corpCode.xml')
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                root = ET.fromstring(z.read('CORPCODE.xml'))
            return {r.findtext('stock_code', '').strip(): r.findtext('corp_code') for r in root.findall('list') if r.findtext('stock_code', '').strip()}
        except Exception:
            raise RuntimeError('DART corporation list unavailable; check API key status') from None

def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(',', ':')), encoding='utf-8')

def main():
    key = os.environ.get('DART_API_KEY', '').strip()
    if not key:
        raise RuntimeError('DART_API_KEY is not configured')
    now = dt.datetime.now(dt.timezone.utc)
    stocks = json.loads((ROOT / 'docs/data/latest_kr.json').read_text())
    selected = [r for r in stocks if r.get('status') == 'OK' and r.get('inUniverse') is True and r.get('trendOk') is True]
    api = Dart(key)
    corps = api.corporations()
    output = ROOT / 'docs/data/fundamentals/kr'
    index = {'schemaVersion': 1, 'market': 'kr', 'checkedAt': now.isoformat(), 'symbols': {}}
    failed = 0
    for stock in selected:
        code = str(stock['code']).zfill(6)
        try:
            if code not in corps:
                raise RuntimeError('DART corporation mapping unavailable')
            reports = {}
            for year in range(now.year - 3, now.year + 1):
                for quarter, report_code in REPORTS.items():
                    # No unfinished current-year period is requested.
                    if year == now.year and quarter * 3 >= now.month:
                        continue
                    rows = api.request('fnlttSinglAcntAll.json', corp_code=corps[code], bsns_year=year, reprt_code=report_code, fs_div='CFS')
                    basis = 'CFS'
                    if not rows:
                        rows = api.request('fnlttSinglAcntAll.json', corp_code=corps[code], bsns_year=year, reprt_code=report_code, fs_div='OFS')
                        basis = 'OFS'
                    if rows:
                        reports[(year, quarter)] = {'rows': rows, 'basis': basis}
            quarters = normalize(reports)
            payload = {'schemaVersion': 1, 'market': 'kr', 'code': code, 'name': stock['name'], 'source': 'OpenDART',
                       'checkedAt': now.isoformat(), 'status': 'ok' if quarters else 'unavailable', 'quarters': quarters,
                       'historyNote': '과거 실적은 수집 시점의 공시 조회값입니다. 과거 매수 시점에 알려진 값으로 간주할 수 없습니다.'}
            digest = hashlib.sha256(json.dumps(quarters, sort_keys=True).encode()).hexdigest()[:20]
            snap = ROOT / 'research/fundamentals/kr' / code / (digest + '.json')
            if not snap.exists():
                save(snap, payload)
            payload['firstObservedAt'] = json.loads(snap.read_text())['checkedAt']
            save(output / (code + '.json'), payload)
            index['symbols'][code] = {'status': payload['status'], 'latestPeriod': quarters[-1]['period'] if quarters else None}
            print(code, payload['status'], len(quarters), 'quarters', flush=True)
        except Exception as exc:
            failed += 1
            # Errors contain no request URLs, response bodies, or credentials.
            index['symbols'][code] = {'status': 'error', 'reason': str(exc) if isinstance(exc, RuntimeError) else 'Data processing failed'}
            print(code, 'collection failed', flush=True)
    save(output / 'index.json', index)
    print(f'Fundamentals: {len(selected)} selected, {failed} failed', flush=True)
    if failed:
        raise RuntimeError('Some fundamentals could not be refreshed; prior files preserved')

if __name__ == '__main__':
    main()
