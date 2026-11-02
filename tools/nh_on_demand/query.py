"""Read-only NHPLUG snapshots. Standard library only; no trading endpoints."""
import argparse
import datetime as dt
import html
import http.client
import json
import math
import os
from pathlib import Path
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request

KST = dt.timezone(dt.timedelta(hours=9))
HOST = 'https://api.nhplug.com:8443'
ALLOWED = {'currentPrice', 'period', 'currentInvestor'}

class DataError(Exception):
    pass


def request_json(url, data, headers):
    # Never expose URLs, response bodies, credentials or exception details.
    for attempt in range(3):
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise DataError('API response is not an object')
            return result
        except urllib.error.HTTPError as exc:
            if attempt < 2 and (exc.code == 429 or exc.code >= 500):
                time.sleep(2 ** attempt)
                continue
            raise DataError('API HTTP ' + str(exc.code)) from None
        except (OSError, ValueError, http.client.HTTPException):
            if attempt == 2:
                raise DataError('API network/JSON failure') from None
            time.sleep(2 ** attempt)


class Client:
    def __init__(self):
        key, secret = os.getenv('NHPLUG_APPKEY'), os.getenv('NHPLUG_APPSECRET')
        if not key or not secret:
            raise DataError('Missing repository secrets: NHPLUG_APPKEY / NHPLUG_APPSECRET')
        params = urllib.parse.urlencode(dict(appkey=key, appsecretkey=secret,
                                            grant_type='client_credentials', scope='oob'))
        response = request_json(HOST + '/oauth2/token?' + params, b'',
                                {'Content-Type': 'application/x-www-form-urlencoded'})
        self.token = response.get('access_token')
        if not self.token:
            raise DataError('Authentication failed: no access token')

    def call(self, endpoint, body):
        if endpoint not in ALLOWED:
            raise DataError('Endpoint is not read-only approved')
        time.sleep(.4)
        result = request_json(HOST + '/krstock/quote/v1/' + endpoint,
                              json.dumps({'Input_0': body}).encode(),
                              {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token})
        message = result.get('message')
        code = result.get('rsp_cd') or (message.get('msg_code') if isinstance(message, dict) else None)
        if code is not None and str(code) != '00000':
            raise DataError('API business response not successful')
        return result


def rows(data, key):
    value = data.get(key, [])
    return [value] if isinstance(value, dict) else value if isinstance(value, list) else []


def number(value):
    try:
        value = float(str(value).replace(',', ''))
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def period_body(code, market):
    return dict(market_cd='KRX', iem_cd=code, mrkt_div_cls_code='4' if market == 'KOSDAQ' else '1',
                edate=dt.datetime.now(KST).strftime('%Y%m%d'), array_cnt='0400', maxavg='000',
                gubun='1', xtick='000', today_cls_code='0', fake_tick='1', sur_flag='0',
                sur_gb_day_cnt='00', sur_bf_end_time='', out1_scale_change='0',
                out2_scale_change='0', view_main_yn='Y')


