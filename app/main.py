from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import os, sqlite3, asyncio, json
from datetime import datetime, timezone
import httpx, xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from app.paypal import router as paypal_router

BASE=os.path.dirname(os.path.dirname(__file__))
DB=os.getenv('DB_PATH',os.path.join(BASE,'money_hunter.db'))
app=FastAPI(title='Money Hunter AI Cloud',version='11.0')
app.include_router(paypal_router)

# Personal-use discovery sources. We only read public feeds/pages here.
SOURCES=[
 ('Reddit Freebies','https://www.reddit.com/r/freebies/.rss'),
 ('GameDeals Free','https://www.reddit.com/r/GameDeals/search.rss?q=free&restrict_sr=1&sort=new'),
 ('FreeGameFindings','https://www.reddit.com/r/FreeGameFindings/.rss'),
]
KEYS=['free','freebie','giveaway','coupon','voucher','cashback','rebate','grant','sample','100% off','แจกฟรี','คูปอง','เงินคืน','ของฟรี']
SCAM=['seed phrase','private key','otp','gift card payment','pay a fee to receive','advance fee','wire money','โอนเงินก่อน','รหัส otp','seed phrase']
HARD_EXCLUDE=['us only','u.s. only','usa only','united states only','uk only','canada only','australia only','in-store only']
PURCHASE_WORDS=['purchase required','with purchase','buy one','spend $','minimum spend','order required','ต้องซื้อ','ยอดซื้อ','ซื้อครบ']
CARD_WORDS=['credit card required','debit card required','card required','บัตรเครดิต','บัตรเดบิต']
SUBSCRIPTION_WORDS=['subscription required','subscribe and save','paid membership','trial converts','สมาชิกแบบเสียเงิน']
SURVEY_WORDS=['complete a survey','survey required','ทำแบบสอบถาม']
REFERRAL_WORDS=['refer a friend','referral required','invite friends','ชวนเพื่อน']
THAI_HINTS=['thailand','thai only','ประเทศไทย','กรุงเทพ','bangkok','.th']
WORLD_HINTS=['worldwide','global','international','available everywhere','ทั่วโลก']

def conn():
 c=sqlite3.connect(DB,timeout=15);c.row_factory=sqlite3.Row;return c

def col_exists(c, table, col):
 return any(r[1]==col for r in c.execute(f'PRAGMA table_info({table})'))

def init_db():
 c=conn()
 c.execute('CREATE TABLE IF NOT EXISTS deals(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT,url TEXT UNIQUE,source TEXT,kind TEXT,risk TEXT,country TEXT,created_at TEXT)')
 for name,typ,default in [
  ('eligibility','TEXT',"'review'"),('eligibility_reason','TEXT',"''"),('simple_offer','INTEGER','0'),
  ('requires_purchase','INTEGER','0'),('requires_card','INTEGER','0'),('requires_subscription','INTEGER','0'),
  ('requires_survey','INTEGER','0'),('requires_referral','INTEGER','0'),('claim_mode','TEXT',"'manual'"),('claim_status','TEXT',"'new'")]:
  if not col_exists(c,'deals',name): c.execute(f'ALTER TABLE deals ADD COLUMN {name} {typ} DEFAULT {default}')
 c.execute('CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY AUTOINCREMENT,amount REAL,currency TEXT,status TEXT,reference TEXT,created_at TEXT)')
 c.execute('CREATE TABLE IF NOT EXISTS claim_log(id INTEGER PRIMARY KEY AUTOINCREMENT,deal_id INTEGER,status TEXT,message TEXT,created_at TEXT)')
 c.commit();c.close()
init_db()

def txt(s):return ' '.join(BeautifulSoup(s or '','html.parser').get_text(' ',strip=True).split())
def kind(t):
 x=t.lower()
 if 'cashback' in x or 'rebate' in x or 'เงินคืน' in x:return 'Cashback'
 if 'coupon' in x or 'voucher' in x or 'คูปอง' in x:return 'Coupon'
 if 'grant' in x:return 'Grant'
 if 'giveaway' in x or 'แจกฟรี' in x:return 'Giveaway'
 if 'sample' in x:return 'Free Sample'
 if '100% off' in x:return '100% Off'
 return 'Freebie'
