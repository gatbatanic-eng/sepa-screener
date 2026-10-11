"""Bounded original-XBRL data probe, run using existing repository secret only.

No account signals, no prices, no model calls, no publication to existing feeds.
"""
import os,pathlib,json,datetime,time,zipfile,io,xml.etree.ElementTree as ET,hashlib,re,urllib.request,urllib.parse
P=pathlib.Path('research-dart-probe');CODES=os.environ.get('DART_SAMPLE_CODES','000120,000150,000240').split(',');MAX_DOCS=48;SOFT_SECONDS=1500
def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8')
def local(tag):return tag.rsplit('}',1)[-1]
def fetch(endpoint,**params):
    key=os.environ.get('DART_API_KEY','').strip()
    if not key:raise RuntimeError('DART_API_KEY missing in existing remote secret context')
    params['crtfc_key']=key;url='https://opendart.fss.or.kr/api/'+endpoint+'?'+urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'GoldenCodeResearch/1.0'})
            with urllib.request.urlopen(req,timeout=25) as response:raw=response.read()
        except Exception:raise RuntimeError('DART transport error; request details suppressed') from None
        if raw[:2]==b'PK':return raw
        try:
            if endpoint.endswith('.json'):d=json.loads(raw);status=d.get('status');message=d.get('message','')
            else:
                tree=ET.fromstring(raw);status=tree.findtext('status');message=tree.findtext('message','')
        except Exception:raise RuntimeError('DART response is not expected JSON/XML/ZIP') from None
        if status=='800' and attempt<2:time.sleep(5*(attempt+1));continue
        if status not in ('000','013'):raise RuntimeError('DART status='+str(status)+' '+str(message)[:120])
        return raw
    raise RuntimeError('DART system unavailable after bounded retries')
def extract_candidates(raw):
    # Only collect accounting facts and their contexts. No economic score until verified.
    wanted={'GrossProfit','Assets','Revenue','CostOfSales','ProfitLoss','BasicEarningsLossPerShare','DilutedEarningsLossPerShare','CashFlowsFromUsedInOperatingActivities','CurrentAssets','CurrentLiabilities','NoncurrentLiabilities','Equity','DepreciationDepletionAndAmortisationExpense','CashAndCashEquivalents','CurrentTaxLiabilities','CurrentBorrowings','NoncurrentBorrowings','CurrentPortionOfNoncurrentBorrowings','NumberOfSharesOutstanding','NumberOfOrdinarySharesOutstanding','IssuedCapital'}
    records=[]
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for name in z.namelist():
            if not name.lower().endswith(('.xml','.xbrl')):continue
            try:root=ET.fromstring(z.read(name))
            except ET.ParseError:continue
            context={};units={}
            for node in root.iter():
                if local(node.tag)=='unit':units[node.attrib.get('id')]=[x.text for x in node.iter() if local(x.tag)=='measure']
            for node in root.iter():
                if local(node.tag)=='context':context[node.attrib.get('id')]=dict(period={local(x.tag):x.text for x in node.iter() if local(x.tag) in ['startDate','endDate','instant']},entities=[x.text for x in node.iter() if local(x.tag)=='identifier'],dimensions=[dict(dimension=x.attrib.get('dimension'),member=x.text) for x in node.iter() if local(x.tag)=='explicitMember'])
            for node in root.iter():
                tag=local(node.tag)
                if tag in wanted and 'contextRef' in node.attrib:
                    records.append(dict(file=name,tag=node.tag,text=node.text,unit=node.attrib.get('unitRef'),unitMeasures=units.get(node.attrib.get('unitRef')),decimals=node.attrib.get('decimals'),context=context.get(node.attrib['contextRef']),contextRef=node.attrib['contextRef']))
    return records
