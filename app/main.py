from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
import os, sqlite3, asyncio, json
from datetime import datetime, timezone
from urllib.parse import quote
import httpx, xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from app.paypal import router as paypal_router

BASE=os.path.dirname(os.path.dirname(__file__))
DB=os.getenv('DB_PATH',os.path.join(BASE,'money_hunter.db'))
TARGET_COUNTRY=os.getenv('TARGET_COUNTRY','Thailand')
AUTO_CLAIM_ENABLED=os.getenv('AUTO_CLAIM_ENABLED','true').lower()=='true'
app=FastAPI(title='Money Hunter AI Deep Hunter',version='14.0')
app.include_router(paypal_router)

SCAM=['seed phrase','private key','gift card payment','pay a fee to receive','advance fee','wire money','โอนเงินก่อน','crypto deposit','wallet seed']
PURCHASE=['purchase required','with purchase','buy one','minimum spend','order required','ต้องซื้อ','ยอดซื้อ','ซื้อครบ','spend $','spend £','spend €']
CARD=['credit card required','debit card required','card required','บัตรเครดิต','บัตรเดบิต']
SUBS=['subscription required','paid membership','trial converts','auto-renew','สมาชิกแบบเสียเงิน']
SURVEY=['complete a survey','survey required','ทำแบบสอบถาม']
REFERRAL=['refer a friend','referral required','invite friends','ชวนเพื่อน']
WORLD=['worldwide','global','international','region free','region-free','available in most countries','ทั่วโลก']
THAI=['thailand','ประเทศไทย','bangkok','กรุงเทพ','.th']
REGIONS={'United States':['us only','u.s. only','usa only','united states only','u.s. residents'],'United Kingdom':['uk only','united kingdom only','uk residents'],'Canada':['canada only','canadian residents'],'Australia':['australia only','australian residents'],'India':['india only','indian residents']}
MONEY=['cash','cashback','rebate','paypal','reward','credit','grant','เงินคืน','เครดิตฟรี','รางวัลเงินสด']
PHYSICAL=['sample','product testing','tester','beauty','skincare','cosmetic','food','drink','pet','household','perfume','demo product','free product','สินค้าทดลอง','ตัวอย่างสินค้า','ของฟรี']
DIGITAL=['game','dlc','steam','epic','gog','software','license','ebook','course','cloud','api credit','digital','pc','playstation','xbox','nintendo','loot','beta','key']
RSS_SOURCES=[('Reddit Freebies','https://www.reddit.com/r/freebies/.rss')]

def conn():
 c=sqlite3.connect(DB,timeout=20);c.row_factory=sqlite3.Row;return c

def has_col(c,t,n):return any(r[1]==n for r in c.execute(f'PRAGMA table_info({t})'))

def init_db():
 c=conn();c.execute('CREATE TABLE IF NOT EXISTS deals(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT,url TEXT UNIQUE,source TEXT,kind TEXT,risk TEXT,country TEXT,created_at TEXT)')
 cols=[('eligibility','TEXT',"'review'"),('eligibility_reason','TEXT',"''"),('simple_offer','INTEGER','0'),('requires_purchase','INTEGER','0'),('requires_card','INTEGER','0'),('requires_subscription','INTEGER','0'),('requires_survey','INTEGER','0'),('requires_referral','INTEGER','0'),('claim_mode','TEXT',"'direct'"),('claim_status','TEXT',"'new'"),('description','TEXT',"''"),('instructions','TEXT',"''"),('end_date','TEXT',"''"),('worth','TEXT',"''"),('platforms','TEXT',"''"),('hunter_type','TEXT',"'digital'"),('grade','TEXT',"'C'"),('collected_type','TEXT',"''"),('direct_claim_url','TEXT',"''"),('source_trust','TEXT',"'public'"),('manual_reason','TEXT',"''")]
 for n,t,d in cols:
  if not has_col(c,'deals',n):c.execute(f'ALTER TABLE deals ADD COLUMN {n} {t} DEFAULT {d}')
 c.execute('CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY AUTOINCREMENT,amount REAL,currency TEXT,status TEXT,reference TEXT,created_at TEXT)')
 c.execute('CREATE TABLE IF NOT EXISTS claim_log(id INTEGER PRIMARY KEY AUTOINCREMENT,deal_id INTEGER,status TEXT,message TEXT,created_at TEXT)')
 c.execute('CREATE TABLE IF NOT EXISTS source_health(source TEXT PRIMARY KEY,status TEXT,message TEXT,checked_at TEXT,found INTEGER DEFAULT 0)')
 c.commit();c.close()
