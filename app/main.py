from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import os, sqlite3, asyncio, json
from datetime import datetime, timezone
import httpx, xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from app.paypal import router as paypal_router

BASE=os.path.dirname(os.path.dirname(__file__))
DB=os.getenv('DB_PATH',os.path.join(BASE,'money_hunter.db'))
TARGET_COUNTRY=os.getenv('TARGET_COUNTRY','Thailand').strip() or 'Thailand'
AUTO_CLAIM_ENABLED=os.getenv('AUTO_CLAIM_ENABLED','true').strip().lower()=='true'
app=FastAPI(title='Money Hunter AI Cloud',version='12.0')
app.include_router(paypal_router)

GAMERPOWER_API='https://www.gamerpower.com/api/giveaways?sort-by=date'
RSS_SOURCES=[('Reddit Freebies','https://www.reddit.com/r/freebies/.rss')]
SCAM=['seed phrase','private key','otp','gift card payment','pay a fee to receive','advance fee','wire money','โอนเงินก่อน','รหัส otp']
PURCHASE=['purchase required','with purchase','buy one','minimum spend','order required','ต้องซื้อ','ยอดซื้อ','ซื้อครบ']
CARD=['credit card required','debit card required','card required','บัตรเครดิต','บัตรเดบิต']
SUBS=['subscription required','paid membership','trial converts','สมาชิกแบบเสียเงิน']
SURVEY=['complete a survey','survey required','ทำแบบสอบถาม']
REFERRAL=['refer a friend','referral required','invite friends','ชวนเพื่อน']
WORLD=['worldwide','global','international','region free','region-free','available in most countries','ทั่วโลก']
THAI=['thailand','ประเทศไทย','bangkok','กรุงเทพ','.th']
REGIONS={'United States':['us only','u.s. only','usa only','united states only','u.s. residents'],'United Kingdom':['uk only','united kingdom only','uk residents'],'Canada':['canada only','canadian residents'],'Australia':['australia only','australian residents'],'India':['india only','indian residents']}


def conn():
 c=sqlite3.connect(DB,timeout=20);c.row_factory=sqlite3.Row;return c

def has_col(c,t,n):return any(r[1]==n for r in c.execute(f'PRAGMA table_info({t})'))

def init_db():
 c=conn();c.execute('CREATE TABLE IF NOT EXISTS deals(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT,url TEXT UNIQUE,source TEXT,kind TEXT,risk TEXT,country TEXT,created_at TEXT)')
 cols=[('eligibility','TEXT',"'review'"),('eligibility_reason','TEXT',"''"),('simple_offer','INTEGER','0'),('requires_purchase','INTEGER','0'),('requires_card','INTEGER','0'),('requires_subscription','INTEGER','0'),('requires_survey','INTEGER','0'),('requires_referral','INTEGER','0'),('claim_mode','TEXT',"'manual'"),('claim_status','TEXT',"'new'"),('description','TEXT',"''"),('instructions','TEXT',"''"),('end_date','TEXT',"''"),('worth','TEXT',"''"),('platforms','TEXT',"''")]
 for n,t,d in cols:
  if not has_col(c,'deals',n):c.execute(f'ALTER TABLE deals ADD COLUMN {n} {t} DEFAULT {d}')
 c.execute('CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY AUTOINCREMENT,amount REAL,currency TEXT,status TEXT,reference TEXT,created_at TEXT)')
 c.execute('CREATE TABLE IF NOT EXISTS claim_log(id INTEGER PRIMARY KEY AUTOINCREMENT,deal_id INTEGER,status TEXT,message TEXT,created_at TEXT)')
 c.execute('CREATE TABLE IF NOT EXISTS source_health(source TEXT PRIMARY KEY,status TEXT,message TEXT,checked_at TEXT,found INTEGER DEFAULT 0)')
 c.commit();c.close()
init_db()


def clean(s):return ' '.join(BeautifulSoup(str(s or ''),'html.parser').get_text(' ',strip=True).split())
def detect_region(x):
 for r,h in REGIONS.items():
  if any(k in x for k in h):return r
 return None

