"""Daily KRX money/news concentration. Query-only, isolated from trading rules.
Run after 16:00 KST: python -m nhplug.money_flow
"""
from __future__ import annotations
import datetime as dt
import html
import json
import math
import re
from pathlib import Path
import requests
from .close_report import ROOT, sector_group, stock_day
from .kr_extra import universe
from .probe import token, rows, kr_period

KST = dt.timezone(dt.timedelta(hours=9))
OUT = ROOT / 'docs/market/money_flow'
CATALYSTS = {'실적·전망': ('실적', '영업익', '매출', '가이던스', '흑자', '적자'),
             '수주·계약': ('수주', '계약', '공급'), '정책·규제': ('정책', '정부', '규제', '관세'),
             '산업 수요·투자': ('투자', '수요', '증설', '데이터센터', 'AI', 'HBM'),
             '기업 이벤트': ('인수', '합병', '자사주', '배당', '임상', '승인')}


def clean(value):
    return html.unescape(re.sub(r'<[^>]+>', '', str(value or ''))).strip()


def normalize_news(payload, name, day):
    """Only dated, same-KST-day, named-stock articles with canonical links.
    Undated/ambiguous items cannot serve as evidence for today's catalyst.
    """
    found = {}
    key = re.sub(r'\s+', '', name)
    def walk(x):
        if isinstance(x, list):
            for v in x: walk(v)
        elif isinstance(x, dict):
            title = clean(x.get('titleFull') or x.get('title'))
            excerpt = clean(x.get('body'))[:200]
            stamp = str(x.get('datetime') or x.get('dateTime') or x.get('publishDate') or x.get('publishedAt') or '')
            digits = re.sub(r'\D', '', stamp)
            url = x.get('url') or x.get('link') or ''
            office, article = x.get('officeId'), x.get('articleId')
            if office and article:
                url = f'https://n.news.naver.com/mnews/article/{office}/{article}'
            if len(key) >= 2 and key in re.sub(r'\s+', '', title + excerpt) and digits[:8] == day and str(url).startswith('https://'):
                found[title] = {'title': title, 'publishedAt': stamp, 'url': url,
                                'publisher': clean(x.get('officeName') or x.get('publisher')), 'excerpt': excerpt}
            for v in x.values():
                if isinstance(v, (dict, list)): walk(v)
    walk(payload)
    return list(found.values())


