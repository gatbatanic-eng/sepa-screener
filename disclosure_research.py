"""Small, price-independent OpenDART research pilot. No orders or buy signals."""
import argparse
import datetime as dt
import hashlib
import html
import io
import json
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import publish_fundamentals as pf

ROOT = Path(__file__).resolve().parent
PILOT = [('355150', '코스텍시스'), ('007810', '코리아써키트'),
         ('353200', '대덕전자'), ('005930', '삼성전자'), ('010950', 'S-Oil')]
EXTRA = {
    'assets': (['ifrs-full_Assets'], ['자산총계']),
    'inventory': (['ifrs-full_Inventories'], ['재고자산']),
    'receivables': (['ifrs-full_CurrentTradeReceivables', 'ifrs-full_TradeReceivables', 'dart_ShortTermTradeReceivable', 'ifrs-full_TradeAndOtherCurrentReceivables'], ['매출채권', '매출채권및기타채권']),
    'capexYtd': (['ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities', 'ifrs-full_PurchaseOfPropertyPlantAndEquipment'], ['유형자산의취득', '유형자산취득']),
}
PPE_NAMES = ('토지', '건물', '구축물', '기계장치', '차량운반구', '공구와기구', '공구와비품', '비품', '시설장치', '건설중인자산', '기타유형자산')
TERMS = {
    '사업·고객': ['주요 제품', '주요제품', '주요 고객', '주요고객', '매출실적'],
    '수주·증설': ['수주상황', '수주현황', '공급계약', '시설투자', '생산능력'],
    '성장 검증': ['가동률', '신규 고객', '신규고객', '양산', '연구개발'],
    '주주·재무 위험': ['전환사채', '교환사채', '유상증자', '계속기업', '소송', '계약해지'],
}


def valid(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]


class Client(pf.Dart):
    """Budgeted calls; return list for list.json as in the existing collector."""
    def __init__(self, key, budget=350):
        super().__init__(key)
        self.calls = 0
        self.budget = budget
        self.lock = threading.Lock()

    def request(self, endpoint, **params):
        with self.lock:
            self.calls += 1
            if self.calls > self.budget:
                raise RuntimeError('DART request budget reached')
        if endpoint == 'list.json':
            # The old client's list-only return discards total_page. DART may repeat
            # the final full page instead of returning an empty page out of range.
            url = 'https://opendart.fss.or.kr/api/list.json?' + urllib.parse.urlencode({'crtfc_key': self.key, **params})
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(url, timeout=30) as response:
                        obj = json.loads(response.read())
                    if obj.get('status') == '013':
                        return {'list': [], 'total_page': 0}
                    if obj.get('status') != '000':
                        raise RuntimeError('DART status ' + str(obj.get('status', 'unknown')))
                    return obj
                except RuntimeError:
                    raise
                except Exception:
                    if attempt == 2:
                        raise RuntimeError('DART disclosure list request failed') from None
                    time.sleep(2)
        return super().request(endpoint, **params)


