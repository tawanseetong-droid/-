from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import os, sqlite3, asyncio, json, re
from datetime import datetime, timezone
import httpx, xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from app.paypal import router as paypal_router

BASE=os.path.dirname(os.path.dirname(__file__))
DB=os.getenv('DB_PATH',os.path.join(BASE,'money_hunter.db'))
TARGET_COUNTRY=os.getenv('TARGET_COUNTRY','Thailand').strip() or 'Thailand'
AUTO_CLAIM_ENABLED=os.getenv('AUTO_CLAIM_ENABLED','true').strip().lower()=='true'
app=FastAPI(title='Money Hunter AI Cloud',version='13.0')
app.include_router(paypal_router)

GAMERPOWER_API='https://www.gamerpower.com/api/giveaways?sort-by=date'
RSS_SOURCES=[('Reddit Freebies','https://www.reddit.com/r/freebies/.rss')]

SCAM=['seed phrase','private key','otp','gift card payment','pay a fee to receive','advance fee','wire money','โอนเงินก่อน','รหัส otp','crypto deposit','wallet seed']
PURCHASE=['purchase required','with purchase','buy one','minimum spend','order required','ต้องซื้อ','ยอดซื้อ','ซื้อครบ','spend $','spend £','spend €']
CARD=['credit card required','debit card required','card required','บัตรเครดิต','บัตรเดบิต']
SUBS=['subscription required','paid membership','trial converts','auto-renew','สมาชิกแบบเสียเงิน']
SURVEY=['complete a survey','survey required','ทำแบบสอบถาม']
REFERRAL=['refer a friend','referral required','invite friends','ชวนเพื่อน']
WORLD=['worldwide','global','international','region free','region-free','available in most countries','ทั่วโลก']
THAI=['thailand','ประเทศไทย','bangkok','กรุงเทพ','.th']
REGIONS={'United States':['us only','u.s. only','usa only','united states only','u.s. residents'],'United Kingdom':['uk only','united kingdom only','uk residents'],'Canada':['canada only','canadian residents'],'Australia':['australia only','australian residents'],'India':['india only','indian residents']}

MONEY_WORDS=['cash','cashback','rebate','paypal','reward','credit','grant','เงิน','เงินคืน','เครดิต','รางวัลเงินสด']
PHYSICAL_WORDS=['sample','product testing','tester','beauty','skincare','cosmetic','food','drink','pet','household','perfume','demo product','free product','สินค้าทดลอง','ตัวอย่างสินค้า','ของฟรี']
DIGITAL_WORDS=['game','dlc','steam','epic','gog','software','license','ebook','course','cloud','api credit','digital','pc','playstation','xbox','nintendo','loot','beta','key']

def conn():
 c=sqlite3.connect(DB,timeout=20);c.row_factory=sqlite3.Row;return c

def has_col(c,t,n):return any(r[1]==n for r in c.execute(f'PRAGMA table_info({t})'))

def init_db():
 c=conn();c.execute('CREATE TABLE IF NOT EXISTS deals(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT,url TEXT UNIQUE,source TEXT,kind TEXT,risk TEXT,country TEXT,created_at TEXT)')
 cols=[('eligibility','TEXT',"'review'"),('eligibility_reason','TEXT',"''"),('simple_offer','INTEGER','0'),('requires_purchase','INTEGER','0'),('requires_card','INTEGER','0'),('requires_subscription','INTEGER','0'),('requires_survey','INTEGER','0'),('requires_referral','INTEGER','0'),('claim_mode','TEXT',"'manual'"),('claim_status','TEXT',"'new'"),('description','TEXT',"''"),('instructions','TEXT',"''"),('end_date','TEXT',"''"),('worth','TEXT',"''"),('platforms','TEXT',"''"),('hunter_type','TEXT',"'digital'"),('grade','TEXT',"'C'"),('collected_type','TEXT',"''")]
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

