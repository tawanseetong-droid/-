from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from datetime import datetime, timezone
import os, sqlite3, httpx, re
from bs4 import BeautifulSoup

app=FastAPI(title='Money Hunter AI Cloud',version='10.1')
DB='/tmp/money_hunter.db'
SOURCES=[
 ('Freebies 4 Mom','https://freebies4mom.com/feed/'),
 ('Reddit Freebies','https://www.reddit.com/r/freebies/.rss'),
 ('GameDeals Free','https://www.reddit.com/r/GameDeals/search.rss?q=free&restrict_sr=1&sort=new')]
KEYS=['free','freebie','giveaway','coupon','voucher','cashback','rebate','grant','sample','แจกฟรี','ของฟรี','คูปอง','เงินคืน']
SCAM=['seed phrase','private key','otp','gift card payment','โอนเงินก่อน']

def db():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init():
 c=db(); c.executescript('''CREATE TABLE IF NOT EXISTS deals(id INTEGER PRIMARY KEY,title TEXT,url TEXT UNIQUE,source TEXT,status TEXT DEFAULT 'พบรายการ',created TEXT DEFAULT CURRENT_TIMESTAMP);CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY,amount REAL,currency TEXT,provider TEXT,status TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);'''); c.commit(); c.close()
init()

@app.get('/api/health')
def health(): return {'ok':True,'service':'Money Hunter AI Cloud','version':'10.1'}

@app.get('/api/v10/readiness')
def ready():
 return {'ready_for_discovery':True,'ready_for_live_receiving':False,'ready_for_auto_claim':False,'ready_for_auto_payout':False,'checks':[{'name':'แหล่งค้นหา','ok':True,'detail':'3 แหล่งเริ่มต้นพร้อมค้นหา'},{'name':'บัญชีรับเงินจริง','ok':False,'detail':'ยังไม่ได้เชื่อม PayPal / Wise / Stripe'},{'name':'Auto-Claim API','ok':False,'detail':'จะเปิดเฉพาะบริการที่อนุญาต API/OAuth'},{'name':'ถอน THB','ok':False,'detail':'ยังไม่ได้เชื่อมบัญชีปลายทาง'}],'important':'ยอดเงินจริงจะนับเฉพาะธุรกรรมที่ผู้ให้บริการยืนยันแล้ว'}

@app.post('/api/scan')
async def scan():
 added=0; checked=0
 async with httpx.AsyncClient(headers={'User-Agent':'MoneyHunterAI/10.1'},follow_redirects=True,timeout=15) as client:
  for name,url in SOURCES:
   try:
    r=await client.get(url); checked+=1
    soup=BeautifulSoup(r.text,'xml')
    for item in soup.find_all(['item','entry'])[:35]:
     title=(item.title.get_text(' ',strip=True) if item.title else '')
     low=title.lower()
     if not any(k in low for k in KEYS) or any(k in low for k in SCAM): continue
     link=''
     l=item.find('link')
     if l: link=(l.get('href') or l.get_text(strip=True) or '')
     c=db()
     try:
      c.execute('INSERT OR IGNORE INTO deals(title,url,source) VALUES(?,?,?)',(title[:300],link,name)); added+=c.total_changes; c.commit()
     finally:c.close()
   except Exception: pass
 return {'ok':True,'sources_checked':checked,'added':added}

@app.get('/api/deals')
def deals():
 c=db(); rows=[dict(x) for x in c.execute('SELECT * FROM deals ORDER BY id DESC LIMIT 100')]; c.close(); return rows

@app.get('/api/wallet')
def wallet():
 c=db(); rows=[dict(x) for x in c.execute("SELECT * FROM ledger WHERE status='confirmed' ORDER BY id DESC")]; c.close()
 return {'confirmed_transactions':rows,'total_thb_estimate':0 if not rows else None,'note':'นับเฉพาะธุรกรรม confirmed จากผู้ให้บริการ ไม่รวมมูลค่าโฆษณาของดีล'}