def pick(rows, key):
    ids, names = (pf.ACCOUNTS | EXTRA)[key]
    groups = ['CF'] if key in ('operatingCashFlow', 'capexYtd') else ['BS'] if key in ('cash', 'liabilities', 'equity', 'assets', 'inventory', 'receivables') else ['IS', 'CIS']
    for group in groups:
        subset = [r for r in rows if r.get('sj_div') == group and r.get('currency', 'KRW') == 'KRW']
        if key == 'receivables':
            # Some issuers tag OTHER receivables with the broad trade-and-other ID.
            # Never use that account alone as customer trade receivables.
            subset = [r for r in subset if '매출' in r.get('account_nm', '') or r.get('account_id') in EXTRA['receivables'][0][:3]]
        for ident in ids:
            hits = [r for r in subset if r.get('account_id') == ident]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                return {}  # Ambiguous contexts must not silently receive a value.
        for name in names:
            hits = [r for r in subset if re.sub(r'\s+', '', r.get('account_nm', '')) == name]
            if len(hits) == 1:
                return hits[0]
    if key == 'capexYtd':
        components = [r for r in rows if r.get('sj_div') == 'CF' and r.get('currency', 'KRW') == 'KRW'
                      and re.sub(r'\s+', '', r.get('account_nm', '')) in {n + suffix for n in PPE_NAMES for suffix in ('의취득', '취득')}]
        names = [re.sub(r'\s+', '', r.get('account_nm', '')) for r in components]
        values = [pf.number(r.get('thstrm_amount')) for r in components]
        if components and len(names) == len(set(names)) and all(valid(v) for v in values):
            return {'account_id': 'derived:PPE-components', 'account_nm': '유형자산 취득 세부항목 합계',
                    'thstrm_amount': str(sum(abs(v) for v in values)),
                    'components': [{'accountId': r.get('account_id'), 'accountName': r.get('account_nm'), 'reportedAmount': v} for r, v in zip(components, values)]}
    return {}


def normalize(reports):
    records = []
    for (year, quarter), report in sorted(reports.items()):
        rows, basis = report['rows'], report['basis']
        receipt = rows[0].get('rcept_no', '')
        rec = {'year': year, 'quarter': quarter, 'period': f'{year} Q{quarter}',
               'basis': basis, 'receipt': receipt, 'sourceUrl': 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=' + receipt,
               'cashFlowBasis': 'standalone-quarter', 'provenance': {}}
        previous = reports.get((year, quarter - 1), {})
        for key in pf.ACCOUNTS | EXTRA:
            row = pick(rows, key)
            value = pf.number(row.get('thstrm_amount'))
            rec['provenance'][key] = {'accountId': row.get('account_id'), 'accountName': row.get('account_nm'), 'receipt': receipt}
            if row.get('components'):
                rec['provenance'][key]['components'] = row['components']
            if key == 'capexYtd':
                rec['provenance'][key]['reportedSignedAmount'] = value
                value = abs(value) if valid(value) else None  # Known purchase outflow, both DART sign conventions.
            if quarter == 4 and key in ('revenue', 'operatingProfit', 'netIncome'):
                ytd = pf.number(pick(previous.get('rows', []), key).get('thstrm_add_amount'))
                value = value - ytd if valid(value) and valid(ytd) and previous.get('basis') == basis else None
                rec['provenance'][key]['subtractReceipt'] = previous.get('rows', [{}])[0].get('rcept_no') if previous.get('rows') else None
            if key in ('operatingCashFlow', 'capexYtd'):
                rec[key + 'Reported'] = value
                if quarter > 1:
                    prior = pf.number(pick(previous.get('rows', []), key).get('thstrm_amount'))
                    if key == 'capexYtd' and valid(prior):
                        prior = abs(prior)
                    value = value - prior if valid(value) and valid(prior) and previous.get('basis') == basis else None
                    rec['provenance'][key]['subtractReceipt'] = previous.get('rows', [{}])[0].get('rcept_no') if previous.get('rows') else None
            rec['capex' if key == 'capexYtd' else key] = value
        sales, profit = rec['revenue'], rec['operatingProfit']
        rec['operatingMargin'] = profit / sales * 100 if valid(sales) and sales > 0 and valid(profit) else None
        rec['fcf'] = rec['operatingCashFlow'] - rec['capex'] if valid(rec['operatingCashFlow']) and valid(rec['capex']) and rec['capex'] >= 0 else None
        records.append(rec)
    for rec in records:
        old = next((q for q in records if q['year'] == rec['year'] - 1 and q['quarter'] == rec['quarter'] and q['basis'] == rec['basis']), {})
        for key in ('revenue', 'operatingProfit', 'inventory', 'receivables'):
            rec[key + 'YoY'] = pf.growth(rec.get(key), old.get(key))
            if key == 'receivables':
                current_name = rec['provenance'][key].get('accountName') or ''
                prior_name = old.get('provenance', {}).get(key, {}).get('accountName') or ''
                if ('기타' in current_name) != ('기타' in prior_name):
                    rec[key + 'YoY'] = {'pct': None, 'label': '채권 범위 불일치'}
    return records