def hunter_type(text,kind=''):
 x=(text+' '+kind).lower()
 if any(k in x for k in MONEY_WORDS):return 'money'
 if any(k in x for k in PHYSICAL_WORDS):return 'physical'
 return 'digital' if any(k in x for k in DIGITAL_WORDS) else 'digital'

def classify(text,source=''):
 x=text.lower();rp=any(k in x for k in PURCHASE);rc=any(k in x for k in CARD);rs=any(k in x for k in SUBS);rv=any(k in x for k in SURVEY);rr=any(k in x for k in REFERRAL);complex_req=rp or rc or rs or rv or rr
 region=detect_region(x)
 if any(k in x for k in THAI):elig='eligible';reason='รองรับประเทศไทย';country='Thailand'
 elif any(k in x for k in WORLD):elig='eligible';reason='ระบุว่าใช้ได้หลายประเทศ/ทั่วโลก';country='Worldwide'
 elif region:elig='regional';reason=f'จำกัดภูมิภาค: {region}';country=region
 elif source=='GamerPower':elig='review';reason='เป็นรายการเปิดอยู่ทั่วโลก แต่ต้องเช็ก region ของรายการ';country='Global candidate'
 else:elig='review';reason='ยังไม่พบข้อความยืนยันประเทศ';country='Unknown'
 simple=int(not complex_req and elig in ('eligible','review'))
 grade='A' if elig=='eligible' and not complex_req else ('B' if elig=='review' and not complex_req else ('C' if elig in ('eligible','review') else 'D'))
 if complex_req:grade='D' if (rp or rc or rs) else 'C'
 return elig,reason,country,simple,int(rp),int(rc),int(rs),int(rv),int(rr),grade

def risk(text):return 'high' if any(k in text.lower() for k in SCAM) else 'low'

def upsert_health(source,status,message,found=0):
 c=conn();c.execute('INSERT INTO source_health(source,status,message,checked_at,found) VALUES(?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET status=excluded.status,message=excluded.message,checked_at=excluded.checked_at,found=excluded.found',(source,status,message,datetime.now(timezone.utc).isoformat(),found));c.commit();c.close()

async def scan_gamerpower(client):
 try:
  r=await client.get(GAMERPOWER_API);r.raise_for_status();data=r.json();data=data if isinstance(data,list) else []
  added=0
  for g in data[:300]:
   title=clean(g.get('title'));desc=clean(g.get('description'));inst=clean(g.get('instructions'));kind=clean(g.get('type') or 'Giveaway');url=g.get('open_giveaway_url') or g.get('gamerpower_url') or ''
   if not title or not url:continue
   text=' '.join([title,desc,inst,clean(g.get('platforms')),kind])
   elig,reason,country,simple,rp,rc,rs,rv,rr,grade=classify(text,'GamerPower');rk=risk(text);ht=hunter_type(text,kind)
   c=conn();cur=c.execute('''INSERT INTO deals(title,url,source,kind,risk,country,created_at,eligibility,eligibility_reason,simple_offer,requires_purchase,requires_card,requires_subscription,requires_survey,requires_referral,claim_mode,claim_status,description,instructions,end_date,worth,platforms,hunter_type,grade) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET title=excluded.title,kind=excluded.kind,risk=excluded.risk,country=excluded.country,eligibility=excluded.eligibility,eligibility_reason=excluded.eligibility_reason,simple_offer=excluded.simple_offer,requires_purchase=excluded.requires_purchase,requires_card=excluded.requires_card,requires_subscription=excluded.requires_subscription,requires_survey=excluded.requires_survey,requires_referral=excluded.requires_referral,description=excluded.description,instructions=excluded.instructions,end_date=excluded.end_date,worth=excluded.worth,platforms=excluded.platforms,hunter_type=excluded.hunter_type,grade=excluded.grade''',(title[:300],url,'GamerPower',kind,rk,country,datetime.now(timezone.utc).isoformat(),elig,reason,simple,rp,rc,rs,rv,rr,'manual','ready' if simple and rk!='high' else 'new',desc[:2000],inst[:2000],clean(g.get('end_date')),clean(g.get('worth')),clean(g.get('platforms')),ht,grade));added+=max(cur.rowcount,0);c.commit();c.close()
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
   if not title or not any(k in text.lower() for k in ['free','giveaway','sample','coupon','cashback','rebate','demo','product testing']):continue
   found+=1;elig,reason,country,simple,rp,rc,rs,rv,rr,grade=classify(text,name);rk=risk(text);ht=hunter_type(text,'Freebie')
   c=conn();cur=c.execute('''INSERT INTO deals(title,url,source,kind,risk,country,created_at,eligibility,eligibility_reason,simple_offer,requires_purchase,requires_card,requires_subscription,requires_survey,requires_referral,claim_mode,claim_status,description,hunter_type,grade) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET title=excluded.title,risk=excluded.risk,country=excluded.country,eligibility=excluded.eligibility,eligibility_reason=excluded.eligibility_reason,simple_offer=excluded.simple_offer,description=excluded.description,hunter_type=excluded.hunter_type,grade=excluded.grade''',(clean(title)[:300],link or url,name,'Freebie',rk,country,datetime.now(timezone.utc).isoformat(),elig,reason,simple,rp,rc,rs,rv,rr,'manual','ready' if simple and rk!='high' else 'new',clean(summary)[:2000],ht,grade));added+=max(cur.rowcount,0);c.commit();c.close()
  upsert_health(name,'ok','RSS ใช้งานได้',found);return added,found
 except Exception as e:
  upsert_health(name,'error',type(e).__name__,0);return 0,0