init_db()

def clean(s):return ' '.join(BeautifulSoup(str(s or ''),'html.parser').get_text(' ',strip=True).split())
def now():return datetime.now(timezone.utc).isoformat()
def detect_region(x):
 for r,h in REGIONS.items():
  if any(k in x for k in h):return r
 return None

def hunter_type(text):
 x=text.lower()
 if any(k in x for k in MONEY):return 'money'
 if any(k in x for k in PHYSICAL):return 'physical'
 return 'digital'

def classify(text,source=''):
 x=text.lower();rp=any(k in x for k in PURCHASE);rc=any(k in x for k in CARD);rs=any(k in x for k in SUBS);rv=any(k in x for k in SURVEY);rr=any(k in x for k in REFERRAL);complex_req=rp or rc or rs or rv or rr
 region=detect_region(x)
 if any(k in x for k in THAI):elig='eligible';reason='รองรับประเทศไทย';country='Thailand'
 elif any(k in x for k in WORLD):elig='eligible';reason='รองรับหลายประเทศ/ทั่วโลก';country='Worldwide'
 elif region:elig='regional';reason='จำกัดภูมิภาค: '+region;country=region
 elif source in ('GamerPower','Epic Games','Steam'):elig='review';reason='พบจากแหล่งสาธารณะ ต้องยืนยัน region ก่อนรับ';country='Global candidate'
 else:elig='review';reason='ยังไม่พบข้อความยืนยันประเทศ';country='Unknown'
 simple=int(not complex_req and elig in ('eligible','review'))
 if rp or rc or rs:grade='D'
 elif rv or rr:grade='C'
 elif elig=='eligible':grade='A'
 elif elig=='review':grade='B'
 else:grade='D'
 return elig,reason,country,simple,int(rp),int(rc),int(rs),int(rv),int(rr),grade

def risk(text):return 'high' if any(k in text.lower() for k in SCAM) else 'low'

def health(source,status,message,found=0):
 c=conn();c.execute('INSERT INTO source_health(source,status,message,checked_at,found) VALUES(?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET status=excluded.status,message=excluded.message,checked_at=excluded.checked_at,found=excluded.found',(source,status,message,now(),found));c.commit();c.close()

def save_deal(title,url,source,kind,text,description='',instructions='',end_date='',worth='',platforms='',trust='public'):
 if not title or not url:return 0
 elig,reason,country,simple,rp,rc,rs,rv,rr,grade=classify(text,source);rk=risk(text);ht=hunter_type(text)
 manual='ต้องล็อกอิน/ยืนยันตามกติกาของต้นทาง' if source in ('Epic Games','Steam','GamerPower') else ''
 c=conn();cur=c.execute('''INSERT INTO deals(title,url,source,kind,risk,country,created_at,eligibility,eligibility_reason,simple_offer,requires_purchase,requires_card,requires_subscription,requires_survey,requires_referral,claim_mode,claim_status,description,instructions,end_date,worth,platforms,hunter_type,grade,direct_claim_url,source_trust,manual_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET title=excluded.title,kind=excluded.kind,risk=excluded.risk,country=excluded.country,eligibility=excluded.eligibility,eligibility_reason=excluded.eligibility_reason,simple_offer=excluded.simple_offer,requires_purchase=excluded.requires_purchase,requires_card=excluded.requires_card,requires_subscription=excluded.requires_subscription,requires_survey=excluded.requires_survey,requires_referral=excluded.requires_referral,description=excluded.description,instructions=excluded.instructions,end_date=excluded.end_date,worth=excluded.worth,platforms=excluded.platforms,hunter_type=excluded.hunter_type,grade=excluded.grade,direct_claim_url=excluded.direct_claim_url,source_trust=excluded.source_trust,manual_reason=excluded.manual_reason''',(clean(title)[:300],url,source,kind,rk,country,now(),elig,reason,simple,rp,rc,rs,rv,rr,'direct','ready' if simple and rk!='high' else 'new',clean(description)[:2000],clean(instructions)[:2000],clean(end_date),clean(worth),clean(platforms),ht,grade,url,trust,manual));c.commit();c.close();return max(cur.rowcount,0)