def filings(api, corp, today):
    """Relevant filing families; insider ownership notices are outside this pilot."""
    result = []
    for family in ('A', 'B', 'I', 'F'):
        for page in range(1, 31):
            response = api.request('list.json', corp_code=corp, pblntf_ty=family,
                               bgn_de=(today - dt.timedelta(days=800)).strftime('%Y%m%d'),
                               end_de=today.strftime('%Y%m%d'), last_reprt_at='N',
                               page_count=100, page_no=page, sort='date', sort_mth='desc')
            rows = response.get('list', []) if isinstance(response, dict) else response
            result.extend(rows)
            total_pages = int(response['total_page']) if isinstance(response, dict) and response.get('total_page') is not None else None
            if len(rows) < 100 or (total_pages is not None and page >= total_pages):
                break
        else:
            raise RuntimeError('Disclosure pagination limit reached; coverage incomplete')
    return sorted({r['rcept_no']: r for r in result}.values(), key=lambda r: r['rcept_no'], reverse=True)


def report_period(title):
    if not re.search(r'(사업|반기|분기)보고서', title):
        return None
    match = re.search(r'\((\d{4})\.(03|06|09|12)\)', title)
    return (int(match[1]), int(match[2]) // 3) if match else None


def text_from_zip(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(('.xml', '.html', '.htm'))]
        if sum(archive.getinfo(n).file_size for n in names) > 35_000_000:
            raise RuntimeError('Disclosure document exceeds extraction limit')
        texts = []
        for name in names:
            data = archive.read(name)
            try:
                content = data.decode('utf-8-sig')
            except UnicodeDecodeError:
                content = data.decode('cp949', errors='replace')
            content = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', '', content, flags=re.I | re.S)
            content = re.sub(r'</(?:p|tr|table|title|section|div)>|<br\s*/?>', '\n', content, flags=re.I)
            content = html.unescape(re.sub(r'<[^>]+>', ' ', content))
            texts.append('\n'.join(re.sub(r'\s+', ' ', line).strip() for line in content.splitlines() if line.strip()))
        if not texts:
            raise RuntimeError('No text document in DART archive')
        return '\n'.join(texts)


def evidence(text):
    out = []
    for group, terms in TERMS.items():
        matches = []
        for term in terms:
            for hit in list(re.finditer(re.escape(term), text))[:5]:
                # Keep surrounding context. A keyword hit is not a factual conclusion.
                start, stop = max(0, hit.start() - 180), min(len(text), hit.end() + 420)
                snippet = text[start:stop].strip()
                if snippet not in [m['excerpt'] for m in matches]:
                    matches.append({'term': term, 'offset': hit.start(), 'excerpt': snippet})
        out.append({'topic': group, 'matches': matches[:5], 'status': 'requires_review'})
    return out


def select_documents(rows):
    selected = []
    for kind in ('사업보고서', '반기보고서', '분기보고서'):
        # Compare same fiscal quarter; do not compare Q1 with Q3 or amended with original.
        candidates = [r for r in rows if kind in r.get('report_nm', '') and report_period(r['report_nm'])]
        if not candidates:
            continue
        newest = candidates[0]
        q = report_period(newest['report_nm'])[1]
        same_quarter = [r for r in candidates if report_period(r['report_nm'])[1] == q]
        seen = set()
        for row in same_quarter:
            p = report_period(row['report_nm'])
            if p not in seen:
                selected.append(row)
                seen.add(p)
            if len(seen) >= 2:
                break
    material = [r for r in rows if re.search(r'공급계약|시설투자|전환사채|교환사채|유상증자|계약해지', r.get('report_nm', ''))]
    selected.extend(material[:2])
    return list({r['rcept_no']: r for r in selected}.values())[:8]


def assess(quarters, today):
    out = {'status': 'hold', 'checks': [], 'warnings': [], 'candidateType': '미확인'}
    if not quarters:
        out['warnings'].append('재무제표 없음')
        return out
    latest = quarters[-1]
    out['latestPeriod'] = latest['period']
    last = quarters[-4:]
    periods = [q['year'] * 4 + q['quarter'] for q in last]
    consecutive = len(last) == 4 and all(b - a == 1 for a, b in zip(periods, periods[1:]))
    same_basis = len({q['basis'] for q in last}) == 1
    if not consecutive or not same_basis:
        out['warnings'].append('최근 4분기 연속성 또는 연결·별도 기준 불일치')
    if (today - pf.period_end(latest['year'], latest['quarter'])).days > 150:
        out['warnings'].append('최근 실적 종료일 150일 초과')
    def check(key, label, value, passed):
        out['checks'].append({'key': key, 'label': label, 'value': value, 'passed': passed})
    growth = [q['revenueYoY']['pct'] for q in quarters[-2:]]
    check('sales', '최근 2분기 매출 전년 대비 10% 이상 증가', growth,
          all(x >= 10 for x in growth) if len(growth) == 2 and all(valid(x) for x in growth) else None)
    profits = [q['operatingProfit'] for q in quarters[-2:]]
    check('profits', '최근 2분기 영업흑자', profits, all(v > 0 for v in profits) if len(profits) == 2 and all(valid(v) for v in profits) else None)
    for key, label in [('operatingCashFlow', '최근 4분기 영업현금흐름 양수'), ('fcf', '최근 4분기 설비취득 차감 현금흐름 양수')]:
        values = [q.get(key) for q in last]
        value = sum(values) if consecutive and same_basis and all(valid(v) for v in values) else None
        check(key, label, value, value > 0 if valid(value) else None)
    eq = latest['equity']
    check('equity', '자본총계 양수', eq, eq > 0 if valid(eq) else None)
    for key in ('inventory', 'receivables'):
        g, sales = latest[key + 'YoY']['pct'], latest['revenueYoY']['pct']
        if valid(g) and valid(sales) and g > sales + 20:
            out['warnings'].append(('재고' if key == 'inventory' else '채권') + ' 증가율이 매출 증가율을 20%p 초과: 원문 확인')
    if any(c['passed'] is None for c in out['checks']):
        out['warnings'].append('필수 비교·현금흐름 계정 미확인')
    old = next((q for q in quarters if q['year'] == latest['year'] - 1 and q['quarter'] == latest['quarter'] and q['basis'] == latest['basis']), {})
    a, b = latest['operatingProfit'], old.get('operatingProfit')
    out['candidateType'] = '흑자전환 관찰' if valid(a) and valid(b) and a > 0 > b else '성장 지속 검증'
    out['status'] = 'hold' if out['warnings'] else 'review_candidate' if all(c['passed'] for c in out['checks']) else 'conditions_not_met'
    return out


def collect(api, corp, code, name, now):
    today = now.date()
    issues = []
    try:
        notices = filings(api, corp, today)
    except Exception as exc:
        notices = []
        reason = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        issues.append({'area': '공시 목록', 'reason': reason})
        print(code, 'filing list unavailable:', reason, flush=True)
    reports = {}
    # Fresh requests also revisit old periods: do not freeze corrections after 150 days.
    for year in range(today.year - 2, today.year + 1):
        for quarter, report_code in pf.REPORTS.items():
            if pf.period_end(year, quarter) >= today:
                continue
            try:
                rows = api.request('fnlttSinglAcntAll.json', corp_code=corp, bsns_year=year, reprt_code=report_code, fs_div='CFS')
                basis = 'CFS'
                if not rows:
                    rows = api.request('fnlttSinglAcntAll.json', corp_code=corp, bsns_year=year, reprt_code=report_code, fs_div='OFS')
                    basis = 'OFS'
            except Exception as exc:
                reason = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
                issues.append({'area': f'{year} Q{quarter} 재무', 'reason': reason})
                print(code, year, quarter, 'financial request unavailable:', reason, flush=True)
                continue
            if rows:
                reports[(year, quarter)] = {'rows': rows, 'basis': basis}
    quarters = normalize(reports)
    review = assess(quarters, today)
    print(code, 'financial periods:', len(quarters), 'filings:', len(notices), flush=True)
    if issues:
        review['status'] = 'hold'
        review['warnings'].append('수집 일부 미확인: 누락 자료를 확인하기 전 후보 판단 보류')
    periods = [report_period(r.get('report_nm', '')) for r in notices]
    periods = [p for p in periods if p]
    if periods and (not quarters or max(periods) > (quarters[-1]['year'], quarters[-1]['quarter'])):
        review['status'] = 'hold'
        review['warnings'].append('최신 정기공시가 재무제표에 미반영')
    documents = []
    for row in select_documents(notices):
        receipt = row['rcept_no']
        doc = {'receipt': receipt, 'title': row['report_nm'], 'filedAt': row['rcept_dt'],
               'sourceUrl': 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=' + receipt}
        try:
            text = text_from_zip(api.request('document.xml', rcept_no=receipt))
            doc.update(status='extracted_requires_review', textDigest=digest(text), textLength=len(text), evidence=evidence(text))
        except Exception:
            doc.update(status='unavailable', reason='DART 원문 추출 실패; 제목만으로 내용 판단 금지')
        documents.append(doc)
    by_kind = {}
    for doc in documents:
        p = report_period(doc['title'])
        if p:
            kind = ('annual' if p[1] == 4 else 'interim') + str(p[1])
            by_kind.setdefault(kind, []).append(doc)
    comparisons = []
    for kind, docs in by_kind.items():
        if len(docs) >= 2 and all(d.get('textDigest') for d in docs[:2]):
            comparisons.append({'kind': kind, 'currentReceipt': docs[0]['receipt'], 'previousReceipt': docs[1]['receipt'],
                                'textChanged': docs[0]['textDigest'] != docs[1]['textDigest'],
                                'status': 'requires_review', 'note': '전년 동분기 문서 비교. 수치·서식 변경도 포함하므로 사업 변화로 자동 판정하지 않음'})
    return {'schemaVersion': 1, 'code': code, 'name': name, 'source': 'OpenDART', 'checkedAt': now.isoformat(),
            'status': 'ok' if quarters and all(d['status'] != 'unavailable' for d in documents) and documents and not issues else 'partial',
            'collectionIssues': issues,
            'filingScope': '정기·주요사항·거래소·외부감사 공시(A/B/I/F). 임원 지분변동 등은 제외',
            'rawReports': [{'year': year, 'quarter': quarter, 'basis': report['basis'],
                            'rows': [{k: r[k] for k in ('rcept_no', 'sj_div', 'currency', 'account_id', 'account_nm', 'thstrm_amount', 'thstrm_add_amount') if k in r}
                                     for r in report['rows'] if r.get('sj_div') == 'CF' or any(r == pick(report['rows'], key) for key in pf.ACCOUNTS | EXTRA)]}
                           for (year, quarter), report in sorted(reports.items())],
            'quarters': quarters[-8:], 'assessment': review, 'documents': documents, 'comparisons': comparisons,
            'filings': [{'receipt': r['rcept_no'], 'title': r['report_nm'], 'filedAt': r['rcept_dt'],
                         'sourceUrl': 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=' + r['rcept_no']} for r in notices],
            'limits': ['연구 후보이며 매수 신호가 아님', '원문 발췌는 키워드 기반이며 기술해자·계약 확정성을 검증한 결론이 아님',
                       '최신 조회 기준 재무자료: 정정 전 시점의 백테스트에 사용 불가', '동일 기준 재작성 전년 비교표와의 최종 대조는 수동 검토 필요',
                       '금융·보험 및 비12월 결산 기업으로 확장하기 전 별도 처리 필요']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--offline', action='store_true', help='Rebuild assessment from existing pilot files without API calls')
    parser.add_argument('--retry-incomplete', action='store_true', help='Reuse complete observations less than one day old and retry missing companies')
    args = parser.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    output = ROOT / 'docs/data/disclosures'
    output.mkdir(parents=True, exist_ok=True)
    results, errors = [], []
    if args.offline:
        for code, name in PILOT:
            path = output / (code + '.json')
            if path.exists():
                payload = json.loads(path.read_text(encoding='utf-8'))
                if payload.get('rawReports'):
                    payload['quarters'] = normalize({(r['year'], r['quarter']): {'basis': r['basis'], 'rows': r['rows']} for r in payload['rawReports']})[-8:]
                payload['assessment'] = assess(payload['quarters'], now.date())
                if payload.get('collectionIssues'):
                    payload['assessment']['status'] = 'hold'
                    payload['assessment']['warnings'].append('기존 수집 누락 미해결')
                results.append(payload)
            else:
                errors.append({'code': code, 'name': name, 'reason': '기존 공시 검증 자료 없음'})
    else:
        selected = []
        for code, name in PILOT:
            path = output / (code + '.json')
            previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            try:
                fresh = (now - dt.datetime.fromisoformat(previous['checkedAt'])).total_seconds() < 86400
            except (KeyError, ValueError):
                fresh = False
            if args.retry_incomplete and previous.get('status') == 'ok' and fresh:
                results.append(previous)
                print(code, 'reuse complete observation:', previous['checkedAt'], flush=True)
            else:
                selected.append((code, name))
        key = os.environ.get('DART_API_KEY', '').strip()
        if not key:
            raise RuntimeError('DART_API_KEY is not configured; no fabricated fallback')
        api = Client(key)
        corps = api.corporations()
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = {pool.submit(collect, api, corps[code], code, name, now): (code, name) for code, name in selected if code in corps}
            errors.extend({'code': code, 'name': name, 'reason': 'DART 기업코드 미확인'} for code, name in selected if code not in corps)
            for job in as_completed(jobs):
                code, name = jobs[job]
                try:
                    payload = job.result()
                    previous_path = output / (code + '.json')
                    if previous_path.exists():
                        previous = json.loads(previous_path.read_text(encoding='utf-8'))
                        payload['previousObservationAt'] = previous.get('checkedAt')
                        payload['financialDataChanged'] = digest(previous.get('quarters')) != digest(payload['quarters'])
                    snap = ROOT / f'research/disclosures/{code}/{digest(payload)}.json'
                    if not snap.exists():
                        pf.save(snap, payload)
                    payload['snapshot'] = snap.relative_to(ROOT).as_posix()
                    pf.save(previous_path, payload)
                    results.append(payload)
                    print(code, payload['status'], payload['assessment']['status'], flush=True)
                except Exception as exc:
                    reason = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
                    errors.append({'code': code, 'name': name, 'reason': 'DART 수집 실패: ' + reason})
                    print(code, 'collection failed:', reason, flush=True)
        print('OpenDART calls:', api.calls, flush=True)
    index = {'schemaVersion': 1, 'checkedAt': now.isoformat(), 'mode': 'offline' if args.offline else 'live',
             'status': 'partial' if errors or any(r['status'] != 'ok' for r in results) else 'ok',
             'requested': len(PILOT), 'received': len(results), 'errors': errors,
             'rows': [{k: r[k] for k in ('code', 'name', 'checkedAt', 'status', 'assessment')} | {'documentCount': len(r['documents'])} for r in sorted(results, key=lambda r: r['code'])]}
    pf.save(output / 'index.json', index)
    if errors:
        raise RuntimeError('Some disclosure pilot companies failed; inspect public index')


if __name__ == '__main__':
    main()
