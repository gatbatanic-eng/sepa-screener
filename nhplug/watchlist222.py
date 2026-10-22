"""Auditable 2-2-2 observation candidates, never order instructions.
Thresholds are initial research rules, not backtested profitability claims.
"""
import math
import datetime as dt
import re

VERSION = 1
MIN_TURNOVER = 10_000_000_000  # 100억 KRW
MIN_CHANGE = 2.0
MIN_SURGE = 1.5
DOMESTIC = ('한국 정부', '우리 정부', '국회', '대통령실', '산업통상', '과학기술정보통신부', '국토교통부', '금융위원회', '한국은행', '중소벤처기업부', '보건복지부')
POLICY = ('정책', '예산', '지원', '법안', '규제', '발표', '일정', '육성')
THEMES = {'로봇·자동화 정책': ('로봇', '자동화'), '원전 정책': ('원전', '원자력'),
          '바이오·의료 정책': ('바이오', '의료', '제약'), '반도체·AI 정책': ('반도체', '인공지능', 'AI'),
          '전력·에너지 정책': ('전력망', '재생에너지', '태양광'), '방산 정책': ('방산', '국방'),
          '자본시장 정책': ('증시', '자본시장', '기업가치 제고', '밸류업')}
EVENTS = ('수주', '공급계약', '실적', '영업이익', '영업익', '매출', '흑자전환', '임상', '승인', '인수', '합병', '자사주', '배당')
RISK_WORDS = ('유상증자', '전환사채', '거래정지', '관리종목', '상장폐지', '적자', '실적 부진')