def risk(t):return 'high' if any(k in t.lower() for k in SCAM) else 'low'

def classify(text):
 x=text.lower()
 rp=any(k in x for k in PURCHASE_WORDS)
 rc=any(k in x for k in CARD_WORDS)
 rs=any(k in x for k in SUBSCRIPTION_WORDS)
 rv=any(k in x for k in SURVEY_WORDS)
 rr=any(k in x for k in REFERRAL_WORDS)
 complex_req=rp or rc or rs or rv or rr
 if any(k in x for k in HARD_EXCLUDE):
  elig='ineligible'; reason='พบเงื่อนไขจำกัดประเทศ/หน้าร้านที่ไม่เหมาะกับไทย'
 elif any(k in x for k in THAI_HINTS):
  elig='eligible'; reason='พบข้อความที่รองรับประเทศไทย'
 elif any(k in x for k in WORLD_HINTS):
  elig='eligible'; reason='ระบุว่าใช้ได้ทั่วโลก/นานาชาติ'
 else:
  elig='review'; reason='ยังไม่พบข้อความยืนยันว่าใช้ได้ในประเทศไทย'
 simple=(elig=='eligible' and not complex_req)
 return elig,reason,int(simple),int(rp),int(rc),int(rs),int(rv),int(rr)

def country_from(text,elig):
 x=text.lower()
 if any(k in x for k in THAI_HINTS):return 'Thailand'
 if any(k in x for k in WORLD_HINTS):return 'Worldwide'
 if 'us only' in x or 'u.s. only' in x or 'usa only' in x:return 'United States'
 if 'uk only' in x:return 'United Kingdom'
 return 'Check source' if elig!='ineligible' else 'Not Thailand'