async def scan_gamerpower(client):
 try:
  r=await client.get('https://www.gamerpower.com/api/giveaways?sort-by=date');r.raise_for_status();data=r.json();data=data if isinstance(data,list) else [];added=0
  for g in data[:300]:
   title=clean(g.get('title'));desc=clean(g.get('description'));inst=clean(g.get('instructions'));url=g.get('open_giveaway_url') or g.get('gamerpower_url') or ''
   text=' '.join([title,desc,inst,clean(g.get('platforms')),clean(g.get('type')),'global digital giveaway'])
   added+=save_deal(title,url,'GamerPower',clean(g.get('type') or 'Giveaway'),text,desc,inst,g.get('end_date',''),g.get('worth',''),g.get('platforms',''),'aggregator')
  health('GamerPower','ok','API ใช้งานได้',len(data));return added,len(data)
 except Exception as e:health('GamerPower','error',type(e).__name__,0);return 0,0

async def scan_epic(client):
 try:
  url='https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=en-US&country=TH&allowCountries=TH'
  r=await client.get(url);r.raise_for_status();data=r.json();els=((data.get('data') or {}).get('Catalog') or {}).get('searchStore',{}).get('elements',[]);added=0;found=0
  for g in els:
   promos=(g.get('promotions') or {}).get('promotionalOffers') or []
   if not promos:continue
   free=False;end=''
   for p in promos:
    for o in p.get('promotionalOffers',[]):
     if ((o.get('discountSetting') or {}).get('discountPercentage')==0):free=True;end=o.get('endDate','')
   if not free:continue
   title=clean(g.get('title'));slug=g.get('productSlug') or g.get('urlSlug') or ''
   claim='https://store.epicgames.com/en-US/p/'+slug if slug else 'https://store.epicgames.com/en-US/free-games'
   text=title+' free game epic Thailand digital';added+=save_deal(title,claim,'Epic Games','Free Game',text,end_date=end,platforms='Epic Games Store',trust='official-public-endpoint');found+=1
  health('Epic Games','ok','ตรวจโปรโมชั่นฟรีสำหรับประเทศไทยได้',found);return added,found
 except Exception as e:health('Epic Games','error',type(e).__name__,0);return 0,0

async def scan_steam(client):
 try:
  url='https://store.steampowered.com/search/results/?maxprice=free&specials=1&cc=TH&l=english&json=1'
  r=await client.get(url);r.raise_for_status();data=r.json();html=data.get('results_html','');s=BeautifulSoup(html,'html.parser');added=found=0
  for a in s.select('a.search_result_row')[:100]:
   title=clean((a.select_one('.title') or {}).get_text() if a.select_one('.title') else '')
   href=a.get('href','').split('?')[0]
   if not title or not href:continue
   text=title+' Steam free special Thailand digital';added+=save_deal(title,href,'Steam','Free Special',text,platforms='Steam',trust='public-store-endpoint');found+=1
  health('Steam','ok','ตรวจ free specials ได้',found);return added,found
 except Exception as e:health('Steam','error',type(e).__name__,0);return 0,0