def daily_bars(data):
    result = {}
    for row in rows(data, 'Output_1'):
        date = str(row.get('bsop_date', ''))
        try:
            date = dt.datetime.strptime(date, '%Y%m%d').date().isoformat()
        except ValueError:
            raise DataError('Invalid daily bar date') from None
        bar = {'date': date}
        for field, source in [('open', 'stck_oprc'), ('high', 'stck_hgpr'),
                              ('low', 'stck_lwpr'), ('close', 'stck_prpr'), ('volume', 'acml_vol')]:
            bar[field] = number(row.get(source, row.get('vol') if field == 'volume' else None))
        if any(bar[k] is None for k in ('open', 'high', 'low', 'close', 'volume')):
            raise DataError('Missing daily OHLCV')
        if not (0 < bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']) or bar['volume'] < 0:
            raise DataError('Invalid daily OHLCV')
        if date in result:
            raise DataError('Duplicate daily date')
        result[date] = bar
    if not result:
        raise DataError('No daily bars returned')
    return [result[key] for key in sorted(result)]


def aggregate(bars, timeframe):
    groups = {}
    for bar in bars:
        date = dt.date.fromisoformat(bar['date'])
        key = date.isocalendar()[:2] if timeframe == 'weekly' else (date.year, date.month)
        groups.setdefault(key, []).append(bar)
    result = []
    for group in groups.values():
        result.append(dict(date=group[-1]['date'], start=group[0]['date'], open=group[0]['open'],
                           high=max(x['high'] for x in group), low=min(x['low'] for x in group),
                           close=group[-1]['close'], volume=sum(x['volume'] for x in group)))
    # Without an exchange calendar, do not promise boundary period completeness.
    if result:
        result[0]['boundaryMayBePartial'] = True
        result[-1]['boundaryMayBePartial'] = True
    return result


def ema(values, period):
    result = [None] * len(values)
    if len(values) >= period:
        result[period - 1] = sum(values[:period]) / period
        for i in range(period, len(values)):
            result[i] = values[i] * (2 / (period + 1)) + result[i - 1] * (1 - 2 / (period + 1))
    return result


def indicators(bars):
    values = [x['close'] for x in bars]
    e12, e26 = ema(values, 12), ema(values, 26)
    macd = [a - b if b is not None else None for a, b in zip(e12, e26)]
    signal = [None] * min(25, len(values)) + ema([x for x in macd if x is not None], 9)
    gains = losses = None
    for i, bar in enumerate(bars):
        for period in (5, 20, 60, 120):
            bar['ma' + str(period)] = sum(values[i + 1 - period:i + 1]) / period if i + 1 >= period else None
        bar['rsi14'] = None
        if i == 14:
            changes = [values[j] - values[j - 1] for j in range(1, 15)]
            gains = sum(max(x, 0) for x in changes) / 14
            losses = sum(max(-x, 0) for x in changes) / 14
        elif i > 14:
            change = values[i] - values[i - 1]
            gains = (gains * 13 + max(change, 0)) / 14
            losses = (losses * 13 + max(-change, 0)) / 14
        if gains is not None:
            bar['rsi14'] = 50 if gains == losses == 0 else 100 if losses == 0 else 100 - 100 / (1 + gains / losses)
        bar['macd'] = macd[i]
        bar['macdSignal'] = signal[i]
        bar['macdHistogram'] = macd[i] - signal[i] if signal[i] is not None else None
        std = statistics.pstdev(values[i - 19:i + 1]) if i >= 19 else None
        bar['bollingerUpper'] = bar['ma20'] + 2 * std if std is not None else None
        bar['bollingerLower'] = bar['ma20'] - 2 * std if std is not None else None
    return bars


def collect(client, code, market):
    started = dt.datetime.now(KST).isoformat()
    quote_data = rows(client.call('currentPrice', {'market_cd': 'KRX', 'iem_cd': code}), 'Output_0')
    if not quote_data or (number(quote_data[0].get('stck_prpr')) or 0) <= 0:
        raise DataError('No valid current price')
    observed = dt.datetime.now(KST).isoformat()
    quote = quote_data[0]
    daily = daily_bars(client.call('period', period_body(code, market)))
    if daily[-1]['date'] > dt.datetime.now(KST).date().isoformat():
        raise DataError('Future daily date')
    quote_date = str(quote.get('bsop_date') or '')
    warnings = ['일회성 조회 결과입니다. 자동 갱신하지 않습니다.',
                '수정주가 적용 여부 미검증: 기업행사 전후 지표 해석에 주의.',
                '최대 400개 일봉 범위이며 주·월봉의 첫/마지막 구간은 미완성일 수 있습니다.',
                '장중 마지막 봉과 보조지표는 변할 수 있습니다.']
    if not re.fullmatch(r'\d{8}', quote_date):
        warnings.append('현재가 응답에 거래일이 없어 시각만으로 최신성을 확정할 수 없습니다.')
        quote_date = None
    investor, investor_error = [], None
    try:
        inv = client.call('currentInvestor', {'market_cd': 'KRX', 'iem_cd': code, 'array_cnt': '030'})
        keys = ('bsop_date1', 'bsop_date', 'invest', 'gigwan', 'person', 'program', 'frgn_ntby_qty')
        investor = [{k: row[k] for k in keys if k in row} for row in rows(inv, 'Output_0')]
        if not investor or not any(investor):
            investor_error = 'No recognized investor fields'
    except DataError as exc:
        investor_error = str(exc)
    if investor_error:
        warnings.append('수급 조회 일부 실패: ' + investor_error)
    return dict(schemaVersion=1, source='NHPLUG', exchange='KRX', code=code, market=market,
                name=str(quote.get('iem_nm') or code), startedAt=started,
                generatedAt=dt.datetime.now(KST).isoformat(), quoteObservedAt=observed,
                quoteSourceDate=quote_date, quoteSourceTime=quote.get('hoga_bsop_hour'),
                quoteFreshness='unverified', currentPrice=number(quote.get('stck_prpr')),
                latestDailyDate=daily[-1]['date'], warnings=warnings, investor=investor,
                investorUnits='API raw units; not interpreted',
                candles={'daily': indicators(daily), 'weekly': indicators(aggregate(daily, 'weekly')),
                         'monthly': indicators(aggregate(daily, 'monthly'))})


def chart(bars):
    bars = bars[-100:]
    low, high = min(x['low'] for x in bars), max(x['high'] for x in bars)
    span = high - low or 1
    y = lambda price: 270 - (price - low) / span * 240
    step = 880 / len(bars)
    parts = ['<svg viewBox="0 0 960 310" role="img" aria-label="캔들 차트">',
             f'<text x="4" y="25">{high:,.0f}</text><text x="4" y="275">{low:,.0f}</text>']
    for i, bar in enumerate(bars):
        x = 75 + (i + .5) * step
        color = '#dc3545' if bar['close'] >= bar['open'] else '#1769cc'
        parts.append(f'<line x1="{x}" x2="{x}" y1="{y(bar["high"])}" y2="{y(bar["low"])}" stroke="{color}"/>')
        parts.append(f'<rect x="{x-step*.3}" y="{min(y(bar["open"]),y(bar["close"]))}" width="{step*.6}" height="{max(1,abs(y(bar["open"])-y(bar["close"])))}" fill="{color}"/>')
    parts.append(f'<text x="75" y="300">{bars[0]["date"]}</text><text x="850" y="300">{bars[-1]["date"]}</text></svg>')
    return ''.join(parts)


def write_result(result, output):
    output.mkdir(parents=True, exist_ok=True)
    (output / 'snapshot.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    esc = lambda x: html.escape(str(x))
    summary = f'# {result["code"]} 나무 조회 결과\n\n조회 완료: {result["generatedAt"]}\n\n현재가: {result["currentPrice"]:,.0f}원 · 최신 일봉: {result["latestDailyDate"]}\n\n'
    summary += '\n'.join('- ' + x for x in result['warnings'])
    (output / 'summary.md').write_text(summary, encoding='utf-8')
    page = '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>나무 종목 조회</title><style>body{font:16px system-ui;max-width:1100px;margin:30px auto;padding:16px;color:#182334}svg{width:100%;background:#fafafa}td,th{padding:8px;border-bottom:1px solid #ddd}table{border-collapse:collapse}small{color:#566}</style>'
    page += f'<h1>{esc(result["name"])} · {result["code"]}</h1><h2>{result["currentPrice"]:,.0f}원</h2><p>조회 완료 {esc(result["generatedAt"])}</p><p>현재가 수신 {esc(result["quoteObservedAt"])} / 공급원 거래일 {esc(result["quoteSourceDate"])} / 시각 {esc(result["quoteSourceTime"])}</p>'
    page += '<ul>' + ''.join('<li>' + esc(x) + '</li>' for x in result['warnings']) + '</ul>'
    for key, title in [('daily', '일봉'), ('weekly', '주봉'), ('monthly', '월봉')]:
        bars = result['candles'][key]
        page += f'<h2>{title} · {len(bars)}개 중 최근 최대 100개 표시</h2>' + chart(bars)
        page += '<table><tr><th>마지막 봉 기준 지표</th><th>값</th></tr>'
        for field in ('ma5', 'ma20', 'ma60', 'ma120', 'rsi14', 'macd', 'macdSignal', 'macdHistogram', 'bollingerUpper', 'bollingerLower'):
            value = bars[-1][field]
            page += f'<tr><td>{field}</td><td>{value:,.2f}</td></tr>' if value is not None else f'<tr><td>{field}</td><td>기간 부족</td></tr>'
        page += '</table>'
    page += '<h2>수급 원자료</h2><p>수량 단위·잠정/확정 구분 미검증</p><pre>' + esc(json.dumps(result['investor'], ensure_ascii=False, indent=2)) + '</pre></html>'
    (output / 'chart.html').write_text(page, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--code', required=True)
    parser.add_argument('--market', choices=['KOSPI', 'KOSDAQ'], default='KOSPI')
    parser.add_argument('--output', type=Path, default=Path('nh-results'))
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9A-Z]{6}', args.code):
        parser.error('Use a six-character KRX security code')
    args.output.mkdir(parents=True, exist_ok=True)
    for name in ('snapshot.json', 'chart.html', 'summary.md'):
        (args.output / name).unlink(missing_ok=True)
    try:
        result = collect(Client(), args.code, args.market)
        write_result(result, args.output)
    except DataError as exc:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / 'summary.md').write_text('# 조회 실패\n\n' + str(exc), encoding='utf-8')
        raise SystemExit(str(exc)) from None


if __name__ == '__main__':
    main()