def numeric(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def universe(report):
    """Legacy reports expose only sector leaders; do not mislabel them as 700 scored names."""
    pool = report.get('stockUniverse')
    scope = 'full_universe' if pool is not None else 'sector_leaders_only'
    if pool is None:
        pool = [dict(s, sector=g['sector']) for g in report['sectors'] for s in g['moneyLeaders'] + g['newsLeaders']]
    unique = {}
    for stock in pool:
        code = stock['code']
        if code not in unique or stock.get('newsEvidence') or stock.get('research'):
            unique[code] = dict(stock)
    return list(unique.values()), scope


def evidence(stock, day):
    articles = stock.get('newsEvidence') or [a for c in stock.get('research', {}).get('catalysts', []) for a in c['evidence']]
    return list({a['url']: a for a in articles if re.sub(r'\D', '', str(a.get('publishedAt', '')))[:8] == day and str(a.get('url', '')).startswith('https://')}.values())


def liquid(stock):
    return (all(numeric(stock.get(k)) for k in ('turnoverKRW', 'changePct', 'turnoverVs20d'))
            and stock['turnoverKRW'] >= MIN_TURNOVER and stock['changePct'] >= MIN_CHANGE
            and stock['turnoverVs20d'] >= MIN_SURGE
            and not stock['name'].upper().startswith(('KODEX', 'TIGER', 'PLUS ', 'ACE ', 'SOL ', 'ETN', 'KOSEF', 'HANARO', 'RISE ')))


def reviewable(stock):
    return numeric(stock.get('turnoverKRW')) and numeric(stock.get('changePct')) and stock['turnoverKRW'] >= 5_000_000_000 and stock['changePct'] > 0 and not stock['name'].upper().startswith(('KODEX', 'TIGER', 'PLUS ', 'ACE ', 'SOL ', 'ETN', 'KOSEF', 'HANARO', 'RISE '))


def score(stock):
    # No missing-value bonus. Only available measures contribute.
    return round(min(25, math.log10(max(1, stock['turnoverKRW']) / 1e8) * 25/3)
                 + min(25, stock['changePct'] * 25/15) + min(20, (stock.get('turnoverVs20d') or 0) * 4)
                 + min(15, (stock.get('providerNewsCount') or 0) / 2)
                 + (15 if stock.get('nearHigh250') is True else 0), 2)


def domestic_themes(articles):
    themes = set()
    for a in articles:
        text = a['title'] + ' ' + a.get('excerpt', '')
        # Explicit Korean institution/decision context, not merely any Korean article.
        if any(k in text for k in DOMESTIC) and any(k in text for k in POLICY):
            themes.update(name for name, keys in THEMES.items() if any(k in text for k in keys))
    return sorted(themes)


def individual_material(stock, articles):
    name = re.sub(r'\s+', '', stock['name'])
    return [a for a in articles if name in re.sub(r'\s+', '', a['title'] + a.get('excerpt', ''))
            and any(k in a['title'] + a.get('excerpt', '') for k in EVENTS)]


def news_pool(report, limit=30):
    stocks, _ = universe(report)
    eligible = sorted((s for s in stocks if reviewable(s)), key=lambda s: (-score(s), s['code']))
    # First inspect one rising/liquid name per sector, then fill by score.
    focus, sectors = {}, set()
    for stock in eligible:
        if stock['sector'] not in sectors:
            focus[stock['code']] = stock; sectors.add(stock['sector'])
    for stock in eligible:
        focus.setdefault(stock['code'], stock)
    return list(focus.values())[:limit]


def select(report):
    stocks, scope = universe(report)
    eligible = sorted((s for s in stocks if liquid(s)), key=lambda s: (-score(s), s['code']))
    by_sector = {g['sector']: g for g in report['sectors']}
    articles = {s['code']: evidence(s, report['date']) for s in stocks}
    groups = [{'id': 'market', 'title': '시장 주도 섹터', 'items': [], 'missingReason': '현재 평가 범위에서 상승 확산과 거래대금·재료 조건을 함께 만족하는 후보 부족'},
              {'id': 'domestic', 'title': '국내 고유 이슈·테마', 'items': [], 'missingReason': '현재 평가 범위에서 한국 정책·일정 보도와 관련 2종목 동반 강세를 확인하지 못함'},
              {'id': 'individual', 'title': '개별 재료', 'items': [], 'missingReason': '현재 평가 범위에서 개별 사건 보도와 거래대금·변동성 조건을 함께 만족하는 후보 부족'}]
    used = set()

    def add(group, stock, reason, theme=None):
        a = articles[stock['code']]
        text = ' '.join(x['title'] + x.get('excerpt', '') for x in a)
        group['items'].append(dict(stock, score=score(stock), reason=reason, theme=theme,
            evidence=a[:3], risks=[w for w in RISK_WORDS if w in text],
            checks={'liquidity': True, 'momentum': True, 'newsEvidence': bool(a),
                    'attentionRank': None, 'catalystContinuity': None, 'ma15m120': None,
                    'high250': stock.get('nearHigh250')},
            reviewRequired=['재료의 다음 날 연속성', '실제 조회 순위', '장중 수급', '15분봉 120기간선'],
            observationPlan={'basis': '다음 거래일 관찰용 · 장중 재확인 필요',
                             'referenceClose': stock.get('close'), 'referenceHigh': stock.get('high'), 'referenceLow': stock.get('low'),
                             'high250': stock.get('high250'),
                             'entryCondition': '기준일 고가 돌파·유지 또는 눌림 지지 + 거래대금 재유입 확인',
                             'invalidation': '기준일 저가 이탈·재료 소멸 시 시나리오 재검토. 실제 손절가·수량은 진입 전에 정함'}))
        used.add(stock['code'])

    sectors = sorted((g for g in report['sectors'] if g['stockCount'] >= 3 and g['meanChangePct'] > 0 and g['advancingPct'] >= 50),
                     key=lambda g: -(g['turnoverSharePct'] * g['advancingPct'] / 100 * g['meanChangePct']))
    for g in sectors:
        for stock in eligible:
            if stock['sector'] == g['sector'] and articles[stock['code']] and stock['code'] not in used and any(k in a['title'] + a.get('excerpt', '') for a in articles[stock['code']] for k in EVENTS + ('수요', '투자', '증설', '정책', '지원', '신제품', '출시')):
                add(groups[0], stock, f"{g['sector']} 평균 {g['meanChangePct']:+.2f}%, 상승 비율 {g['advancingPct']}% + 거래대금·재료 확인")
                if len(groups[0]['items']) == 2: break
        if len(groups[0]['items']) == 2: break
    theme_members = {}
    for stock in eligible:
        for theme in domestic_themes(articles[stock['code']]):
            theme_members.setdefault(theme, []).append(stock)
    for theme, members in sorted(theme_members.items(), key=lambda kv: -sum(score(s) for s in kv[1])):
        # Require two distinct related advancing names BEFORE allocating disjoint slots.
        if len({s['code'] for s in members}) < 2: continue
        for stock in members:
            if stock['code'] not in used:
                add(groups[1], stock, f'{theme}: 한국 정책 보도 + 관련 {len(members)}종목 거래대금·상승 확인. 국내 고유성 검토 필요', theme)
                if len(groups[1]['items']) == 2: break
        if len(groups[1]['items']) == 2: break
    for stock in eligible:
        if stock['code'] not in used and individual_material(stock, articles[stock['code']]):
            add(groups[2], stock, '해당 기업의 실적·수주·기업 이벤트 보도 + 거래대금 급증·상승 확인')
            if len(groups[2]['items']) == 2: break
    all_items = [s for g in groups for s in g['items']]
    zero = max(all_items, key=lambda s: s['score'], default=None)
    return {'version': VERSION, 'date': report['date'], 'generatedAt': dt.datetime.now(dt.timezone.utc).isoformat(), 'purpose': '다음 거래일 관심종목 준비 · 매수 신호 아님',
            'status': 'complete' if len(all_items) == 6 else 'partial',
            'groups': groups, 'selectedCount': len(all_items),
            'reviewPool': [dict(s, score=score(s), strictEligible=liquid(s), evidence=articles[s['code']][:3],
                                domesticThemes=domestic_themes(articles[s['code']]),
                                individualMaterial=bool(individual_material(s, articles[s['code']]))) for s in sorted((x for x in stocks if reviewable(x)), key=lambda x: (-score(x), x['code']))[:40]],
            'zeroPriority': {'code': zero['code'], 'name': zero['name'], 'reason': '선정 후보 중 관찰 점수 최상위. 장중 진입 판단과 별개'} if zero else None,
            'coverage': {'scope': scope, 'evaluated': len(stocks), 'marketCollected': report['coverage']['valid'], 'eligible': len(eligible)},
            'rules': {'minTurnoverKRW': MIN_TURNOVER, 'minChangePct': MIN_CHANGE, 'minTurnoverVs20d': MIN_SURGE,
                      'score': '거래대금 25 + 상승률 25 + 거래대금 배수 20 + 뉴스 건수 15 + 250봉 고가 근접 15 (결측 시 가점 없음) (초기 연구 규칙, 수익성 미검증)'},
            'limitations': ['미국 전일장 연결 분석 미연결', '실시간 조회 순위 미연결: 뉴스 노출은 대용 지표',
                           '국내 고유 이슈는 기사 분류 후보이며 사람이 국내 고유성·일정·공시 원문을 검토',
                           '재료 연속성과 장중 수급·15분봉 120기간선은 자동 확인하지 않음']}
