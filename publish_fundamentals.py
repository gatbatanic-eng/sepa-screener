"""Public DART fundamentals for KR legacy 8/8 pass observations; never exports credentials.

Snapshots are first-observed payloads, not historical point-in-time backtests.
"""
import datetime as dt
import gzip
import hashlib
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parent
DART_WORKERS = 4        # DART 응답이 느려(종목당 1분 이상) 종목별로 동시에 조회한다
SETTLE_DAYS = 150       # 분기 말 후 이 기간이 지나면 확정 보고서로 보고 캐시한다(정정 공시는 반영하지 않음)
CACHE_DIR = ROOT / 'research/fundamentals/_cache/kr'
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

def _needed_row(r):
    """account()가 고르는 행만 남겨도 같은 결과가 나온다(아이디 또는 계정명이 ACCOUNTS에 있는 행)."""
    name = re.sub(r'\s+', '', r.get('account_nm', ''))
    return any(r.get('account_id') in ids or name in names for ids, names in ACCOUNTS.values())

def slim(rows):
    keep = ('rcept_no', 'sj_div', 'currency', 'account_id', 'account_nm', 'thstrm_amount', 'thstrm_add_amount')
    return [{k: r[k] for k in keep if k in r} for r in rows if _needed_row(r)]

def period_end(year, quarter):
    return dt.date(year + quarter // 4, quarter % 4 * 3 + 1, 1) - dt.timedelta(days=1)

def is_settled(year, quarter, today):
    return (today - period_end(year, quarter)).days > SETTLE_DAYS

def load_cache(code, cache_dir=None):
    path = (cache_dir or CACHE_DIR) / (code + '.json.gz')
    if not path.exists():
        return {}
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def save_cache(code, cache, cache_dir=None):
    path = (cache_dir or CACHE_DIR) / (code + '.json.gz')
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, 'wt', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, separators=(',', ':'))

def fetch_reports(api, corp_code, code, now, cache_dir=None):
    """종목 하나의 분기 보고서. 확정된 과거 분기는 캐시(보고서가 없던 분기 포함)를 쓰고 나머지만 조회한다."""
    cache = load_cache(code, cache_dir)
    today = now.date()
    reports, changed = {}, False
    for year in range(now.year - 3, now.year + 1):
        for quarter, report_code in REPORTS.items():
            # No unfinished current-year period is requested.
            if year == now.year and quarter * 3 >= now.month:
                continue
            key = f'{year}Q{quarter}'
            if key in cache and is_settled(year, quarter, today):
                hit = cache[key]
            else:
                rows = api.request('fnlttSinglAcntAll.json', corp_code=corp_code, bsns_year=year, reprt_code=report_code, fs_div='CFS')
                basis = 'CFS'
                if not rows:
                    rows = api.request('fnlttSinglAcntAll.json', corp_code=corp_code, bsns_year=year, reprt_code=report_code, fs_div='OFS')
                    basis = 'OFS'
                hit = {'rows': slim(rows), 'basis': basis if rows else None}
                if is_settled(year, quarter, today) and cache.get(key) != hit:
                    cache[key], changed = hit, True
            if hit['rows']:
                reports[(year, quarter)] = {'rows': hit['rows'], 'basis': hit['basis']}
    if changed:
        save_cache(code, cache, cache_dir)
    return reports

def collect_stock(api, corps, stock, now, cache_dir=None):
    code = str(stock['code']).zfill(6)
    if code not in corps:
        raise RuntimeError('DART corporation mapping unavailable')
    company = api.request('company.json', corp_code=corps[code])
    quarters = normalize(fetch_reports(api, corps[code], code, now, cache_dir))
    return code, company, quarters

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
        return obj if endpoint == 'company.json' else obj.get('list', [])

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
    # 8개 조건 전부 통과 종목 전체(passAll). passAll의 조건8은 v2 RS_Score>=80으로 통일돼
    # 있어(screening.apply_v2_trend_as_pass_all) passAll == v2 TREND_OK 다.
    selected = [r for r in stocks if r.get('status') == 'OK' and r.get('passAll') is True]
    api = Dart(key)
    corps = api.corporations()
    output = ROOT / 'docs/data/fundamentals/kr'
    index = {'schemaVersion': 1, 'market': 'kr', 'checkedAt': now.isoformat(), 'symbols': {}}
    failed = 0
    def finish(stock, company, quarters):
        code = str(stock['code']).zfill(6)
        payload = {'schemaVersion': 1, 'market': 'kr', 'code': code, 'name': stock['name'], 'source': 'OpenDART', 'industryCode': company.get('induty_code'), 'industrySystem': 'KSIC',
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

    # 조회는 동시에, 파일 저장과 색인 갱신은 이 스레드에서 차례로 한다.
    with ThreadPoolExecutor(max_workers=DART_WORKERS) as pool:
        futures = {pool.submit(collect_stock, api, corps, stock, now): stock for stock in selected}
        for fut in as_completed(futures):
            stock = futures[fut]
            code = str(stock['code']).zfill(6)
            try:
                _, company, quarters = fut.result()
                finish(stock, company, quarters)
            except Exception as exc:
                failed += 1
                # Errors contain no request URLs, response bodies, or credentials.
                index['symbols'][code] = {'status': 'error', 'reason': str(exc) if isinstance(exc, RuntimeError) else 'Data processing failed'}
                print(code, 'collection failed', flush=True)
    index['symbols'] = dict(sorted(index['symbols'].items()))
    save(output / 'index.json', index)
    print(f'Fundamentals: {len(selected)} selected, {failed} failed', flush=True)
    if failed:
        raise RuntimeError('Some fundamentals could not be refreshed; prior files preserved')

if __name__ == '__main__':
    main()