async def scan_once():
 added=0
 async with httpx.AsyncClient(headers={'User-Agent':'MoneyHunterAI/11.0 personal-use'},follow_redirects=True,timeout=20) as client:
  for name,url in SOURCES:
   try:
    r=await client.get(url);r.raise_for_status();root=ET.fromstring(r.content)
    for e in root.iter():
     if e.tag.split('}')[-1].lower() not in ('item','entry'):continue
     title=summary=link=''
     for ch in list(e):
      n=ch.tag.split('}')[-1].lower()
      if n=='title' and not title:title=''.join(ch.itertext()).strip()
      elif n in ('summary','description','content') and not summary:summary=' '.join(''.join(ch.itertext()).split())
      elif n=='link' and not link:link=(ch.attrib.get('href') or (ch.text or '')).strip()
     text=txt(title+' '+summary)
     if not title or not any(k in text.lower() for k in KEYS):continue
     rk=risk(text)
     elig,reason,simple,rp,rc,rs,rv,rr=classify(text)
     ctry=country_from(text,elig)
     c=conn()
     cur=c.execute('''INSERT OR IGNORE INTO deals(title,url,source,kind,risk,country,created_at,eligibility,eligibility_reason,simple_offer,requires_purchase,requires_card,requires_subscription,requires_survey,requires_referral,claim_mode,claim_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
      (txt(title)[:300],link or url,name,kind(text),rk,ctry,datetime.now(timezone.utc).isoformat(),elig,reason,simple,rp,rc,rs,rv,rr,'manual','ready' if simple and rk!='high' else 'new'))
     added+=cur.rowcount if cur.rowcount>0 else 0
     c.commit();c.close()
   except Exception as exc:
    print('scan source error',name,type(exc).__name__)
 return added

def reclassify_existing():
 c=conn(); rows=c.execute('SELECT id,title,country FROM deals').fetchall()
 for r in rows:
  text=(r['title'] or '')+' '+(r['country'] or '')
  elig,reason,simple,rp,rc,rs,rv,rr=classify(text)
  c.execute('UPDATE deals SET eligibility=?,eligibility_reason=?,simple_offer=?,requires_purchase=?,requires_card=?,requires_subscription=?,requires_survey=?,requires_referral=?,claim_status=? WHERE id=?',
   (elig,reason,simple,rp,rc,rs,rv,rr,'ready' if simple else 'new',r['id']))
 c.commit();c.close()
reclassify_existing()

# Optional allowlisted authorized claim connectors. Nothing is auto-submitted unless the owner
# explicitly configures an API endpoint that permits automated claims.
def authorized_connectors():
 try:return json.loads(os.getenv('AUTHORIZED_CLAIM_CONNECTORS','{}'))
 except Exception:return {}

async def auto_claim_authorized():
 connectors=authorized_connectors()
 if not connectors:return 0
 c=conn(); rows=[dict(x) for x in c.execute("SELECT * FROM deals WHERE simple_offer=1 AND risk!='high' AND eligibility='eligible' AND claim_status='ready' LIMIT 25")];c.close()
 done=0
 for d in rows:
  cfg=connectors.get(d['source'])
  if not isinstance(cfg,dict) or not cfg.get('endpoint') or not cfg.get('automation_permitted'):continue
  headers={'User-Agent':'MoneyHunterAI/11.0'}
  token_env=cfg.get('token_env')
  if token_env and os.getenv(token_env):headers['Authorization']='Bearer '+os.getenv(token_env)
  try:
   async with httpx.AsyncClient(timeout=20,follow_redirects=False) as client:
    res=await client.post(cfg['endpoint'],json={'offer_url':d['url'],'offer_title':d['title']},headers=headers)
   status='submitted' if 200<=res.status_code<300 else 'failed'
   msg=f'authorized connector HTTP {res.status_code}'
  except Exception as exc:
   status='failed';msg=type(exc).__name__
  c=conn();c.execute('UPDATE deals SET claim_status=?,claim_mode=? WHERE id=?',(status,'authorized_api',d['id']));c.execute('INSERT INTO claim_log(deal_id,status,message,created_at) VALUES(?,?,?,?)',(d['id'],status,msg,datetime.now(timezone.utc).isoformat()));c.commit();c.close();done+=1
 return done

@app.on_event('startup')
async def startup():
 async def loop():
  await asyncio.sleep(3)
  while True:
   try:
    await scan_once();await auto_claim_authorized()
   except Exception as exc:print('background error',type(exc).__name__)
   await asyncio.sleep(1800)
 asyncio.create_task(loop())

@app.get('/api/search')
async def search():
 a=await scan_once(); b=await auto_claim_authorized();return {'ok':True,'added':a,'auto_claim_attempts':b}
@app.get('/api/deals')
def deals():
 c=conn();r=[dict(x) for x in c.execute("SELECT * FROM deals WHERE risk!='high' ORDER BY simple_offer DESC, CASE eligibility WHEN 'eligible' THEN 0 WHEN 'review' THEN 1 ELSE 2 END, id DESC LIMIT 250")];c.close();return r
@app.get('/api/deals/simple')
def simple_deals():
 c=conn();r=[dict(x) for x in c.execute("SELECT * FROM deals WHERE risk!='high' AND simple_offer=1 AND eligibility='eligible' ORDER BY id DESC LIMIT 100")];c.close();return r
@app.get('/api/stats')
def stats():
 c=conn();
 total=c.execute("SELECT COUNT(*) FROM deals WHERE risk!='high'").fetchone()[0]
 eligible=c.execute("SELECT COUNT(*) FROM deals WHERE risk!='high' AND eligibility='eligible'").fetchone()[0]
 simple=c.execute("SELECT COUNT(*) FROM deals WHERE risk!='high' AND simple_offer=1 AND eligibility='eligible'").fetchone()[0]
 review=c.execute("SELECT COUNT(*) FROM deals WHERE risk!='high' AND eligibility='review'").fetchone()[0]
 submitted=c.execute("SELECT COUNT(*) FROM deals WHERE claim_status='submitted'").fetchone()[0]
 c.close();return {'total':total,'eligible':eligible,'simple':simple,'review':review,'submitted':submitted}
@app.get('/api/wallet')
def wallet():
 c=conn();rows=[dict(x) for x in c.execute("SELECT * FROM ledger WHERE status='confirmed' ORDER BY id DESC")];c.close();total=sum(float(x['amount']) for x in rows if x['currency'].upper()=='THB');return {'confirmed_thb':round(total,2),'transactions':rows,'note':'นับเฉพาะธุรกรรมที่ผู้ให้บริการยืนยันแล้ว'}
@app.get('/api/readiness')
def readiness():
 configured=bool(os.getenv('PAYPAL_CLIENT_ID') and os.getenv('PAYPAL_CLIENT_SECRET'))
 connectors=authorized_connectors()
 return {'discovery':True,'auto_scan_minutes':30,'simple_filter':True,'country_filter':'Thailand/Worldwide','authorized_auto_claim_sources':len(connectors),'live_money_actions':os.getenv('ALLOW_LIVE_MONEY_ACTIONS','false').lower()=='true','live_payouts':os.getenv('ALLOW_LIVE_PAYOUTS','false').lower()=='true','provider_connected':configured,'paypal_mode':os.getenv('PAYPAL_MODE','sandbox'),'message':'โหมดส่วนตัว: ค้นหาและคัดกรองอัตโนมัติทุก 30 นาที'}
@app.get('/health')
def health():return {'ok':True,'service':'money-hunter-ai','version':'11.0'}

HTML='''<!doctype html><html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Money Hunter AI</title><style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f6f7fb;margin:0;color:#18202a}.wrap{max-width:920px;margin:auto;padding:20px}.hero{background:#111827;color:#fff;border-radius:24px;padding:24px}.money{font-size:42px;font-weight:800;margin:8px 0}.btn{border:0;border-radius:14px;padding:14px 18px;font-size:16px;font-weight:700;cursor:pointer}.primary{background:#22c55e;color:#06240f;width:100%;margin-top:14px}.secondary{background:#e5e7eb;color:#111827;width:100%;margin-top:10px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:14px}.card{background:#fff;border-radius:18px;padding:16px;box-shadow:0 4px 18px #0000000d}.n{font-size:28px;font-weight:800}.deal{margin-top:10px;border-top:1px solid #eee;padding-top:12px}.tag{display:inline-block;background:#eef2ff;padding:5px 9px;border-radius:999px;font-size:12px;margin-right:4px}.ok{background:#dcfce7}.warn{background:#fef3c7}.bad{background:#fee2e2}.muted{color:#6b7280;font-size:13px}a{color:#2563eb;text-decoration:none}.note{background:#ecfdf5;border-radius:14px;padding:12px;margin-top:12px}@media(max-width:700px){.grid{grid-template-columns:1fr 1fr}}</style></head><body><div class=wrap><div class=hero><div><b>Money Hunter AI — โหมดส่วนตัว</b></div><div class=muted style="color:#cbd5e1">ค้นหาอัตโนมัติทุก 30 นาที • ตัดข้อเสนอที่ต้องซื้อ/บัตร/สมาชิก/แบบสอบถาม/ชวนเพื่อน</div><div class=money id=money>฿0.00</div><div>เงินจริงที่ยืนยันแล้ว</div><button id=scanBtn class="btn primary" onclick="scan()">ค้นหาและคัดกรองตอนนี้</button><button id=paypalBtn class="btn secondary" onclick="testPaypal()">ทดสอบ PayPal Sandbox</button></div><div class=grid><div class=card><div class=muted>พบทั้งหมด</div><div class=n id=total>0</div></div><div class=card><div class=muted>เข้าเงื่อนไขไทย/ทั่วโลก</div><div class=n id=eligible>0</div></div><div class=card><div class=muted>ของฟรีแบบง่าย</div><div class=n id=simple>0</div></div><div class=card><div class=muted>ต้องตรวจเพิ่ม</div><div class=n id=review>0</div></div></div><div class=card style="margin-top:14px"><b>สถานะระบบ</b><div id=ready class=muted>กำลังตรวจ...</div><div id=paypal class=muted style="margin-top:5px"></div><div class=note>ระบบจะรับแทนให้อัตโนมัติเฉพาะแหล่งที่มี API/สิทธิ์อนุญาต automation เท่านั้น หากเว็บต้อง CAPTCHA, OTP, ล็อกอิน หรือห้ามบอท จะเปิดต้นทางให้คุณทำเอง</div></div><div class=card style="margin-top:14px"><b>โอกาสล่าสุดที่คัดกรองแล้ว</b><div id=deals></div></div></div><script>function esc(s){return String(s||'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]))}async function load(){let w=await fetch('/api/wallet').then(r=>r.json());money.textContent='฿'+Number(w.confirmed_thb||0).toLocaleString('th-TH',{minimumFractionDigits:2,maximumFractionDigits:2});let rs=await fetch('/api/readiness').then(r=>r.json());ready.textContent=rs.message+' • แหล่งรับอัตโนมัติที่อนุญาต: '+rs.authorized_auto_claim_sources;let ps=await fetch('/api/paypal/status').then(r=>r.json());paypal.textContent=ps.configured?'PayPal: เชื่อมไว้แล้ว ('+ps.mode+')':'PayPal: ยังตั้งค่าไม่ครบ';let st=await fetch('/api/stats').then(r=>r.json());total.textContent=st.total;eligible.textContent=st.eligible;simple.textContent=st.simple;review.textContent=st.review;let ds=await fetch('/api/deals').then(r=>r.json());deals.innerHTML=ds.slice(0,40).map(d=>{let ec=d.eligibility==='eligible'?'ok':d.eligibility==='review'?'warn':'bad';let flags=[];if(d.requires_purchase)flags.push('ต้องซื้อ');if(d.requires_card)flags.push('ต้องใช้บัตร');if(d.requires_subscription)flags.push('สมาชิก');if(d.requires_survey)flags.push('แบบสอบถาม');if(d.requires_referral)flags.push('ชวนเพื่อน');return `<div class=deal><span class=tag>${esc(d.kind)}</span><span class="tag ${ec}">${esc(d.eligibility)}</span>${d.simple_offer?'<span class="tag ok">ของฟรีแบบง่าย</span>':''}<div><b>${esc(d.title)}</b></div><div class=muted>${esc(d.source)} · ${esc(d.country)} · ${esc(d.eligibility_reason)}</div>${flags.length?`<div class=muted>เงื่อนไขที่พบ: ${esc(flags.join(', '))}</div>`:''}<div class=muted>สถานะรับสิทธิ์: ${esc(d.claim_status)} ${d.claim_mode==='authorized_api'?'(API ที่ได้รับอนุญาต)':''}</div><a href="${esc(d.url)}" target=_blank rel="noopener noreferrer">เปิดแหล่งต้นทาง</a></div>`}).join('')||'<div class=muted style="margin-top:10px">ยังไม่มีรายการ</div>'}async function scan(){scanBtn.disabled=true;scanBtn.textContent='กำลังค้นหาและตรวจเงื่อนไข...';await fetch('/api/search');await load();scanBtn.disabled=false;scanBtn.textContent='ค้นหาและคัดกรองตอนนี้'}async function testPaypal(){paypalBtn.disabled=true;paypalBtn.textContent='กำลังทดสอบ...';let r=await fetch('/api/paypal/test').then(r=>r.json());paypal.textContent=r.message+(r.ok?' ✅':' ❌');paypalBtn.disabled=false;paypalBtn.textContent='ทดสอบ PayPal Sandbox'}load()</script></body></html>'''
@app.get('/',response_class=HTMLResponse)
def home():return HTML