async def scan_rss(client,name,url):
 try:
  r=await client.get(url);r.raise_for_status();root=ET.fromstring(r.content);added=found=0
  for e in root.iter():
   if e.tag.split('}')[-1].lower() not in ('item','entry'):continue
   title=summary=link=''
   for ch in list(e):
    n=ch.tag.split('}')[-1].lower()
    if n=='title' and not title:title=''.join(ch.itertext()).strip()
    elif n in ('summary','description','content') and not summary:summary=' '.join(''.join(ch.itertext()).split())
    elif n=='link' and not link:link=(ch.attrib.get('href') or (ch.text or '')).strip()
   text=clean(title+' '+summary)
   if not title or not any(k in text.lower() for k in ['free','giveaway','sample','coupon','cashback','rebate','demo','product testing']):continue
   found+=1;added+=save_deal(title,link or url,name,'Freebie',text,summary,trust='community')
  health(name,'ok','RSS ใช้งานได้',found);return added,found
 except Exception as e:health(name,'error',type(e).__name__,0);return 0,0

async def scan_once():
 async with httpx.AsyncClient(headers={'User-Agent':'MoneyHunterAI/14.0 personal-use'},follow_redirects=True,timeout=20) as client:
  results=await asyncio.gather(scan_gamerpower(client),scan_epic(client),scan_steam(client),*[scan_rss(client,n,u) for n,u in RSS_SOURCES])
  return {'added':sum(x[0] for x in results),'found':sum(x[1] for x in results)}

def authorized_connectors():
 try:return json.loads(os.getenv('AUTHORIZED_CLAIM_CONNECTORS','{}'))
 except Exception:return {}

def auto_claimable(d):
 cfg=authorized_connectors().get(d['source']);return bool(AUTO_CLAIM_ENABLED and d.get('simple_offer') and d.get('risk')!='high' and d.get('eligibility')=='eligible' and isinstance(cfg,dict) and cfg.get('endpoint') and cfg.get('automation_permitted'))

async def claim_one(d):
 if not d:return {'ok':False,'message':'ไม่พบรายการ'}
 if not auto_claimable(d):return {'ok':False,'direct':d.get('direct_claim_url') or d.get('url'),'message':'ต้นทางยังไม่เปิด API ให้รับแทน ใช้ปุ่มไปหน้ารับจริงได้ทันที'}
 cfg=authorized_connectors()[d['source']];headers={'User-Agent':'MoneyHunterAI/14.0'};te=cfg.get('token_env')
 if te and os.getenv(te):headers['Authorization']='Bearer '+os.getenv(te)
 try:
  async with httpx.AsyncClient(timeout=20,follow_redirects=False) as client:r=await client.post(cfg['endpoint'],json={'offer_url':d['url'],'offer_title':d['title'],'target_country':TARGET_COUNTRY},headers=headers)
  ok=200<=r.status_code<300;status='submitted' if ok else 'failed';msg='ส่งคำขอรับสิทธิ์แล้ว' if ok else f'ต้นทางตอบ HTTP {r.status_code}'
 except Exception as e:ok=False;status='failed';msg=type(e).__name__
 c=conn();c.execute('UPDATE deals SET claim_status=?,claim_mode=? WHERE id=?',(status,'authorized_api',d['id']));c.execute('INSERT INTO claim_log(deal_id,status,message,created_at) VALUES(?,?,?,?)',(d['id'],status,msg,now()));c.commit();c.close();return {'ok':ok,'message':msg}

async def auto_claim_all():
 c=conn();rows=[dict(x) for x in c.execute("SELECT * FROM deals WHERE claim_status='ready' AND risk!='high' AND grade IN ('A','B') LIMIT 100")];c.close();attempted=submitted=0
 for d in rows:
  if auto_claimable(d):
   attempted+=1;r=await claim_one(d);submitted+=1 if r.get('ok') else 0
 return attempted,submitted

@app.on_event('startup')
async def startup():
 async def loop():
  await asyncio.sleep(2)
  while True:
   try:await scan_once();await auto_claim_all()
   except Exception as e:print('background',type(e).__name__)
   await asyncio.sleep(1800)
 asyncio.create_task(loop())