def classify(text,source=''):
 x=text.lower();rp=any(k in x for k in PURCHASE);rc=any(k in x for k in CARD);rs=any(k in x for k in SUBS);rv=any(k in x for k in SURVEY);rr=any(k in x for k in REFERRAL);complex_req=rp or rc or rs or rv or rr
 region=detect_region(x)
 if any(k in x for k in THAI):elig='eligible';reason='พบข้อความรองรับประเทศไทย';country='Thailand'
 elif any(k in x for k in WORLD):elig='eligible';reason='พบข้อความระบุว่ากว้างหลายประเทศ/ทั่วโลก';country='Worldwide'
 elif region:elig='regional';reason=f'จำกัดภูมิภาค: {region}';country=region
 elif source=='GamerPower':elig='review';reason='GamerPower คัดรายการที่เปิดอยู่และโดยทั่วไปเข้าถึงได้หลายประเทศ แต่ต้องเช็กภูมิภาคของรายการ';country='Global candidate'
 else:elig='review';reason='ยังไม่พบข้อความยืนยันประเทศ';country='Unknown'
 simple=int(not complex_req and elig in ('eligible','review'))
 return elig,reason,country,simple,int(rp),int(rc),int(rs),int(rv),int(rr)

def risk(text):return 'high' if any(k in text.lower() for k in SCAM) else 'low'

def upsert_health(source,status,message,found=0):
 c=conn();c.execute('INSERT INTO source_health(source,status,message,checked_at,found) VALUES(?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET status=excluded.status,message=excluded.message,checked_at=excluded.checked_at,found=excluded.found',(source,status,message,datetime.now(timezone.utc).isoformat(),found));c.commit();c.close()

async def scan_gamerpower(client):
 try:
  r=await client.get(GAMERPOWER_API);r.raise_for_status();data=r.json()
  if not isinstance(data,list):data=[]
  added=0
  for g in data[:250]:
   title=clean(g.get('title'));desc=clean(g.get('description'));inst=clean(g.get('instructions'));url=g.get('open_giveaway_url') or g.get('gamerpower_url') or ''
   if not title or not url:continue
   text=' '.join([title,desc,inst,clean(g.get('platforms')),clean(g.get('type'))])
   elig,reason,country,simple,rp,rc,rs,rv,rr=classify(text,'GamerPower');rk=risk(text)
   c=conn();cur=c.execute('''INSERT OR IGNORE INTO deals(title,url,source,kind,risk,country,created_at,eligibility,eligibility_reason,simple_offer,requires_purchase,requires_card,requires_subscription,requires_survey,requires_referral,claim_mode,claim_status,description,instructions,end_date,worth,platforms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(title[:300],url,'GamerPower',clean(g.get('type') or 'Giveaway'),rk,country,datetime.now(timezone.utc).isoformat(),elig,reason,simple,rp,rc,rs,rv,rr,'manual','ready' if simple and rk!='high' else 'new',desc[:2000],inst[:2000],clean(g.get('end_date')),clean(g.get('worth')),clean(g.get('platforms'))));added+=max(cur.rowcount,0);c.commit();c.close()
  upsert_health('GamerPower','ok','API ใช้งานได้',len(data));return added,len(data)
 except Exception as e:
  upsert_health('GamerPower','error',type(e).__name__,0);return 0,0

async def scan_rss(client,name,url):
 try:
  r=await client.get(url);r.raise_for_status();root=ET.fromstring(r.content);found=added=0
  for e in root.iter():
   if e.tag.split('}')[-1].lower() not in ('item','entry'):continue
   title=summary=link=''
   for ch in list(e):
    n=ch.tag.split('}')[-1].lower()
    if n=='title' and not title:title=''.join(ch.itertext()).strip()
    elif n in ('summary','description','content') and not summary:summary=' '.join(''.join(ch.itertext()).split())
    elif n=='link' and not link:link=(ch.attrib.get('href') or (ch.text or '')).strip()
   text=clean(title+' '+summary)
   if not title or not any(k in text.lower() for k in ['free','giveaway','sample','coupon','cashback','rebate']):continue
   found+=1;elig,reason,country,simple,rp,rc,rs,rv,rr=classify(text,name);rk=risk(text)
   c=conn();cur=c.execute('''INSERT OR IGNORE INTO deals(title,url,source,kind,risk,country,created_at,eligibility,eligibility_reason,simple_offer,requires_purchase,requires_card,requires_subscription,requires_survey,requires_referral,claim_mode,claim_status,description) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(clean(title)[:300],link or url,name,'Freebie',rk,country,datetime.now(timezone.utc).isoformat(),elig,reason,simple,rp,rc,rs,rv,rr,'manual','ready' if simple and rk!='high' else 'new',clean(summary)[:2000]));added+=max(cur.rowcount,0);c.commit();c.close()
  upsert_health(name,'ok','RSS ใช้งานได้',found);return added,found
 except Exception as e:
  upsert_health(name,'error',type(e).__name__,0);return 0,0