HTML='''<!doctype html><html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Money Hunter AI</title><style>*{box-sizing:border-box}body{margin:0;background:#f4f6fa;color:#172033;font-family:-apple-system,BlinkMacSystemFont,"Noto Sans Thai",sans-serif}.app{max-width:560px;margin:auto;min-height:100vh;background:white;padding:24px 18px 90px}.top{display:flex;justify-content:space-between;align-items:center}.logo{font-weight:850;font-size:24px}.pill{background:#eaf8ef;color:#16733a;padding:7px 11px;border-radius:99px;font-size:13px}.hero{margin-top:22px;padding:22px;border-radius:24px;background:linear-gradient(135deg,#151b2c,#283759);color:white}.hero small{opacity:.75}.money{font-size:38px;font-weight:850;margin:8px 0}.btn{border:0;border-radius:16px;padding:15px 18px;font-weight:800;font-size:16px;width:100%;cursor:pointer;background:#1d6cff;color:white}.btn:disabled{opacity:.55}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0}.card{border:1px solid #e8ebf1;border-radius:18px;padding:16px}.num{font-size:24px;font-weight:850}.flow{display:flex;gap:6px;align-items:center;font-size:12px;overflow:auto;padding:10px 0}.step{white-space:nowrap;background:#f1f4f8;padding:8px;border-radius:10px}.section{font-weight:850;font-size:19px;margin:25px 0 10px}.deal{padding:14px 0;border-bottom:1px solid #eee}.deal a{color:#172033;text-decoration:none;font-weight:700}.source{font-size:12px;color:#778095;margin-top:4px}.warn{background:#fff8df;padding:12px;border-radius:14px;font-size:13px;margin-top:14px}.bottom{position:fixed;bottom:0;left:50%;transform:translateX(-50%);width:min(560px,100%);background:#fff;border-top:1px solid #e9ebef;display:flex;justify-content:space-around;padding:12px 5px 18px;font-size:12px}.bottom b{color:#1d6cff}#status{font-size:13px;margin-top:10px;color:#667085}</style></head><body><main class="app"><div class="top"><div class="logo">Money Hunter AI</div><div class="pill">● Cloud ทำงาน</div></div><section class="hero"><small>เงินจริงที่ยืนยันแล้ว</small><div class="money">฿0.00</div><small>ไม่นับมูลค่าดีลจนกว่าจะมีธุรกรรมยืนยัน</small></section><div class="grid"><div class="card"><div class="num" id="found">0</div><div>รายการที่พบ</div></div><div class="card"><div class="num">0</div><div>จ่ายแล้ว</div></div></div><button class="btn" id="scan" onclick="scanNow()">ค้นหาให้ตอนนี้</button><div id="status">พร้อมค้นหาแหล่งสาธารณะ</div><div class="flow"><span class="step">ค้นหา</span>→<span class="step">ตรวจสิทธิ์</span>→<span class="step">ยืนยันจ่าย</span>→<span class="step">ถอน THB</span></div><div class="warn">ระบบจะไม่ข้าม CAPTCHA, ไม่สร้างบัญชีปลอม และไม่รับสิทธิ์แทนในบริการที่ไม่อนุญาต การรับอัตโนมัติจะเปิดเฉพาะ API/OAuth ที่ได้รับอนุญาตเท่านั้น</div><div class="section">โอกาสล่าสุด</div><div id="deals">ยังไม่มีรายการ กด “ค้นหาให้ตอนนี้”</div></main><nav class="bottom"><b>หน้าหลัก</b><span>ค้นหา</span><span>กำลังรับ</span><span>กระเป๋า</span><span>ตั้งค่า</span></nav><script>async function load(){let r=await fetch('/api/deals');let d=await r.json();found.textContent=d.length;deals.innerHTML=d.length?d.map(x=>`<div class="deal"><a href="${x.url||'#'}" target="_blank">${esc(x.title)}</a><div class="source">${esc(x.source)} · ${esc(x.status)}</div></div>`).join(''):'ยังไม่มีรายการ กด “ค้นหาให้ตอนนี้”'}function esc(s){return String(s||'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}async function scanNow(){let b=document.getElementById('scan');b.disabled=true;b.textContent='กำลังค้นหา…';status.textContent='กำลังตรวจแหล่งข้อมูล';try{let r=await fetch('/api/scan',{method:'POST'});let j=await r.json();status.textContent=`ตรวจ ${j.sources_checked} แหล่ง · เพิ่ม ${j.added} รายการ`;await load()}catch(e){status.textContent='ค้นหาไม่สำเร็จ ลองใหม่อีกครั้ง'}b.disabled=false;b.textContent='ค้นหาให้ตอนนี้'}load()</script></body></html>'''

@app.get('/',response_class=HTMLResponse)
def home(): return HTML
@app.get('/v10',response_class=HTMLResponse)
def v10(): return HTML