async def scan_once():
 async with httpx.AsyncClient(headers={'User-Agent':'MoneyHunterAI/13.0 personal-use'},follow_redirects=True,timeout=20) as client:
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
 if not auto_claimable(d):return {'ok':False,'needs_user':True,'message':'ค้นพบจริง แต่ต้นทางยังไม่มี API ที่อนุญาตให้ระบบรับแทน'}
 cfg=authorized_connectors()[d['source']];headers={'User-Agent':'MoneyHunterAI/13.0'};te=cfg.get('token_env')
 if te and os.getenv(te):headers['Authorization']='Bearer '+os.getenv(te)
 try:
  async with httpx.AsyncClient(timeout=20,follow_redirects=False) as client:r=await client.post(cfg['endpoint'],json={'offer_url':d['url'],'offer_title':d['title'],'target_country':TARGET_COUNTRY},headers=headers)
  ok=200<=r.status_code<300;status='submitted' if ok else 'failed';msg='ส่งคำขอรับสิทธิ์แล้ว' if ok else f'ต้นทางตอบ HTTP {r.status_code}'
 except Exception as e:ok=False;status='failed';msg=type(e).__name__
 c=conn();c.execute('UPDATE deals SET claim_status=?,claim_mode=? WHERE id=?',(status,'authorized_api',d['id']));c.execute('INSERT INTO claim_log(deal_id,status,message,created_at) VALUES(?,?,?,?)',(d['id'],status,msg,datetime.now(timezone.utc).isoformat()));c.commit();c.close();return {'ok':ok,'message':msg}