@app.get('/api/search')
async def search():
 s=await scan_once();a,b=await auto_claim_all();return {'ok':True,**s,'claim_attempted':a,'claim_submitted':b}
@app.get('/api/deals')
def deals(hunter:str='all'):
 c=conn();q="SELECT * FROM deals WHERE risk!='high'";args=[]
 if hunter in ('money','physical','digital'):q+=' AND hunter_type=?';args.append(hunter)
 q+=" ORDER BY CASE grade WHEN 'A' THEN 0 WHEN 'B' THEN 1 WHEN 'C' THEN 2 ELSE 3 END,id DESC LIMIT 500"
 rows=[dict(x) for x in c.execute(q,args)];c.close()
 for d in rows:d['auto_claimable']=auto_claimable(d)
 return rows
@app.post('/api/claim/{deal_id}')
async def claim(deal_id:int):
 c=conn();r=c.execute('SELECT * FROM deals WHERE id=?',(deal_id,)).fetchone();c.close();return await claim_one(dict(r) if r else None)
@app.get('/claim/{deal_id}')
def direct_claim(deal_id:int):
 c=conn();r=c.execute('SELECT direct_claim_url,url FROM deals WHERE id=?',(deal_id,)).fetchone();c.close()
 if not r:raise HTTPException(404)
 return RedirectResponse(r['direct_claim_url'] or r['url'])
@app.post('/api/claim-all')
async def claim_all():
 a,b=await auto_claim_all();return {'ok':True,'attempted':a,'submitted':b}
@app.get('/api/source-health')
def source_health():
 c=conn();r=[dict(x) for x in c.execute('SELECT * FROM source_health ORDER BY source')];c.close();return r
@app.get('/api/stats')
def stats():
 c=conn();total=c.execute("SELECT COUNT(*) FROM deals WHERE risk!='high'").fetchone()[0];money=c.execute("SELECT COUNT(*) FROM deals WHERE hunter_type='money' AND risk!='high'").fetchone()[0];physical=c.execute("SELECT COUNT(*) FROM deals WHERE hunter_type='physical' AND risk!='high'").fetchone()[0];digital=c.execute("SELECT COUNT(*) FROM deals WHERE hunter_type='digital' AND risk!='high'").fetchone()[0];a=c.execute("SELECT COUNT(*) FROM deals WHERE grade='A' AND risk!='high'").fetchone()[0];submitted=c.execute("SELECT COUNT(*) FROM deals WHERE claim_status='submitted'").fetchone()[0];c.close();return {'total':total,'money':money,'physical':physical,'digital':digital,'grade_a':a,'submitted':submitted}
@app.get('/api/wallet')
def wallet():
 c=conn();rows=[dict(x) for x in c.execute("SELECT * FROM ledger WHERE status='confirmed' ORDER BY id DESC")];c.close();total=sum(float(x['amount']) for x in rows if (x['currency'] or '').upper()=='THB');return {'confirmed_thb':round(total,2),'transactions':rows}
@app.get('/api/readiness')
def readiness():return {'version':'14.0','target_country':TARGET_COUNTRY,'auto_scan_minutes':30,'authorized_auto_claim_sources':len(authorized_connectors()),'message':'Deep Hunter: public APIs + store endpoints + direct claim router'}
@app.get('/health')
def health():return {'ok':True,'version':'14.0'}

