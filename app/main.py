from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import os, sqlite3, asyncio
from datetime import datetime, timezone
import httpx, xml.etree.ElementTree as ET
from bs4 import BeautifulSoup

BASE=os.path.dirname(os.path.dirname(__file__))
DB=os.getenv('DB_PATH',os.path.join(BASE,'money_hunter.db'))
app=FastAPI(title='Money Hunter AI Cloud',version='10.1')
SOURCES=[('Reddit Freebies','https://www.reddit.com/r/freebies/.rss'),('GameDeals Free','https://www.reddit.com/r/GameDeals/search.rss?q=free&restrict_sr=1&sort=new')]
KEYS=['free','freebie','giveaway','coupon','voucher','cashback','rebate','grant','sample','แจกฟรี','คูปอง','เงินคืน']
SCAM=['seed phrase','private key','otp','gift card payment','pay a fee to receive','โอนเงินก่อน','รหัส otp']

def conn():
 c=sqlite3.connect(DB,timeout=15);c.row_factory=sqlite3.Row;return c

def init_db():
 c=conn();c.execute('CREATE TABLE IF NOT EXISTS deals(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT,url TEXT UNIQUE,source TEXT,kind TEXT,risk TEXT,country TEXT,created_at TEXT)');c.execute('CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY AUTOINCREMENT,amount REAL,currency TEXT,status TEXT,reference TEXT,created_at TEXT)');c.commit();c.close()
init_db()

def txt(s):return ' '.join(BeautifulSoup(s or '','html.parser').get_text(' ',strip=True).split())
def kind(t):
 x=t.lower()
 if 'cashback' in x or 'rebate' in x or 'เงินคืน' in x:return 'Cashback'
 if 'coupon' in x or 'voucher' in x or 'คูปอง' in x:return 'Coupon'
 if 'grant' in x:return 'Grant'
 if 'giveaway' in x or 'แจกฟรี' in x:return 'Giveaway'
 if 'sample' in x:return 'Free Sample'
 return 'Freebie'
def risk(t):return 'high' if any(k in t.lower() for k in SCAM) else 'low'
def country(t):
 x=t.lower()
 if 'us only' in x:return 'United States'
 if 'uk only' in x:return 'United Kingdom'
 if 'thailand only' in x or 'ประเทศไทยเท่านั้น' in x:return 'Thailand'
 return 'Worldwide/Check source'

async def scan_once():
 added=0
 async with httpx.AsyncClient(headers={'User-Agent':'MoneyHunterAI/10.1'},follow_redirects=True,timeout=20) as client:
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
     c=conn();c.execute('INSERT OR IGNORE INTO deals(title,url,source,kind,risk,country,created_at) VALUES(?,?,?,?,?,?,?)',(txt(title)[:300],link or url,name,kind(text),risk(text),country(text),datetime.now(timezone.utc).isoformat()));added+=c.total_changes;c.commit();c.close()
   except Exception:pass
 return added

@app.on_event('startup')
async def startup():
 async def loop():
  await asyncio.sleep(3)
  while True:
   try:await scan_once()
   except Exception:pass
   await asyncio.sleep(3600)
 asyncio.create_task(loop())

@app.get('/api/search')
async def search():return {'ok':True,'added':await scan_once()}
@app.get('/api/deals')
def deals():
 c=conn();r=[dict(x) for x in c.execute("SELECT * FROM deals WHERE risk!='high' ORDER BY id DESC LIMIT 200")];c.close();return r
@app.get('/api/wallet')
def wallet():
 c=conn();rows=[dict(x) for x in c.execute("SELECT * FROM ledger WHERE status='confirmed' ORDER BY id DESC")];c.close();total=sum(float(x['amount']) for x in rows if x['currency'].upper()=='THB');return {'confirmed_thb':round(total,2),'transactions':rows,'note':'นับเฉพาะธุรกรรมที่ยืนยันแล้ว'}
@app.get('/api/readiness')
def readiness():return {'discovery':True,'live_money_actions':False,'live_payouts':False,'provider_connected':False,'message':'ค้นหาได้แล้ว; การรับ/ถอนเงินจริงจะเปิดหลังเชื่อม provider ที่ได้รับอนุญาต'}
@app.get('/health')
def health():return {'ok':True,'service':'money-hunter-ai'}

HTML='''<!doctype html><html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Money Hunter AI</title><style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f6f7fb;margin:0;color:#18202a}.wrap{max-width:760px;margin:auto;padding:20px}.hero{background:#111827;color:#fff;border-radius:24px;padding:24px}.money{font-size:42px;font-weight:800;margin:8px 0}.btn{border:0;border-radius:14px;padding:14px 18px;font-size:16px;font-weight:700;cursor:pointer}.primary{background:#22c55e;color:#06240f;width:100%;margin-top:14px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}.card{background:#fff;border-radius:18px;padding:16px;box-shadow:0 4px 18px #0000000d}.deal{margin-top:10px;border-top:1px solid #eee;padding-top:12px}.tag{display:inline-block;background:#eef2ff;padding:5px 9px;border-radius:999px;font-size:12px}.muted{color:#6b7280;font-size:13px}a{color:#2563eb;text-decoration:none}</style></head><body><div class=wrap><div class=hero><div>Money Hunter AI</div><div class=muted style="color:#cbd5e1">ค้นหา → ตรวจสิทธิ์ → ยืนยันจ่าย → ถอน THB</div><div class=money id=money>฿0.00</div><div>เงินจริงที่ยืนยันแล้ว</div><button id=scanBtn class="btn primary" onclick="scan()">ค้นหาให้ตอนนี้</button></div><div class=grid><div class=card><b>สถานะระบบ</b><div id=ready class=muted>กำลังตรวจ...</div></div><div class=card><b>รายการที่พบ</b><div id=count style="font-size:30px;font-weight:800">0</div></div></div><div class=card style="margin-top:14px"><b>โอกาสล่าสุด</b><div id=deals></div></div></div><script>async function load(){let w=await fetch('/api/wallet').then(r=>r.json());money.textContent='฿'+Number(w.confirmed_thb||0).toLocaleString('th-TH',{minimumFractionDigits:2,maximumFractionDigits:2});let rs=await fetch('/api/readiness').then(r=>r.json());ready.textContent=rs.message;let ds=await fetch('/api/deals').then(r=>r.json());count.textContent=ds.length;deals.innerHTML=ds.slice(0,20).map(d=>`<div class=deal><span class=tag>${d.kind}</span> <span class=tag>${d.country}</span><div><b>${d.title}</b></div><div class=muted>${d.source} · ความเสี่ยง ${d.risk}</div><a href="${d.url}" target=_blank rel=noopener>เปิดแหล่งต้นทาง</a></div>`).join('')||'<div class=muted style="margin-top:10px">ยังไม่มีรายการ กด “ค้นหาให้ตอนนี้”</div>'}async function scan(){scanBtn.disabled=true;scanBtn.textContent='กำลังค้นหา...';await fetch('/api/search');await load();scanBtn.disabled=false;scanBtn.textContent='ค้นหาให้ตอนนี้'}load()</script></body></html>'''
@app.get('/',response_class=HTMLResponse)
def home():return HTML