async def scan_once():
 async with httpx.AsyncClient(headers={'User-Agent':'MoneyHunterAI/12.0 personal-use'},follow_redirects=True,timeout=20) as client:
  a,total=await scan_gamerpower(client)
  for n,u in RSS_SOURCES:
   x,y=await scan_rss(client,n,u);a+=x;total+=y
  return {'added':a,'found':total}

def authorized_connectors():
 try:return json.loads(os.getenv('AUTHORIZED_CLAIM_CONNECTORS','{}'))
 except Exception:return {}

def auto_claimable(d):
 cfg=authorized_connectors().get(d['source']);return bool(AUTO_CLAIM_ENABLED and d.get('simple_offer') and d.get('risk')!='high' and d.get('eligibility')=='eligible' and isinstance(cfg,dict) and cfg.get('endpoint') and cfg.get('automation_permitted'))

async def claim_one(d):
 if not d:return {'ok':False,'message':'ไม่พบรายการ'}
 if not auto_claimable(d):return {'ok':False,'needs_user':True,'message':'รายการนี้ค้นพบจริง แต่แหล่งต้นทางยังไม่มี API ที่อนุญาตให้บอทกดรับแทน'}
 cfg=authorized_connectors()[d['source']];headers={'User-Agent':'MoneyHunterAI/12.0'};te=cfg.get('token_env')
 if te and os.getenv(te):headers['Authorization']='Bearer '+os.getenv(te)
 try:
  async with httpx.AsyncClient(timeout=20,follow_redirects=False) as client:r=await client.post(cfg['endpoint'],json={'offer_url':d['url'],'offer_title':d['title'],'target_country':TARGET_COUNTRY},headers=headers)
  ok=200<=r.status_code<300;status='submitted' if ok else 'failed';msg='ส่งคำขอรับสิทธิ์แล้ว' if ok else f'ผู้ให้บริการตอบ HTTP {r.status_code}'
 except Exception as e:ok=False;status='failed';msg=type(e).__name__
 c=conn();c.execute('UPDATE deals SET claim_status=?,claim_mode=? WHERE id=?',(status,'authorized_api',d['id']));c.execute('INSERT INTO claim_log(deal_id,status,message,created_at) VALUES(?,?,?,?)',(d['id'],status,msg,datetime.now(timezone.utc).isoformat()));c.commit();c.close();return {'ok':ok,'message':msg}

async def auto_claim_all():
 c=conn();rows=[dict(x) for x in c.execute("SELECT * FROM deals WHERE claim_status='ready' AND risk!='high' LIMIT 100")];c.close();attempted=0;submitted=0
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
def deals():
 c=conn();rows=[dict(x) for x in c.execute("SELECT * FROM deals WHERE risk!='high' ORDER BY CASE source WHEN 'GamerPower' THEN 0 ELSE 1 END, id DESC LIMIT 400")];c.close()
 for d in rows:d['auto_claimable']=auto_claimable(d)
 return rows
@app.post('/api/claim/{deal_id}')
async def claim(deal_id:int):
 c=conn();r=c.execute('SELECT * FROM deals WHERE id=?',(deal_id,)).fetchone();c.close();return await claim_one(dict(r) if r else None)