HTML='''<!doctype html><html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Money Hunter AI v14</title><style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f4f6fa;margin:0;color:#172033}.wrap{max-width:1000px;margin:auto;padding:20px}.hero{background:#0f172a;color:white;border-radius:24px;padding:24px}.btn{border:0;border-radius:13px;padding:13px 16px;font-weight:750;cursor:pointer}.green{background:#22c55e}.blue{background:#dbeafe}.full{width:100%;margin-top:10px}.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}.card{background:#fff;border-radius:18px;padding:16px;margin-top:12px}.n{font-size:26px;font-weight:800}.muted{color:#697386;font-size:13px}.deal{border-top:1px solid #eee;padding:12px 0}.tag{display:inline-block;background:#eef2ff;border-radius:999px;padding:4px 8px;font-size:12px;margin-right:4px}.A{background:#dcfce7}.B{background:#fef3c7}.C,.D{background:#fee2e2}.tabs button{margin:3px}.direct{display:inline-block;text-decoration:none;background:#2563eb;color:#fff;border-radius:12px;padding:10px 12px;font-weight:700;margin-top:7px}@media(max-width:720px){.grid{grid-template-columns:1fr 1fr}}</style></head><body><div class=wrap><div class=hero><b>Money Hunter AI v14 — Deep Hunter</b><div>ค้นลึกแบบถูกสิทธิ์: API • ร้านค้า • RSS • หน้ารับตรง</div><button id=scan class="btn green full" onclick="go()">🌍 ปล่อยสามมารค้นลึกทั่วโลก</button></div><div class=grid><div class=card><div>ทั้งหมด</div><div class=n id=total>0</div></div><div class=card><div>💰 เงิน</div><div class=n id=money>0</div></div><div class=card><div>🎁 สินค้า</div><div class=n id=physical>0</div></div><div class=card><div>💻 ดิจิทัล</div><div class=n id=digital>0</div></div><div class=card><div>เกรด A</div><div class=n id=gradea>0</div></div></div><div class=card><b>แหล่งค้นหา</b><div id=health class=muted></div></div><div class=card><div class=tabs><button class="btn blue" onclick="load('all')">ทั้งหมด</button><button class="btn blue" onclick="load('money')">💰 เงิน</button><button class="btn blue" onclick="load('physical')">🎁 สินค้า</button><button class="btn blue" onclick="load('digital')">💻 ดิจิทัล</button></div><div id=deals></div></div></div><script>function esc(s){return String(s||'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]))}async function stats(){let s=await fetch('/api/stats').then(r=>r.json());total.textContent=s.total;money.textContent=s.money;physical.textContent=s.physical;digital.textContent=s.digital;gradea.textContent=s.grade_a;let h=await fetch('/api/source-health').then(r=>r.json());health.innerHTML=h.map(x=>`${esc(x.source)}: ${esc(x.status)} · พบ ${x.found}`).join('<br>')}async function load(h='all'){let ds=await fetch('/api/deals?hunter='+h).then(r=>r.json());deals.innerHTML=ds.slice(0,80).map(d=>{let action=d.auto_claimable?`<button class="btn green" onclick="autoClaim(${d.id},this)">AI รับให้เลย</button>`:`<a class=direct href="/claim/${d.id}" target="_blank">ไปหน้ารับจริง →</a>`;return `<div class=deal><span class="tag ${esc(d.grade)}">เกรด ${esc(d.grade)}</span><span class=tag>${esc(d.source)}</span><span class=tag>${esc(d.hunter_type)}</span><div><b>${esc(d.title)}</b></div><div class=muted>${esc(d.eligibility_reason)} ${d.worth?'· '+esc(d.worth):''}</div>${action}</div>`}).join('')||'ยังไม่มีรายการ';await stats()}async function go(){scan.disabled=true;scan.textContent='กำลังค้นลึก...';let r=await fetch('/api/search').then(r=>r.json());await load();scan.textContent='พบ '+r.found+' รายการ';setTimeout(()=>{scan.textContent='🌍 ปล่อยสามมารค้นลึกทั่วโลก';scan.disabled=false},2200)}async function autoClaim(id,b){b.disabled=true;let r=await fetch('/api/claim/'+id,{method:'POST'}).then(r=>r.json());if(r.ok){alert(r.message)}else if(r.direct){window.open(r.direct,'_blank')}else{alert(r.message)}await load()}load()</script></body></html>'''
@app.get('/',response_class=HTMLResponse)
def home():return HTML