async def auto_claim_all():
 c=conn();rows=[dict(x) for x in c.execute("SELECT * FROM deals WHERE claim_status='ready' AND risk!='high' AND grade IN ('A','B') LIMIT 100")];c.close();attempted=submitted=0
 for d in rows:
  if auto_claimable(d):attempted+=1;r=await claim_one(d);submitted+=1 if r.get('ok') else 0
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
@app.get('/api/collection')
def collection():
 c=conn();rows=[dict(x) for x in c.execute("SELECT * FROM deals WHERE claim_status IN ('submitted','confirmed') ORDER BY id DESC LIMIT 200")];c.close();return rows
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
 c=conn();get=lambda sql,*a:c.execute(sql,a).fetchone()[0]
 r={'total':get("SELECT COUNT(*) FROM deals WHERE risk!='high'"),'money':get("SELECT COUNT(*) FROM deals WHERE hunter_type='money' AND risk!='high'"),'physical':get("SELECT COUNT(*) FROM deals WHERE hunter_type='physical' AND risk!='high'"),'digital':get("SELECT COUNT(*) FROM deals WHERE hunter_type='digital' AND risk!='high'"),'grade_a':get("SELECT COUNT(*) FROM deals WHERE grade='A' AND risk!='high'"),'grade_b':get("SELECT COUNT(*) FROM deals WHERE grade='B' AND risk!='high'"),'submitted':get("SELECT COUNT(*) FROM deals WHERE claim_status='submitted'")};c.close();return r
@app.get('/api/wallet')
def wallet():
 c=conn();rows=[dict(x) for x in c.execute("SELECT * FROM ledger WHERE status='confirmed' ORDER BY id DESC")];c.close();total=sum(float(x['amount']) for x in rows if (x['currency'] or '').upper()=='THB');return {'confirmed_thb':round(total,2),'transactions':rows}
@app.get('/api/readiness')
def readiness():return {'version':'13.0','target_country':TARGET_COUNTRY,'auto_scan_minutes':30,'auto_claim_enabled':AUTO_CLAIM_ENABLED,'authorized_auto_claim_sources':len(authorized_connectors()),'message':'สามมาร 3 สาย: เงิน / สินค้าฟรี / ดิจิทัลฟรี'}
@app.get('/health')
def health():return {'ok':True,'version':'13.0'}