@app.post('/api/claim-all')
async def claim_all():
 a,b=await auto_claim_all();return {'ok':True,'attempted':a,'submitted':b}
@app.get('/api/source-health')
def source_health():
 c=conn();r=[dict(x) for x in c.execute('SELECT * FROM source_health ORDER BY source')];c.close();return r
@app.get('/api/stats')
def stats():
 c=conn();total=c.execute("SELECT COUNT(*) FROM deals WHERE risk!='high'").fetchone()[0];gp=c.execute("SELECT COUNT(*) FROM deals WHERE source='GamerPower' AND risk!='high'").fetchone()[0];eligible=c.execute("SELECT COUNT(*) FROM deals WHERE eligibility='eligible' AND risk!='high'").fetchone()[0];ready=c.execute("SELECT COUNT(*) FROM deals WHERE claim_status='ready' AND risk!='high'").fetchone()[0];submitted=c.execute("SELECT COUNT(*) FROM deals WHERE claim_status='submitted'").fetchone()[0];c.close();return {'total':total,'gamerpower':gp,'eligible':eligible,'ready':ready,'submitted':submitted}
@app.get('/api/wallet')
def wallet():
 c=conn();rows=[dict(x) for x in c.execute("SELECT * FROM ledger WHERE status='confirmed' ORDER BY id DESC")];c.close();total=sum(float(x['amount']) for x in rows if (x['currency'] or '').upper()=='THB');return {'confirmed_thb':round(total,2),'transactions':rows}
@app.get('/api/readiness')
def readiness():
 return {'version':'12.0','target_country':TARGET_COUNTRY,'auto_scan_minutes':30,'auto_claim_enabled':AUTO_CLAIM_ENABLED,'authorized_auto_claim_sources':len(authorized_connectors()),'message':'สามมาร: ค้นหา → ตรวจสิทธิ์ → รับอัตโนมัติเมื่อแหล่งอนุญาต API'}
@app.get('/health')
def health():return {'ok':True,'version':'12.0'}