def fetch_news(code, name, day):
    try:
        response = requests.get(f'https://m.stock.naver.com/api/news/stock/{code}?pageSize=30&page=1',
            headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://m.stock.naver.com/'}, timeout=12)
        response.raise_for_status()
        return {'status': 'ok', 'articles': normalize_news(response.json(), name, day)}
    except (requests.RequestException, ValueError):
        return {'status': 'failed', 'articles': []}


def explain(articles):
    groups = []
    for category, words in CATALYSTS.items():
        hits = [a for a in articles if any(w.lower() in (a['title'] + ' ' + a.get('excerpt', '')).lower() for w in words)]
        if hits:
            groups.append({'category': category, 'claim': '당일 기사 제목·요약에 보도된 관련 재료', 'evidence': hits[:3]})
    return {'status': 'reported_catalyst' if groups else 'unconfirmed', 'catalysts': groups,
            'interpretation': '거래 집중과 당일 보도가 동반됨. 보도 내용·공시 원문 및 선후관계 확인 필요.' if groups else '당일 기사에서 구체적인 재료를 확인하지 못함.',
            'causality': '뉴스 동반 관측이며 가격 움직임의 인과관계는 미확정'}


def build(day, data, expected, news=None, previous=None):
    news = news or {}
    # Caller supplies only day-validated bars. Invalid monetary fields are excluded.
    valid = {c: d for c, d in data.items() if isinstance(d.get('turn'), (int, float)) and math.isfinite(d['turn']) and d['turn'] > 0 and isinstance(d.get('chg'), (int, float)) and math.isfinite(d['chg'])}
    total = sum(d['turn'] for d in valid.values())
    sectors = {}
    for code, d in valid.items():
        name = sector_group(d.get('sector'))
        g = sectors.setdefault(name, {'sector': name, 'turnoverKRW': 0, 'stocks': [], 'newsCount': 0, 'newsCoveredStocks': 0})
        # news_cnt is a provider count, not readership/search popularity.
        count = d.get('news')
        stock = {'code': code, 'name': d['name'], 'changePct': d['chg'], 'turnoverKRW': d['turn'],
                 'turnoverVs20d': d.get('surge'), 'providerNewsCount': count,
                 'sourceUrl': f'https://finance.naver.com/item/main.naver?code={code}'}
        evidence = news.get(code)
        if evidence is not None:
            stock['newsFetchStatus'] = evidence['status']
            stock['research'] = explain(evidence['articles'])
        g['stocks'].append(stock)
        g['turnoverKRW'] += d['turn']
        if count is not None:
            g['newsCoveredStocks'] += 1
            g['newsCount'] += count
    prior = {x['sector']: x for x in (previous or {}).get('sectors', [])}
    for name, g in sectors.items():
        xs = g['stocks']
        g['stockCount'] = len(xs)
        g['turnoverSharePct'] = round(g['turnoverKRW'] / total * 100, 2) if total else None
        g['meanChangePct'] = round(sum(x['changePct'] for x in xs) / len(xs), 2)
        g['advancingPct'] = round(sum(x['changePct'] > 0 for x in xs) / len(xs) * 100, 1)
        g['moneyLeaders'] = sorted(xs, key=lambda x: (-x['turnoverKRW'], x['code']))[:3]
        g['newsLeaders'] = sorted((x for x in xs if x['providerNewsCount'] is not None), key=lambda x: (-x['providerNewsCount'], -x['turnoverKRW']))[:3]
        g['observations'] = [f"분석 대상 거래대금의 {g['turnoverSharePct']}%가 이 업종에 집중",
                             f"업종 {len(xs)}종목 중 상승 비율 {g['advancingPct']}%, 평균 등락 {g['meanChangePct']}%",
                             '상승 동반 거래 집중' if g['meanChangePct'] > 0 else '하락 또는 보합 중 거래 집중: 강세 섹터로 해석하지 않음']
        old = prior.get(name)
        g['shareChangePp'] = round(g['turnoverSharePct'] - old['turnoverSharePct'], 2) if old and old.get('turnoverSharePct') is not None else None
        g['research'] = explain([a for x in xs for a in news.get(x['code'], {}).get('articles', [])])
        del g['stocks']
    all_sectors = sorted(sectors.values(), key=lambda g: -g['turnoverKRW'])
    coverage = len(valid) / expected if expected else 0
    return {'schemaVersion': 1, 'date': day, 'generatedAt': dt.datetime.now(KST).isoformat(),
            'session': '장 마감 일봉·잠정', 'status': 'ok' if expected and coverage >= .8 and len(valid) >= 300 else 'degraded',
            'coverage': {'expected': expected, 'valid': len(valid), 'ratio': round(coverage, 4)},
            'totalTurnoverKRW': total, 'sectors': all_sectors,
            'moneyRanking': [g['sector'] for g in all_sectors],
            'attentionRanking': [g['sector'] for g in sorted(all_sectors, key=lambda g: (-g['newsCount'], -g['turnoverKRW'])) if g['newsCount'] > 0],
            'previousDate': (previous or {}).get('date'),
            'method': {'money': '분석 대상 종목 거래대금 합계·비중. 순유입액이 아님',
                       'attention': 'NHPLUG news_cnt 뉴스 건수 합계. 조회·검색 순위가 아닌 뉴스 노출 대용 지표',
                       'classification': 'NHPLUG 업종 분류. 투자 테마와 다를 수 있음',
                       'research': '거래대금·뉴스 상위 3개 업종의 대표 종목 최대 18개, 네이버 최신 30건 중 당일·종목명(제목·요약)·링크가 있는 기사',
                       'universe': '기존 한국 수집 대상 내 순위이며 전체 시장 전수 순위가 아님'}}


def save(report, folder=OUT):
    folder.mkdir(parents=True, exist_ok=True)
    day = report['date']
    (folder / f'{day}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (folder / 'latest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    dates = sorted((p.stem for p in folder.glob('????????.json')), reverse=True)
    (folder / 'index.json').write_text(json.dumps(dates), encoding='utf-8')


def refresh_research(report):
    selected = set(report['moneyRanking'][:3] + report['attentionRanking'][:3])
    focus = {s['code']: s for g in report['sectors'] if g['sector'] in selected
             for s in g['moneyLeaders'][:2] + g['newsLeaders'][:2]}
    news = {c: fetch_news(c, s['name'], report['date']) for c, s in list(focus.items())[:18]}
    for g in report['sectors']:
        for stock in g['moneyLeaders'] + g['newsLeaders']:
            n = news.get(stock['code'])
            if n is not None:
                stock['research'] = explain(n['articles'])
                stock['newsFetchStatus'] = n['status']
        codes = {s['code'] for s in g['moneyLeaders'] + g['newsLeaders']}
        g['research'] = explain([a for c in codes for a in news.get(c, {}).get('articles', [])])
    report['researchVersion'] = 2
    report['newsRequests'] = {'requested': len(news), 'failed': sum(n['status'] == 'failed' for n in news.values()),
                              'withDatedEvidence': sum(bool(n['articles']) for n in news.values())}
    return report


def main():
    now = dt.datetime.now(KST)
    if now.weekday() >= 5 or now.time() < dt.time(15, 35):
        print('한국 장 마감 이후 평일에만 생성'); return
    day = now.strftime('%Y%m%d')
    if (OUT / f'{day}.json').exists():
        report = json.loads((OUT / f'{day}.json').read_text(encoding='utf-8'))
        if report.get('researchVersion', 0) < 2 or report.get('newsRequests', {}).get('failed', 0):
            save(refresh_research(report))
            print('기존 가격 집계 유지, 뉴스 재조사', report['newsRequests'])
        else:
            print('당일 보고서 이미 생성')
        return
    tok = token()
    if not stock_day(rows(kr_period(tok, '005930', '1', day, 21)), {}, day):
        print('당일 대표 종목 봉 없음: 휴장 또는 데이터 미도착'); return
    codes = list(universe())
    data, errors = {}, 0
    for i, (code, market) in enumerate(codes, 1):
        try:
            response = kr_period(tok, code, market, day, 21)
            meta = (response.get('data') or {}).get('Output_0') or {}
            if isinstance(meta, list): meta = meta[0] if meta else {}
            d = stock_day(rows(response), meta, day)
            if d:
                # Preserve missing news counts instead of treating missing as zero.
                d['news'] = None if rows(response)[0].get('news_cnt') in (None, '') else d['news']
                data[code] = d
        except Exception:
            errors += 1
        if i % 100 == 0:
            print(f'가격 수집 {i}/{len(codes)}, 당일 유효 {len(data)}, 오류 {errors}', flush=True)
    previous = None
    for p in sorted(OUT.glob('????????.json'), reverse=True):
        if p.stem < day:
            previous = json.loads(p.read_text(encoding='utf-8')); break
    report = build(day, data, len(codes), previous=previous)
    if report['status'] != 'ok':
        raise RuntimeError(f'Coverage insufficient: {len(data)}/{len(codes)}; latest report retained')
    report['collectionErrors'] = errors
    refresh_research(report)
    save(report)
    print('시황 저장', day, report['coverage'], report['newsRequests'])

if __name__ == '__main__': main()