HTML='''<!doctype html><html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Money Hunter AI v13</title><style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f4f6fa;margin:0;color:#172033}.wrap{max-width:1040px;margin:auto;padding:20px}.hero{background:#0f172a;color:white;border-radius:24px;padding:24px}.money{font-size:42px;font-weight:800}.btn{border:0;border-radius:13px;padding:13px 16px;font-weight:750;cursor:pointer}.green{background:#22c55e}.gray{background:#e5e7eb}.full{width:100%;margin-top:10px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:12px}.card{background:#fff;border-radius:18px;padding:16px;margin-top:12px;box-shadow:0 4px 18px #0000000a}.n{font-size:26px;font-weight:800}.muted{color:#697386;font-size:13px}.deal{border-top:1px solid #eee;padding:12px 0}.tag{display:inline-block;background:#eef2ff;border-radius:999px;padding:4px 8px;font-size:12px;margin-right:4px}.A{background:#dcfce7}.B{background:#dbeafe}.C{background:#fef3c7}.D{background:#fee2e2}.claim{background:#16a34a;color:white;margin-top:7px}.disabled{background:#e5e7eb;color:#667085;margin-top:7px}.tabs{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.tab{background:#fff;border:1px solid #ddd}.active{background:#0f172a;color:#fff}.agent{background:#f8fafc;border-radius:12px;padding:10px;margin-top:8px}@media(max-width:720px){.grid{grid-template-columns:1fr 1fr}}</style></head><body><div class=wrap><div class=hero><b>Money Hunter AI v13 — สามมาร 3 สาย</b><div class=muted style="color:#cbd5e1">💰 MONEY HUNTER • 🎁 FREE STUFF HUNTER • 💻 DIGITAL HUNTER</div><div id=money class=money>฿0.00</div><div>เงินจริงที่ยืนยันแล้วเท่านั้น</div><button id=scan class="btn green full" onclick="go()">🌍 ปล่อยสามมารออกล่าทั่วโลก</button><button class="btn gray full" onclick="claimAll()">⚡ รับอัตโนมัติทุกสิทธิ์ที่ต้นทางอนุญาต</button></div><div class=grid><div class=card><div class=muted>เงินฟรี/เครดิต</div><div class=n id=moneyN>0</div></div><div class=card><div class=muted>สินค้า/ตัวอย่าง</div><div class=n id=physicalN>0</div></div><div class=card><div class=muted>ดิจิทัลฟรี</div><div class=n id=digitalN>0</div></div><div class=card><div class=muted>เกรด A พร้อมที่สุด</div><div class=n id=gradeA>0</div></div></div><div class=card><b>กติกาคัดมูลค่าฟรี</b><div class=agent>🟢 A = ฟรี + ประเทศผ่าน + เงื่อนไขง่าย</div><div class=agent>🔵 B = ฟรี แต่ต้องตรวจ region/บัญชีเล็กน้อย</div><div class=agent>🟡 C = มีภารกิจ เช่น แบบสอบถาม/ชวนเพื่อน</div><div class=agent>🔴 D = ต้องซื้อ/ใช้บัตร/สมาชิกเสียเงิน — ระบบไม่เอา</div></div><div class=card><b>รายการที่สามมารค้นพบ</b><div class=tabs><button class="btn tab active" onclick="setHunter('all',this)">ทั้งหมด</button><button class="btn tab" onclick="setHunter('money',this)">💰 เงิน</button><button class="btn tab" onclick="setHunter('physical',this)">🎁 สินค้า</button><button class="btn tab" onclick="setHunter('digital',this)">💻 ดิจิทัล</button></div><div id=deals></div></div></div><script>let hunter='all';function e(s){return String(s||'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]))}async function load(){let w=await fetch('/api/wallet').then(r=>r.json());money.textContent='฿'+Number(w.confirmed_thb||0).toLocaleString('th-TH',{minimumFractionDigits:2});let st=await fetch('/api/stats').then(r=>r.json());moneyN.textContent=st.money;physicalN.textContent=st.physical;digitalN.textContent=st.digital;gradeA.textContent=st.grade_a;let ds=await fetch('/api/deals?hunter='+hunter).then(r=>r.json());deals.innerHTML=ds.slice(0,80).map(d=>{let icon=d.hunter_type==='money'?'💰':d.hunter_type==='physical'?'🎁':'💻';let action=d.auto_claimable?`<button class="btn claim" onclick="claim(${d.id},this)">AI รับให้เลย</button>`:`<button class="btn disabled" disabled>พบแล้ว — ต้นทางยังไม่เปิด API ให้รับแทน</button>`;return `<div class=deal><span class=tag>${icon} ${e(d.hunter_type)}</span><span class="tag ${e(d.grade)}">เกรด ${e(d.grade)}</span><span class=tag>${e(d.source)}</span><div><b>${e(d.title)}</b></div><div class=muted>${e(d.platforms)} ${d.worth?'· มูลค่า '+e(d.worth):''}</div><div class=muted>${e(d.country)} · ${e(d.eligibility_reason)}</div>${action}</div>`}).join('')||'<div class=muted>ยังไม่มีรายการ กดปล่อยสามมารด้านบน</div>'}function setHunter(x,b){hunter=x;document.querySelectorAll('.tab').forEach(z=>z.classList.remove('active'));b.classList.add('active');load()}async function go(){scan.disabled=true;scan.textContent='สามมารกำลังล่า...';let r=await fetch('/api/search').then(r=>r.json());await load();scan.textContent='พบ '+r.found+' รายการ';setTimeout(()=>{scan.textContent='🌍 ปล่อยสามมารออกล่าทั่วโลก';scan.disabled=false},2200)}async function claim(id,b){b.disabled=true;b.textContent='กำลังรับ...';let r=await fetch('/api/claim/'+id,{method:'POST'}).then(r=>r.json());alert(r.message);await load()}async function claimAll(){let r=await fetch('/api/claim-all',{method:'POST'}).then(r=>r.json());alert('ลองรับ '+r.attempted+' รายการ / ส่งสำเร็จ '+r.submitted+' รายการ');await load()}load()</script></body></html>'''
@app.get('/',response_class=HTMLResponse)
def home():return HTML