HTML='''<!doctype html><html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Money Hunter AI v12</title><style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f4f6fa;margin:0;color:#172033}.wrap{max-width:980px;margin:auto;padding:20px}.hero{background:#0f172a;color:white;border-radius:24px;padding:24px}.money{font-size:42px;font-weight:800}.btn{border:0;border-radius:13px;padding:13px 16px;font-weight:750;cursor:pointer}.green{background:#22c55e}.gray{background:#e5e7eb}.full{width:100%;margin-top:10px}.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-top:12px}.card{background:#fff;border-radius:18px;padding:16px;margin-top:12px;box-shadow:0 4px 18px #0000000a}.n{font-size:26px;font-weight:800}.muted{color:#697386;font-size:13px}.deal{border-top:1px solid #eee;padding:12px 0}.tag{display:inline-block;background:#eef2ff;border-radius:999px;padding:4px 8px;font-size:12px;margin-right:4px}.ok{background:#dcfce7}.warn{background:#fef3c7}.bad{background:#fee2e2}.claim{background:#16a34a;color:#fff;margin-top:7px}.disabled{background:#e5e7eb;color:#667085;margin-top:7px}.agent{background:#f8fafc;border-radius:12px;padding:10px;margin-top:8px}@media(max-width:720px){.grid{grid-template-columns:1fr 1fr}}</style></head><body><div class=wrap><div class=hero><b>Money Hunter AI v12 — สามมาร</b><div class=muted style="color:#cbd5e1">มารค้นหา → มารตรวจ → มารรับ</div><div id=money class=money>฿0.00</div><div>เงินจริงที่ผู้ให้บริการยืนยันแล้ว</div><button id=scan class="btn green full" onclick="go()">🌍 ให้สามมารออกค้นหาทั่วโลกตอนนี้</button><button class="btn gray full" onclick="claimAll()">⚡ รับอัตโนมัติทุกรายการที่ระบบได้รับอนุญาต</button></div><div class=grid><div class=card><div class=muted>พบทั้งหมด</div><div class=n id=total>0</div></div><div class=card><div class=muted>GamerPower</div><div class=n id=gp>0</div></div><div class=card><div class=muted>ผ่านประเทศ</div><div class=n id=eligible>0</div></div><div class=card><div class=muted>คิวพร้อมรับ</div><div class=n id=ready>0</div></div><div class=card><div class=muted>ส่งรับแล้ว</div><div class=n id=submitted>0</div></div></div><div class=card><b>สถานะสามมาร</b><div class=agent id=a1>🔎 มารค้นหา: กำลังตรวจ...</div><div class=agent id=a2>🛡️ มารตรวจ: กำลังตรวจ...</div><div class=agent id=a3>⚡ มารรับ: กำลังตรวจ...</div></div><div class=card><b>รายการฟรีที่พบจริง</b><div id=deals></div></div></div><script>function e(s){return String(s||'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]))}async function load(){let w=await fetch('/api/wallet').then(r=>r.json());money.textContent='฿'+Number(w.confirmed_thb||0).toLocaleString('th-TH',{minimumFractionDigits:2});let st=await fetch('/api/stats').then(r=>r.json());total.textContent=st.total;gp.textContent=st.gamerpower;eligible.textContent=st.eligible;ready.textContent=st.ready;submitted.textContent=st.submitted;let h=await fetch('/api/source-health').then(r=>r.json());let gh=h.find(x=>x.source==='GamerPower');a1.textContent=gh&&gh.status==='ok'?'🔎 มารค้นหา: GamerPower API ใช้งานได้ พบ '+gh.found+' รายการ':'🔎 มารค้นหา: '+(gh?gh.message:'รอรอบค้นหา');a2.textContent='🛡️ มารตรวจ: คัดของเสี่ยงสูง/ต้องซื้อ/จำกัดประเทศก่อนรับ';let rd=await fetch('/api/readiness').then(r=>r.json());a3.textContent='⚡ มารรับ: แหล่ง API ที่อนุญาตให้กดรับอัตโนมัติ '+rd.authorized_auto_claim_sources+' แหล่ง';let ds=await fetch('/api/deals').then(r=>r.json());deals.innerHTML=ds.slice(0,50).map(d=>{let cl=d.eligibility==='eligible'?'ok':d.eligibility==='regional'?'bad':'warn';let b=d.auto_claimable?`<button class="btn claim" onclick="claim(${d.id},this)">AI รับให้เลย</button>`:`<button class="btn disabled" disabled>ค้นพบแล้ว — แหล่งนี้ยังไม่เปิด API ให้กดรับแทน</button>`;return `<div class=deal><span class=tag>${e(d.source)}</span><span class="tag ${cl}">${e(d.eligibility)}</span><div><b>${e(d.title)}</b></div><div class=muted>${e(d.platforms)} ${d.worth?'· มูลค่า '+e(d.worth):''}</div><div class=muted>${e(d.eligibility_reason)}</div>${b}</div>`}).join('')||'<div class=muted>ยังไม่มีรายการ กดค้นหาด้านบน</div>'}async function go(){scan.disabled=true;scan.textContent='สามมารกำลังทำงาน...';let r=await fetch('/api/search').then(r=>r.json());await load();scan.textContent='พบ '+r.found+' รายการ · เพิ่มใหม่ '+r.added+' รายการ';setTimeout(()=>{scan.textContent='🌍 ให้สามมารออกค้นหาทั่วโลกตอนนี้';scan.disabled=false},2500)}async function claim(id,b){b.disabled=true;b.textContent='กำลังรับ...';let r=await fetch('/api/claim/'+id,{method:'POST'}).then(r=>r.json());alert(r.message);await load()}async function claimAll(){let r=await fetch('/api/claim-all',{method:'POST'}).then(r=>r.json());alert('ลองรับ '+r.attempted+' รายการ / ส่งสำเร็จ '+r.submitted+' รายการ');await load()}load()</script></body></html>'''
@app.get('/',response_class=HTMLResponse)
def home():return HTML