def main():
    P.mkdir(exist_ok=True);(P/'raw').mkdir(exist_ok=True);now=datetime.datetime.now(datetime.timezone.utc).isoformat();started=time.monotonic()
    assert all(re.fullmatch(r'\d{6}',code) for code in CODES)
    plan=dict(startedAt=now,sampleCodes=CODES,selection='first24 lexicographic original337 codes split into4 batches of6, fixed before financial performance; deliberate pilot sample not full market',filingRange=['2018-01-01','2026-10-08'],priority='2018..2025 original annual reports;8years include financial warmup; staged collection not strategy change',maxOriginalDocuments=MAX_DOCS,softLimitSeconds=SOFT_SECONDS,releaseNote='original regular filing date is not necessarily earliest earnings release; PEAD requires IR/KIND comparison',productionEligible=False)
    save(P/'plan.json',plan);out=dict(plan=plan,status='running',companies=[],documents=[],errors=[])
    try:
        raw=fetch('corpCode.xml')
        if raw[:2]!=b'PK':raise RuntimeError('corporation ZIP unavailable')
        with zipfile.ZipFile(io.BytesIO(raw)) as z:tree=ET.fromstring(z.read(z.namelist()[0]))
        mapping={n.findtext('stock_code'):n.findtext('corp_code') for n in tree.findall('list')}
        for code in CODES:
            corp=mapping.get(code)
            if not corp:out['errors'].append(dict(code=code,reason='issuer mapping unavailable'));continue
            filings=[]
            for page in range(1,5):
                obj=json.loads(fetch('list.json',corp_code=corp,bgn_de='20180101',end_de='20261008',pblntf_ty='A',last_reprt_at='N',page_count=100,page_no=page))
                filings.extend(obj.get('list',[]))
                if page>=obj.get('total_page',0):break
            save(P/f'filings-{code}.json',filings);original={}
            metadata=json.loads(fetch('company.json',corp_code=corp));save(P/f'company-{code}.json',{k:metadata.get(k) for k in ['corp_code','corp_name','stock_code','induty_code','jurir_no','bizr_no']})
            for row in sorted(filings,key=lambda x:(x['rcept_dt'],x['rcept_no'])):
                name=row['report_nm'];match=re.search(r'(사업보고서|반기보고서|분기보고서)\s*\((\d{4})\.(\d{2})\)',name)
                if not match or '정정' in name:continue
                kind,year,month=match.groups();year=int(year);month=int(month)
                if not 2018<=year<=2025 or month!=12 or kind!='사업보고서':continue
                key=(year,month);original.setdefault(key,row)
            out['companies'].append(dict(code=code,corpCode=corp,filings=len(filings),originalPeriods=len(original)))
            for (year,month),row in sorted(original.items()):
                if len(out['documents'])>=MAX_DOCS or time.monotonic()-started>SOFT_SECONDS:break
                receipt=row['rcept_no'];reprt={3:'11013',6:'11012',9:'11014',12:'11011'}[month]
                try:
                    raw=fetch('fnlttXbrl.xml',rcept_no=receipt,reprt_code=reprt)
                    if raw[:2]!=b'PK':raise RuntimeError('original XBRL ZIP unavailable')
                    f=P/'raw'/f'{code}-{receipt}.zip';f.write_bytes(raw);facts=extract_candidates(raw);save(P/f'facts-{code}-{receipt}.json',facts)
                    out['documents'].append(dict(code=code,year=year,month=month,receipt=receipt,filedDate=row['rcept_dt'],reportName=row['report_nm'],sha256=hashlib.sha256(raw).hexdigest(),candidateFacts=len(facts),tags=sorted({local(x['tag']) for x in facts}),originalRegularFiling=True,earliestEarningsReleaseVerified=False))
                except RuntimeError as e:
                    out['errors'].append(dict(code=code,receipt=receipt,reason=str(e)))
                    if '800' in str(e) or 'transport' in str(e):raise
                save(P/'summary.json',out)
            print(code,'original documents',sum(x['code']==code for x in out['documents']),flush=True)
        out['status']='complete_data_probe'
    except RuntimeError as e:out['status']='unavailable';out['errors'].append(dict(reason=str(e)))
    finally:
        out['finishedAt']=datetime.datetime.now(datetime.timezone.utc).isoformat();save(P/'summary.json',out)
        print(json.dumps(dict(status=out['status'],documents=len(out['documents']),errors=out['errors'][-3:]),ensure_ascii=False))
    if out['status']=='unavailable':raise SystemExit(1)
if __name__=='__main__':main()
