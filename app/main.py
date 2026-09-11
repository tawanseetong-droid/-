from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
import os, sqlite3, asyncio, json, re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
import httpx, xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from app.paypal import router as paypal_router

BASE=os.path.dirname(os.path.dirname(__file__))
DB=os.getenv('DB_PATH',os.path.join(BASE,'money_hunter.db'))
TARGET_COUNTRY=os.getenv('TARGET_COUNTRY','Thailand')
AUTO_CLAIM_ENABLED=os.getenv('AUTO_CLAIM_ENABLED','true').lower()=='true'
app=FastAPI(title='Money Hunter AI Claimability Engine',version='15.0')
app.include_router(paypal_router)

SCAM=['seed phrase','private key','gift card payment','pay a fee to receive','advance fee','wire money','โอนเงินก่อน','crypto deposit','wallet seed']
PURCHASE=['purchase required','with purchase','buy one','minimum spend','order required','ต้องซื้อ','ยอดซื้อ','ซื้อครบ','spend $','spend £','spend €']
EXCHANGE_PURCHASE=['add-on deal','exchange purchase','redeem with purchase','แลกซื้อ','ซื้อเพิ่ม','ซื้อคู่','ซื้อสินค้าเพื่อรับ']
CARD=['credit card required','debit card required','card required','บัตรเครดิต','บัตรเดบิต']
SUBS=['subscription required','paid membership','trial converts','auto-renew','สมาชิกแบบเสียเงิน']
SURVEY=['complete a survey','survey required','ทำแบบสอบถาม']
REFERRAL=['refer a friend','referral required','invite friends','ชวนเพื่อน']
GAME=['play game','mini game','spin to win','lucky draw','เกม','เล่นเกม','หมุนวงล้อ','สุ่มรางวัล','จับรางวัล']
CHECKIN=['check in','daily check-in','เช็กอิน','เช็คอิน','เก็บเหรียญ','รับเหรียญรายวัน']
COUPON=['coupon','voucher','promo code','discount code','คูปอง','โค้ดส่วนลด','ส่วนลด','ลดทันที']
CASHBACK=['cashback','cash back','rebate','coins back','เงินคืน','เครดิตเงินคืน','คืนเหรียญ']
SAMPLE=['free sample','product testing','tester','trial size','สินค้าทดลอง','ตัวอย่างสินค้า','ทดลองใช้']
FREE=['free gift','giveaway','freebie','100% free','รับฟรี','แจกฟรี','ของฟรี','ของแถมฟรี']
FLASH=['flash sale','limited-time deal','daily deal','ดีลประจำวัน','แฟลชเซล','ลดจำกัดเวลา']
WORLD=['worldwide','global','international','region free','region-free','available in most countries','ทั่วโลก']
THAI=['thailand','ประเทศไทย','bangkok','กรุงเทพ','.th']
REGIONS={'United States':['us only','u.s. only','usa only','united states only','u.s. residents'],'United Kingdom':['uk only','united kingdom only','uk residents'],'Canada':['canada only','canadian residents'],'Australia':['australia only','australian residents'],'India':['india only','indian residents']}
MONEY=['cash','cashback','rebate','paypal','reward','credit','grant','เงินคืน','เครดิตฟรี','รางวัลเงินสด']
PHYSICAL=['sample','product testing','tester','beauty','skincare','cosmetic','food','drink','pet','household','perfume','demo product','free product','สินค้าทดลอง','ตัวอย่างสินค้า','ของฟรี']
RSS_SOURCES=[('Reddit Freebies','https://www.reddit.com/r/freebies/.rss')]
VERIFY_HOST_SUFFIXES=('gamerpower.com','epicgames.com','steampowered.com','steamcommunity.com')
MARKETPLACE_SOURCES=[
 {'name':'TikTok Shop','url':'https://shop.tiktok.com/th','hosts':('shop.tiktok.com','tiktok.com')},
 {'name':'Shopee','url':'https://shopee.co.th/','hosts':('shopee.co.th',)},
 {'name':'Lazada','url':'https://www.lazada.co.th/','hosts':('lazada.co.th',)},
]
MARKETPLACE_ENTRY_POINTS=[
 {'platform':'TikTok Shop','title':'ศูนย์คูปองและโปรโมชัน TikTok Shop','url':'https://shop.tiktok.com/th','kind':'coupon','label':'คูปอง/ส่วนลด','note':'เปิด TikTok Shop แล้วเลือกคูปองที่บัญชีของคุณมีสิทธิ์รับ'},
 {'platform':'Shopee','title':'คูปอง โค้ดส่งฟรี และโปรโมชัน Shopee','url':'https://shopee.co.th/','kind':'coupon','label':'คูปอง/ส่งฟรี','note':'เปิดหน้าแรก Shopee แล้วเลือกเมนูโค้ดส่วนลดหรือส่งฟรีที่แสดงในบัญชีของคุณ'},
 {'platform':'Lazada','title':'คูปองและโปรโมชัน Lazada','url':'https://www.lazada.co.th/','kind':'coupon','label':'คูปอง/ส่วนลด','note':'เปิด Lazada แล้วเข้าหมวดคูปองเพื่อกดเก็บในบัญชี'},
]


def conn():
 c=sqlite3.connect(DB,timeout=20);c.row_factory=sqlite3.Row;return c

def has_col(c,t,n):return any(r[1]==n for r in c.execute(f'PRAGMA table_info({t})'))

def init_db():
 c=conn();c.execute('CREATE TABLE IF NOT EXISTS deals(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT,url TEXT UNIQUE,source TEXT,kind TEXT,risk TEXT,country TEXT,created_at TEXT)')
 cols=[('eligibility','TEXT',"'review'"),('eligibility_reason','TEXT',"''"),('simple_offer','INTEGER','0'),('requires_purchase','INTEGER','0'),('requires_card','INTEGER','0'),('requires_subscription','INTEGER','0'),('requires_survey','INTEGER','0'),('requires_referral','INTEGER','0'),('requires_game','INTEGER','0'),('requires_checkin','INTEGER','0'),('offer_mechanic','TEXT',"'other'"),('offer_mechanic_label','TEXT',"'ข้อเสนออื่น'"),('platform','TEXT',"''"),('claim_mode','TEXT',"'manual'"),('claim_status','TEXT',"'new'"),('description','TEXT',"''"),('instructions','TEXT',"''"),('end_date','TEXT',"''"),('worth','TEXT',"''"),('platforms','TEXT',"''"),('hunter_type','TEXT',"'digital'"),('grade','TEXT',"'C'"),('direct_claim_url','TEXT',"''"),('source_trust','TEXT',"'public'"),('manual_reason','TEXT',"''"),('claimability','TEXT',"'manual'"),('claim_reason','TEXT',"''"),('url_live','INTEGER','0'),('last_verified_at','TEXT',"''"),('discount_amount','REAL','0'),('discount_percent','REAL','0'),('min_spend','REAL','0'),('sort_score','REAL','0')]
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

def offer_mechanic(text):
 x=text.lower()
 if any(k in x for k in GAME):return 'game','เล่นเกม/ลุ้นรางวัล'
 if any(k in x for k in CHECKIN):return 'checkin','เช็กอิน/เก็บเหรียญ'
 if any(k in x for k in REFERRAL):return 'referral','เชิญเพื่อน'
 if any(k in x for k in EXCHANGE_PURCHASE):return 'exchange_purchase','แลกซื้อ/ซื้อเพิ่ม'
 if any(k in x for k in PURCHASE):return 'purchase_required','ต้องซื้อก่อน'
 if any(k in x for k in SAMPLE):return 'sample','ทดลองสินค้า'
 if any(k in x for k in CASHBACK):return 'cashback','เงินคืน'
 if any(k in x for k in COUPON):return 'coupon','คูปอง/ส่วนลด'
 if any(k in x for k in FREE):return 'free','แจกฟรี'
 if any(k in x for k in FLASH):return 'flash_sale','Flash Sale'
 return 'other','ข้อเสนออื่น'

def classify(text,source=''):
 x=text.lower();rp=any(k in x for k in PURCHASE);rc=any(k in x for k in CARD);rs=any(k in x for k in SUBS);rv=any(k in x for k in SURVEY);rr=any(k in x for k in REFERRAL)
 region=detect_region(x)
 if any(k in x for k in THAI):elig='eligible';reason='รองรับประเทศไทย';country='Thailand'
 elif any(k in x for k in WORLD):elig='eligible';reason='รองรับหลายประเทศ/ทั่วโลก';country='Worldwide'
 elif region:elig='regional';reason='จำกัดภูมิภาค: '+region;country=region
 elif source in ('GamerPower','Epic Games','Steam'):elig='review';reason='พบจากแหล่งสาธารณะ ต้องยืนยันภูมิภาคก่อนรับ';country='Global candidate'
 else:elig='review';reason='ยังไม่พบข้อความยืนยันประเทศ';country='Unknown'
 simple=int(not (rp or rc or rs or rv or rr) and elig in ('eligible','review'))
 if rp or rc or rs:grade='D'
 elif rv or rr:grade='C'
 elif elig=='eligible':grade='A'
 elif elig=='review':grade='B'
 else:grade='D'
 return elig,reason,country,simple,int(rp),int(rc),int(rs),int(rv),int(rr),grade

def risk(text):return 'high' if any(k in text.lower() for k in SCAM) else 'low'

def health(source,status,message,found=0):
 c=conn();c.execute('INSERT INTO source_health(source,status,message,checked_at,found) VALUES(?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET status=excluded.status,message=excluded.message,checked_at=excluded.checked_at,found=excluded.found',(source,status,message,now(),found));c.commit();c.close()

def authorized_connectors():
 try:return json.loads(os.getenv('AUTHORIZED_CLAIM_CONNECTORS','{}'))
 except Exception:return {}

def claimability_for(source,elig,simple,rk,url):
 cfg=authorized_connectors().get(source)
 if rk=='high':return 'blocked','เสี่ยงสูง ระบบไม่รับ'
 if elig=='regional':return 'blocked','ประเทศเป้าหมายไม่ตรงเงื่อนไข'
 if isinstance(cfg,dict) and cfg.get('endpoint') and cfg.get('automation_permitted') and elig=='eligible' and simple:
  return 'auto_api','ต้นทางมี API และอนุญาต automation'
 if source in ('Epic Games','Steam'):
  return 'login','ไปหน้ารับตรงได้ แต่ต้องยืนยันบัญชีของคุณกับร้านค้า'
 if source=='GamerPower':
  return 'direct','มีหน้ารับตรง แต่ GamerPower เป็นตัวรวมรายการ ไม่ใช่ API สำหรับ claim'
 if url:return 'manual','พบรายการ แต่ต้องตรวจเงื่อนไขต้นทางก่อน'
 return 'blocked','ไม่มีหน้ารับที่ตรวจได้'

def discount_values(text,mechanic):
 x=clean(text).replace(',','')
 def largest(patterns):
  values=[]
  for pattern in patterns:
   values.extend(float(v) for v in re.findall(pattern,x,re.I))
  return max(values,default=0)
 percent=largest([r'(\d+(?:\.\d+)?)\s*%',r'ลด\s*(\d+(?:\.\d+)?)\s*เปอร์เซ็นต์'])
 amount=largest([r'(?:ลด|คืน|รับ)\s*(?:สูงสุด\s*)?(\d+(?:\.\d+)?)\s*(?:บาท|฿)',r'(\d+(?:\.\d+)?)\s*(?:บาท|฿)\s*(?:ส่วนลด|คืน)'])
 minimum=largest([r'(?:ขั้นต่ำ|ครบ|เมื่อซื้อ)\s*(\d+(?:\.\d+)?)\s*(?:บาท|฿)'])
 if mechanic=='free':score=1_000_000_000
 elif amount:score=100_000_000+amount
 elif percent:score=10_000_000+percent
 elif 'ส่งฟรี' in x:score=1_000_000
 else:score=0
 return amount,percent,minimum,score

def save_deal(title,url,source,kind,text,description='',instructions='',end_date='',worth='',platforms='',trust='public'):
 if not title or not url:return 0
 elig,reason,country,simple,rp,rc,rs,rv,rr,grade=classify(text,source);rk=risk(text);ht=hunter_type(text)
 mechanic,mechanic_label=offer_mechanic(text)
 amount,percent,minimum,score=discount_values(text,mechanic)
 rg=int(mechanic=='game');rcheck=int(mechanic=='checkin')
 cl,cr=claimability_for(source,elig,simple,rk,url)
 c=conn();cur=c.execute('''INSERT INTO deals(title,url,source,kind,risk,country,created_at,eligibility,eligibility_reason,simple_offer,requires_purchase,requires_card,requires_subscription,requires_survey,requires_referral,claim_mode,claim_status,description,instructions,end_date,worth,platforms,hunter_type,grade,direct_claim_url,source_trust,manual_reason,claimability,claim_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET title=excluded.title,kind=excluded.kind,risk=excluded.risk,country=excluded.country,eligibility=excluded.eligibility,eligibility_reason=excluded.eligibility_reason,simple_offer=excluded.simple_offer,requires_purchase=excluded.requires_purchase,requires_card=excluded.requires_card,requires_subscription=excluded.requires_subscription,requires_survey=excluded.requires_survey,requires_referral=excluded.requires_referral,description=excluded.description,instructions=excluded.instructions,end_date=excluded.end_date,worth=excluded.worth,platforms=excluded.platforms,hunter_type=excluded.hunter_type,grade=excluded.grade,direct_claim_url=excluded.direct_claim_url,source_trust=excluded.source_trust,claimability=excluded.claimability,claim_reason=excluded.claim_reason''',(clean(title)[:300],url,source,kind,rk,country,now(),elig,reason,simple,rp,rc,rs,rv,rr,cl,'ready' if cl in ('auto_api','direct','login') else 'new',clean(description)[:2000],clean(instructions)[:2000],clean(end_date),clean(worth),clean(platforms),ht,grade,url,trust,cr,cl,cr))
 c.execute('UPDATE deals SET requires_game=?,requires_checkin=?,offer_mechanic=?,offer_mechanic_label=?,platform=?,discount_amount=?,discount_percent=?,min_spend=?,sort_score=?,created_at=? WHERE url=?',(rg,rcheck,mechanic,mechanic_label,source,amount,percent,minimum,score,now(),url))
 c.commit();c.close();return max(cur.rowcount,0)

async def scan_gamerpower(client):
 try:
  r=await client.get('https://www.gamerpower.com/api/giveaways?sort-by=date');r.raise_for_status();data=r.json();data=data if isinstance(data,list) else [];added=0
  for g in data[:300]:
   title=clean(g.get('title'));desc=clean(g.get('description'));inst=clean(g.get('instructions'));url=g.get('open_giveaway_url') or g.get('gamerpower_url') or ''
   text=' '.join([title,desc,inst,clean(g.get('platforms')),clean(g.get('type')),'global digital giveaway'])
   added+=save_deal(title,url,'GamerPower',clean(g.get('type') or 'Giveaway'),text,desc,inst,g.get('end_date',''),g.get('worth',''),g.get('platforms',''),'aggregator')
  health('GamerPower','ok','API ค้นหารายการใช้งานได้',len(data));return added,len(data)
 except Exception as e:health('GamerPower','error',type(e).__name__,0);return 0,0

async def scan_epic(client):
 try:
  u='https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=en-US&country=TH&allowCountries=TH';r=await client.get(u);r.raise_for_status();data=r.json();els=((data.get('data') or {}).get('Catalog') or {}).get('searchStore',{}).get('elements',[]);added=found=0
  for g in els:
   promos=(g.get('promotions') or {}).get('promotionalOffers') or [];free=False;end=''
   for p in promos:
    for o in p.get('promotionalOffers',[]):
     if ((o.get('discountSetting') or {}).get('discountPercentage')==0):free=True;end=o.get('endDate','')
   if not free:continue
   title=clean(g.get('title'));slug=g.get('productSlug') or g.get('urlSlug') or '';claim='https://store.epicgames.com/en-US/p/'+slug if slug else 'https://store.epicgames.com/en-US/free-games'
   added+=save_deal(title,claim,'Epic Games','Free Game',title+' free game epic Thailand digital',end_date=end,platforms='Epic Games Store',trust='official-public-endpoint');found+=1
  health('Epic Games','ok','ตรวจโปรโมชั่นฟรีสำหรับประเทศไทยได้',found);return added,found
 except Exception as e:health('Epic Games','error',type(e).__name__,0);return 0,0

async def scan_steam(client):
 try:
  u='https://store.steampowered.com/search/results/?maxprice=free&specials=1&cc=TH&l=english&json=1';r=await client.get(u);r.raise_for_status();data=r.json();s=BeautifulSoup(data.get('results_html',''),'html.parser');added=found=0
  for a in s.select('a.search_result_row')[:100]:
   t=a.select_one('.title');title=clean(t.get_text() if t else '');href=a.get('href','').split('?')[0]
   if not title or not href:continue
   added+=save_deal(title,href,'Steam','Free Special',title+' Steam free special Thailand digital',platforms='Steam',trust='public-store-endpoint');found+=1
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

async def scan_marketplace(client,source):
 name=source['name'];hub=source['url']
 c=conn();c.execute("DELETE FROM deals WHERE source=? AND source_trust='official-public-page'",(name,));c.commit();c.close()
 try:
  r=await client.get(hub);r.raise_for_status();soup=BeautifulSoup(r.text,'html.parser');added=found=0;seen=set()
  keywords=FREE+SAMPLE+CASHBACK+COUPON+FLASH+GAME+CHECKIN+REFERRAL+EXCHANGE_PURCHASE+PURCHASE
  for a in soup.select('a[href]'):
   title=clean(a.get_text(' ',strip=True) or a.get('title') or a.get('aria-label'))
   href=urljoin(hub,a.get('href',''));host=(urlparse(href).hostname or '').lower()
   if not title or len(title)<4 or href in seen:continue
   if not any(host==h or host.endswith('.'+h) for h in source['hosts']):continue
   context=clean(title+' '+(a.get('title') or '')+' '+(a.get('aria-label') or ''))
   if not any(k in context.lower() for k in keywords):continue
   seen.add(href);mechanic,label=offer_mechanic(context)
   added+=save_deal(title,href,name,label,context+' Thailand',description=context,platforms=name,trust='official-public-page');found+=1
   if found>=80:break
  status='ok' if found else 'manual'
  message='พบข้อเสนอจากหน้าสาธารณะ' if found else 'หน้าเว็บไม่เปิดรายการให้อ่านอัตโนมัติ ต้องเปิดแอปตรวจ'
  health(name,status,message,found);return added,found
 except Exception as e:
  health(name,'manual','อ่านหน้าอัตโนมัติไม่ได้ ต้องเปิดแอปตรวจ ('+type(e).__name__+')',0);return 0,0

async def refresh_marketplace_entry_points(client):
 added=live=0
 active_urls=[item['url'] for item in MARKETPLACE_ENTRY_POINTS]
 c=conn();marks=','.join('?' for _ in active_urls);c.execute(f"DELETE FROM deals WHERE source_trust='official-entry-point' AND url NOT IN ({marks})",active_urls);c.commit();c.close()
 for item in MARKETPLACE_ENTRY_POINTS:
  url=item['url'];is_live=0
  try:
   r=await client.get(url,follow_redirects=True)
   host=(urlparse(str(r.url)).hostname or '').lower()
   allowed=next(s['hosts'] for s in MARKETPLACE_SOURCES if s['name']==item['platform'])
   is_live=int(r.status_code<400 and any(host==h or host.endswith('.'+h) for h in allowed))
  except Exception:is_live=0
  added+=save_deal(item['title'],url,item['platform'],item['label'],item['title']+' '+item['note']+' Thailand',description=item['note'],instructions='กดปุ่มเปิดหน้ารับสิทธิ์ แล้วกดเก็บในบัญชีของคุณ',platforms=item['platform'],trust='official-entry-point')
  c=conn();c.execute("UPDATE deals SET offer_mechanic=?,offer_mechanic_label=?,claimability='login',claim_reason=?,manual_reason=?,url_live=?,last_verified_at=? WHERE url=?",(item['kind'],item['label'],'ต้องเปิดแพลตฟอร์มและกดเก็บด้วยบัญชีของคุณ',item['note'],is_live,now(),url));c.commit();c.close();live+=is_live
 return added,live

async def verify_official_links(client):
 c=conn();rows=[dict(x) for x in c.execute("SELECT id,direct_claim_url,url FROM deals WHERE source IN ('Epic Games','Steam','GamerPower') AND risk!='high' ORDER BY id DESC LIMIT 120")];c.close();ok=0
 for d in rows:
  u=d.get('direct_claim_url') or d.get('url') or ''
  try:
   h=(urlparse(u).hostname or '').lower()
   if not any(h==s or h.endswith('.'+s) for s in VERIFY_HOST_SUFFIXES):continue
   r=await client.get(u,follow_redirects=True);live=1 if r.status_code<400 else 0
  except Exception:live=0
  c=conn();c.execute('UPDATE deals SET url_live=?,last_verified_at=? WHERE id=?',(live,now(),d['id']));c.commit();c.close();ok+=live
 return ok

async def scan_once():
 async with httpx.AsyncClient(headers={'User-Agent':'MoneyHunterAI/15.0 personal-use'},follow_redirects=True,timeout=20,trust_env=False) as client:
  results=await asyncio.gather(*[scan_marketplace(client,s) for s in MARKETPLACE_SOURCES]);seeded,live=await refresh_marketplace_entry_points(client)
  return {'added':sum(x[0] for x in results)+seeded,'found':sum(x[1] for x in results)+len(MARKETPLACE_ENTRY_POINTS),'verified_live':live}

def auto_claimable(d):
 cfg=authorized_connectors().get(d['source']);return bool(AUTO_CLAIM_ENABLED and d.get('claimability')=='auto_api' and isinstance(cfg,dict) and cfg.get('endpoint') and cfg.get('automation_permitted'))

async def claim_one(d):
 if not d:return {'ok':False,'message':'ไม่พบรายการ'}
 if not auto_claimable(d):return {'ok':False,'direct':d.get('direct_claim_url') or d.get('url'),'claimability':d.get('claimability'),'message':d.get('claim_reason') or 'ต้นทางยังไม่อนุญาตให้รับอัตโนมัติ'}
 cfg=authorized_connectors()[d['source']];headers={'User-Agent':'MoneyHunterAI/15.0'};te=cfg.get('token_env')
 if te and os.getenv(te):headers['Authorization']='Bearer '+os.getenv(te)
 try:
  async with httpx.AsyncClient(timeout=20,follow_redirects=False) as client:r=await client.post(cfg['endpoint'],json={'offer_url':d['url'],'offer_title':d['title'],'target_country':TARGET_COUNTRY},headers=headers)
  ok=200<=r.status_code<300;status='submitted' if ok else 'failed';msg='ต้นทางยืนยันรับคำขอแล้ว' if ok else f'ต้นทางตอบ HTTP {r.status_code}'
 except Exception as e:ok=False;status='failed';msg=type(e).__name__
 c=conn();c.execute('UPDATE deals SET claim_status=?,claim_mode=? WHERE id=?',(status,'authorized_api',d['id']));c.execute('INSERT INTO claim_log(deal_id,status,message,created_at) VALUES(?,?,?,?)',(d['id'],status,msg,now()));c.commit();c.close();return {'ok':ok,'message':msg}

async def auto_claim_all():
 c=conn();rows=[dict(x) for x in c.execute("SELECT * FROM deals WHERE claimability='auto_api' AND claim_status='ready' AND risk!='high' LIMIT 100")];c.close();attempted=submitted=0
 for d in rows:
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
def deals(hunter:str='all',mode:str='all',mechanic:str='all',platform:str='all',owner:str='all',scope:str='all'):
 c=conn();q="SELECT * FROM deals WHERE risk!='high'";args=[]
 if scope=='marketplace':
  marks=','.join('?' for _ in MARKETPLACE_SOURCES);q+=f" AND platform IN ({marks}) AND source_trust IN ('official-entry-point','official-public-page')";args.extend(s['name'] for s in MARKETPLACE_SOURCES)
 if hunter in ('money','physical','digital'):q+=' AND hunter_type=?';args.append(hunter)
 if mode in ('auto_api','direct','login','manual','blocked'):q+=' AND claimability=?';args.append(mode)
 if mechanic in ('free','coupon','cashback','sample','purchase_required','exchange_purchase','game','checkin','referral','flash_sale','other'):q+=' AND offer_mechanic=?';args.append(mechanic)
 known_platforms={s['name'] for s in MARKETPLACE_SOURCES}|{'GamerPower','Epic Games','Steam','Reddit Freebies'}
 if platform in known_platforms:q+=' AND platform=?';args.append(platform)
 if owner=='system':q+=" AND claimability='auto_api'"
 elif owner=='user':q+=" AND claimability!='auto_api'"
 q+=" ORDER BY sort_score DESC, discount_amount DESC, discount_percent DESC, id DESC LIMIT 500"
 rows=[dict(x) for x in c.execute(q,args)];c.close()
 for d in rows:
  d['auto_claimable']=auto_claimable(d)
  d['claim_owner']='system' if d['auto_claimable'] else 'user'
  d['claim_owner_label']='สามมารกดรับให้ได้' if d['auto_claimable'] else 'คุณต้องกดรับเอง'
  if d['auto_claimable']:d['claim_owner_reason']='ต้นทางอนุญาตระบบอัตโนมัติและไม่ต้องยืนยันแทนผู้ใช้'
  elif d.get('requires_purchase'):d['claim_owner_reason']='มีเงื่อนไขซื้อหรือชำระเงิน'
  elif d.get('requires_game'):d['claim_owner_reason']='ต้องเล่นเกมหรือร่วมกิจกรรมด้วยตนเอง'
  elif d.get('requires_checkin'):d['claim_owner_reason']='ต้องเช็กอินในบัญชีของคุณ'
  elif d.get('requires_referral'):d['claim_owner_reason']='ต้องเชิญเพื่อนจากบัญชีของคุณ'
  elif d.get('claimability')=='login':d['claim_owner_reason']='ต้องล็อกอินหรือยืนยันตัวตน'
  else:d['claim_owner_reason']='ต้นทางยังไม่มีช่องทางอัตโนมัติที่ได้รับอนุญาต'
 return rows
@app.post('/api/claim/{deal_id}')
async def claim(deal_id:int):
 c=conn();r=c.execute('SELECT * FROM deals WHERE id=?',(deal_id,)).fetchone();c.close();return await claim_one(dict(r) if r else None)
@app.get('/claim/{deal_id}')
def direct_claim(deal_id:int):
 c=conn();r=c.execute('SELECT direct_claim_url,url,claimability,risk FROM deals WHERE id=?',(deal_id,)).fetchone();c.close()
 if not r:raise HTTPException(404)
 if r['risk']=='high' or r['claimability']=='blocked':raise HTTPException(403,'รายการนี้ไม่ผ่านตัวกรองความปลอดภัย')
 return RedirectResponse(r['direct_claim_url'] or r['url'])
@app.post('/api/claim-all')
async def claim_all():
 a,b=await auto_claim_all();return {'ok':True,'attempted':a,'submitted':b}
@app.get('/api/source-health')
def source_health():
 c=conn();r=[dict(x) for x in c.execute('SELECT * FROM source_health ORDER BY source')];c.close();return r
@app.get('/api/marketplaces')
def marketplaces():return [{'name':s['name'],'url':s['url']} for s in MARKETPLACE_SOURCES]
@app.get('/api/stats')
def stats():
 c=conn();
 marketplace_names=','.join("'"+s['name'].replace("'","''")+"'" for s in MARKETPLACE_SOURCES)
 def n(w):return c.execute("SELECT COUNT(*) FROM deals WHERE risk!='high' AND source_trust IN ('official-entry-point','official-public-page') AND platform IN ("+marketplace_names+") AND "+w).fetchone()[0]
 out={'total':n('1=1'),'money':n("hunter_type='money'"),'physical':n("hunter_type='physical'"),'digital':n("hunter_type='digital'"),'auto_api':n("claimability='auto_api'"),'user_claim':n("claimability!='auto_api'"),'direct':n("claimability='direct'"),'login':n("claimability='login'"),'live':n('url_live=1'),'submitted':n("claim_status='submitted'"),'free':n("offer_mechanic='free'"),'coupon':n("offer_mechanic='coupon'"),'cashback':n("offer_mechanic='cashback'"),'game':n("offer_mechanic='game'")};c.close();return out
@app.get('/api/readiness')
def readiness():return {'version':'15.0','target_country':TARGET_COUNTRY,'auto_scan_minutes':30,'authorized_auto_claim_sources':len(authorized_connectors()),'message':'Claimability Engine: แยกรับอัตโนมัติ / รับตรง / ต้องล็อกอิน / ตรวจเอง'}
@app.get('/api/wallet')
def wallet():
 c=conn();rows=[dict(x) for x in c.execute("SELECT * FROM ledger WHERE status='confirmed' ORDER BY id DESC")];c.close();total=sum(float(x['amount']) for x in rows if (x['currency'] or '').upper()=='THB');return {'confirmed_thb':round(total,2),'transactions':rows}
@app.get('/health')
def healthcheck():return {'ok':True,'version':'15.0'}

HTML='''<!doctype html><html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Money Hunter AI v15</title><style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f4f6fa;margin:0;color:#172033}.wrap{max-width:1050px;margin:auto;padding:20px}.hero{background:#0f172a;color:#fff;border-radius:24px;padding:24px}.btn{border:0;border-radius:12px;padding:11px 14px;font-weight:750;cursor:pointer}.green{background:#22c55e}.blue{background:#dbeafe}.full{width:100%;margin-top:10px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.card{background:#fff;border-radius:18px;padding:16px;margin-top:12px}.n{font-size:25px;font-weight:800}.muted{color:#697386;font-size:13px}.deal{border-top:1px solid #eee;padding:12px 0}.tag{display:inline-block;background:#eef2ff;border-radius:999px;padding:4px 8px;font-size:12px;margin:2px}.auto_api{background:#dcfce7}.direct{background:#dbeafe}.login{background:#fef3c7}.manual{background:#f3f4f6}.blocked{background:#fee2e2}.go{display:inline-block;text-decoration:none;background:#2563eb;color:#fff;border-radius:11px;padding:9px 12px;font-weight:700;margin-top:7px}@media(max-width:720px){.grid{grid-template-columns:1fr 1fr}}</style></head><body><div class=wrap><div class=hero><b>Money Hunter AI v15 — Claimability Engine</b><div>หาให้เจอ แล้วบอกให้ชัดว่า “รับได้จริงแบบไหน”</div><button id=scan class="btn green full" onclick="go()">🌍 ให้สามมารค้นหาและตรวจหน้ารับจริง</button></div><div class=grid><div class=card><div>พบทั้งหมด</div><div class=n id=total>0</div></div><div class=card><div>🤖 รับอัตโนมัติ</div><div class=n id=autoapi>0</div></div><div class=card><div>🎯 หน้ารับตรง</div><div class=n id=direct>0</div></div><div class=card><div>🔐 ต้องล็อกอิน</div><div class=n id=login>0</div></div><div class=card><div>ลิงก์ตรวจแล้ว</div><div class=n id=live>0</div></div><div class=card><div>💰 เงิน</div><div class=n id=money>0</div></div><div class=card><div>🎁 สินค้า</div><div class=n id=physical>0</div></div><div class=card><div>💻 ดิจิทัล</div><div class=n id=digital>0</div></div></div><div class=card><b>แหล่งค้นหา</b><div id=health class=muted></div></div><div class=card><button class="btn blue" onclick="load('all')">ทั้งหมด</button><button class="btn blue" onclick="load('auto_api')">🤖 รับอัตโนมัติ</button><button class="btn blue" onclick="load('direct')">🎯 รับตรง</button><button class="btn blue" onclick="load('login')">🔐 ต้องล็อกอิน</button><div id=deals></div></div></div><script>function esc(s){return String(s||'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]))}async function stat(){let s=await fetch('/api/stats').then(r=>r.json());for(let k of ['total','money','physical','digital','direct','login','live'])document.getElementById(k).textContent=s[k];autoapi.textContent=s.auto_api;let h=await fetch('/api/source-health').then(r=>r.json());health.innerHTML=h.map(x=>`${esc(x.source)}: ${esc(x.status)} · พบ ${x.found}`).join('<br>')}async function load(mode='all'){let ds=await fetch('/api/deals?mode='+mode).then(r=>r.json());deals.innerHTML=ds.slice(0,100).map(d=>{let action=d.auto_claimable?`<button class="btn green" onclick="claim(${d.id},this)">AI รับให้เลย</button>`:(d.claimability==='direct'||d.claimability==='login'?`<a class=go href="/claim/${d.id}" target="_blank">${d.claimability==='login'?'ไปล็อกอินแล้วรับ →':'ไปหน้ารับตรง →'}</a>`:'');return `<div class=deal><span class="tag ${esc(d.claimability)}">${esc(d.claimability)}</span><span class=tag>เกรด ${esc(d.grade)}</span><span class=tag>${esc(d.source)}</span>${d.url_live?'<span class="tag auto_api">ลิงก์ใช้งานได้</span>':''}<div><b>${esc(d.title)}</b></div><div class=muted>${esc(d.claim_reason)} ${d.worth?'· '+esc(d.worth):''}</div>${action}</div>`}).join('')||'ยังไม่มีรายการในหมวดนี้';await stat()}async function go(){scan.disabled=true;scan.textContent='กำลังค้นหา + ตรวจลิงก์ต้นทาง...';let r=await fetch('/api/search').then(r=>r.json());await load();scan.textContent='พบ '+r.found+' · ตรวจลิงก์ใช้งานได้ '+r.verified_live;setTimeout(()=>{scan.textContent='🌍 ให้สามมารค้นหาและตรวจหน้ารับจริง';scan.disabled=false},2500)}async function claim(id,b){b.disabled=true;let r=await fetch('/api/claim/'+id,{method:'POST'}).then(r=>r.json());alert(r.message);await load()}load()</script></body></html>'''
HTML_MARKETPLACE='''<!doctype html><html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Money Hunter AI v15</title><style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f4f6fa;margin:0;color:#172033}.wrap{max-width:1080px;margin:auto;padding:20px}.hero{background:linear-gradient(135deg,#0f172a,#164e63);color:#fff;border-radius:24px;padding:24px}.btn{border:0;border-radius:12px;padding:11px 14px;font-weight:750;cursor:pointer}.green{background:#22c55e}.blue{background:#dbeafe}.full{width:100%;margin-top:12px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.card{background:#fff;border-radius:18px;padding:16px;margin-top:12px}.n{font-size:25px;font-weight:800}.muted{color:#697386;font-size:13px}.deal{border-top:1px solid #eee;padding:12px 0}.tag{display:inline-block;background:#eef2ff;border-radius:999px;padding:4px 8px;font-size:12px;margin:2px}.auto_api,.free,.system{background:#dcfce7}.direct,.coupon{background:#dbeafe}.login,.game,.checkin,.user{background:#fef3c7}.cashback{background:#fae8ff}.purchase_required,.exchange_purchase{background:#ffedd5}.go{display:inline-block;text-decoration:none;background:#2563eb;color:#fff;border-radius:11px;padding:9px 12px;font-weight:700;margin-top:7px}.claim-ai{background:#16a34a;color:#fff}.filters{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0}.filters select{padding:11px;border:1px solid #d9dee8;border-radius:11px;background:#fff;font-size:14px}.sources a{display:inline-block;margin:6px 8px 0 0;color:#1d4ed8}.platform-group{border:1px solid #e5e7eb;border-radius:16px;margin:14px 0;overflow:hidden}.platform-head{display:flex;justify-content:space-between;align-items:center;background:#eef2ff;padding:14px 16px;font-size:18px;font-weight:800}.claim-group{padding:12px 16px}.claim-group+.claim-group{border-top:1px dashed #d1d5db}.claim-title{font-weight:750;margin-bottom:6px}.empty{color:#94a3b8;padding:8px 0}@media(max-width:720px){.grid{grid-template-columns:1fr 1fr}.filters{grid-template-columns:1fr}}
</style></head><body><div class=wrap><div class=hero><b>Money Hunter AI v15 — Marketplace Rewards</b><div>ค้นหาเงินจริง ของรางวัล ของแจก และส่วนลด พร้อมแยกเงื่อนไขก่อนกดรับ</div><button id=scan class="btn green full" onclick="go()">🌍 ค้นหาและจัดหมวดข้อเสนอจริง</button></div>
<div class=grid><div class=card><div>พบทั้งหมด</div><div class=n id=total>0</div></div><div class=card><div>แจกฟรี</div><div class=n id=free>0</div></div><div class=card><div>คูปอง/ส่วนลด</div><div class=n id=coupon>0</div></div><div class=card><div>เงินคืน</div><div class=n id=cashback>0</div></div><div class=card><div>เล่นเกม</div><div class=n id=game>0</div></div><div class=card><div>เงินจริง</div><div class=n id=money>0</div></div><div class=card><div>สินค้าจริง</div><div class=n id=physical>0</div></div><div class=card><div>ดิจิทัล</div><div class=n id=digital>0</div></div></div>
<div class=card><b>แหล่งค้นหา</b><div id=health class=muted></div><div id=sources class=sources></div></div>
<div class=card><b>รายการแยกตามแพลตฟอร์ม</b><div class=filters><select id=mechanic onchange="load()"><option value=all>ทุกประเภท</option><option value=coupon>คูปอง/ส่วนลด</option><option value=flash_sale>Flash Sale</option><option value=cashback>เงินคืน</option><option value=checkin>เหรียญ/เช็กอิน</option></select></div><div id=deals></div></div></div>
<script>const all='all';</script>
<script>let platformNames=[];function esc(s){return String(s||'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]))}async function stat(){let s=await fetch('/api/stats').then(r=>r.json());for(let k of ['total','free','coupon','cashback','game','money','physical','digital'])document.getElementById(k).textContent=s[k]||0;let h=await fetch('/api/source-health').then(r=>r.json());health.innerHTML=h.filter(x=>platformNames.includes(x.source)).map(x=>`${esc(x.source)}: ${esc(x.status)} · พบ ${x.found} · ${esc(x.message)}`).join('<br>')}async function setup(){let ms=await fetch('/api/marketplaces').then(r=>r.json());platformNames=ms.map(m=>m.name);for(let m of ms)sources.insertAdjacentHTML('beforeend',`<a href="${esc(m.url)}" target=_blank rel="noopener">${esc(m.name)} ↗</a>`);await load()}function valueBadge(d){if(d.offer_mechanic==='free')return '<span class="tag free">ฟรี</span>';if(d.discount_amount>0)return `<span class="tag cashback">ลดสูงสุด ฿${d.discount_amount.toLocaleString()}</span>`;if(d.discount_percent>0)return `<span class="tag coupon">ลด ${d.discount_percent}%</span>`;if((d.title+d.description).includes('ส่งฟรี'))return '<span class="tag coupon">ส่งฟรี</span>';return '<span class="tag manual">ไม่ระบุมูลค่า</span>'}function dealCard(d){let live=d.url_live?'<span class="tag auto_api">ตรวจลิงก์แล้ว</span>':'<span class="tag manual">รอตรวจลิงก์</span>';return `<div class=deal><span class="tag ${esc(d.offer_mechanic)}">${esc(d.offer_mechanic_label)}</span>${valueBadge(d)}${live}<div><b>${esc(d.title)}</b></div><div class=muted>${esc(d.description||d.claim_owner_reason)}${d.min_spend>0?`<br>ยอดขั้นต่ำ ฿${d.min_spend.toLocaleString()}`:''}<br>เรียงตามมูลค่าที่ต้นทางระบุ โดยไม่ประมาณตัวเลขเอง</div><a class=go href="/claim/${d.id}" target=_blank rel="noopener">เปิดหน้ารับสิทธิ์ →</a></div>`}function claimSection(items){return `<div class=claim-group><div class=claim-title>เรียง: ฟรี → ส่วนลดมากไปน้อย (${items.length})</div>${items.length?items.map(dealCard).join(''):'<div class=empty>ยังไม่มีรายการ</div>'}</div>`}async function load(){let q=new URLSearchParams({mechanic:mechanic.value,scope:'marketplace'});let ds=await fetch('/api/deals?'+q).then(r=>r.json());deals.innerHTML=platformNames.map(name=>{let items=ds.filter(d=>(d.platform||d.source)===name&&d.claim_owner==='user');return `<section class=platform-group><div class=platform-head><span>${esc(name)}</span><span>${items.length} รายการ</span></div>${claimSection(items)}</section>`}).join('')||'<div class=empty>ยังไม่พบรายการ</div>';await stat()}async function go(){scan.disabled=true;scan.textContent='กำลังค้นหาและเรียงส่วนลด...';let r=await fetch('/api/search').then(r=>r.json());await load();scan.textContent=`พบ ${r.found} รายการ · เรียงมูลค่าแล้ว`;setTimeout(()=>{scan.textContent='🌍 ค้นหาและจัดหมวดข้อเสนอจริง';scan.disabled=false},3000)}setup()</script></body></html>'''

HTML_MARKETPLACE_V2='''<!doctype html><html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#071426"><title>Money Hunter AI v15</title><style>
*{box-sizing:border-box}body{margin:0;font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Tahoma,sans-serif;background:#f5f7fb;color:#132238}.shell{max-width:1160px;margin:auto;padding:18px}.hero{position:relative;overflow:hidden;background:linear-gradient(135deg,#071426 0%,#123a59 56%,#0e7490 100%);color:#fff;border-radius:28px;padding:28px;box-shadow:0 18px 50px #0f36552b}.hero:after{content:"";position:absolute;width:260px;height:260px;border-radius:50%;background:#5eead433;right:-70px;top:-120px}.brand{display:flex;align-items:center;gap:12px;font-weight:850;font-size:22px}.logo{display:grid;place-items:center;width:42px;height:42px;border-radius:14px;background:linear-gradient(135deg,#facc15,#fb923c);color:#172033}.hero h1{font-size:clamp(27px,5vw,44px);line-height:1.08;margin:26px 0 9px;max-width:720px}.hero p{margin:0;color:#d7eef8;max-width:680px;line-height:1.65}.scan{position:relative;z-index:1;width:100%;margin-top:22px;border:0;border-radius:16px;padding:15px 18px;background:linear-gradient(135deg,#facc15,#fb923c);color:#172033;font-weight:850;font-size:16px;cursor:pointer;box-shadow:0 10px 25px #f59e0b40}.scan:disabled{opacity:.7}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0}.stat{background:#fff;border:1px solid #e8edf4;border-radius:19px;padding:16px;box-shadow:0 7px 24px #1e3a5f0a}.stat .num{font-size:27px;font-weight:900;margin-top:5px}.stat .label{font-size:13px;color:#66758b}.panel{background:#fff;border:1px solid #e8edf4;border-radius:22px;padding:18px;box-shadow:0 8px 30px #1e3a5f0a;margin-top:14px}.panel-title{display:flex;align-items:center;justify-content:space-between;gap:10px}.panel-title h2{margin:0;font-size:19px}.rule{font-size:12px;color:#64748b;background:#f1f5f9;border-radius:999px;padding:7px 10px}.tabs{display:flex;gap:8px;overflow:auto;padding:14px 1px 4px;scrollbar-width:none}.tab{white-space:nowrap;border:1px solid #dce4ee;background:#fff;color:#334155;padding:10px 14px;border-radius:999px;font-weight:750;cursor:pointer}.tab.active{background:#132d46;color:#fff;border-color:#132d46}.filters{display:grid;grid-template-columns:1fr auto;gap:10px;margin-top:13px}.filters select{min-width:190px;border:1px solid #dce4ee;border-radius:13px;padding:12px;background:#fff;color:#24364b;font-weight:650}.refresh-note{align-self:center;color:#718096;font-size:12px}.list{display:grid;gap:12px;margin-top:16px}.offer{display:grid;grid-template-columns:auto 1fr auto;gap:14px;align-items:center;border:1px solid #e5eaf1;border-radius:18px;padding:15px;transition:.18s;background:linear-gradient(180deg,#fff,#fbfdff)}.offer:hover{transform:translateY(-2px);box-shadow:0 12px 30px #16324f12}.platform-logo{width:48px;height:48px;border-radius:15px;display:grid;place-items:center;color:#fff;font-weight:900;font-size:18px}.TikTok{background:#151515}.Shopee{background:#ee4d2d}.Lazada{background:linear-gradient(135deg,#32149d,#f05a28)}.badges{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:5px}.badge{font-size:11px;font-weight:800;border-radius:999px;padding:5px 8px;background:#eef2f7;color:#45566d}.free{background:#dcfce7;color:#166534}.money{background:#fef3c7;color:#92400e}.percent{background:#dbeafe;color:#1d4ed8}.verified{background:#ecfeff;color:#0e7490}.offer h3{margin:0;font-size:16px;line-height:1.35}.desc{font-size:13px;color:#66758b;margin-top:5px;line-height:1.45}.claim{display:inline-flex;align-items:center;justify-content:center;min-width:140px;text-decoration:none;background:#1565d8;color:#fff;padding:12px 14px;border-radius:13px;font-weight:850;box-shadow:0 7px 16px #1565d82e}.empty{text-align:center;padding:34px 12px;color:#7b8798}.source-status{margin-top:13px;color:#708096;font-size:12px;line-height:1.7}.footer{text-align:center;color:#8290a3;font-size:12px;padding:22px}.skeleton{height:90px;border-radius:18px;background:linear-gradient(90deg,#f1f5f9,#fff,#f1f5f9);background-size:200% 100%;animation:pulse 1.3s infinite}@keyframes pulse{to{background-position:-200% 0}}@media(max-width:760px){.shell{padding:10px}.hero{border-radius:22px;padding:21px}.stats{grid-template-columns:1fr 1fr}.panel{padding:14px}.panel-title{align-items:flex-start;flex-direction:column}.filters{grid-template-columns:1fr}.filters select{width:100%}.offer{grid-template-columns:auto 1fr}.claim{grid-column:1/-1;width:100%}.platform-logo{width:43px;height:43px}}
</style></head><body><main class=shell><section class=hero><div class=brand><span class=logo>฿</span> Money Hunter AI <small>v15</small></div><h1>รวมสิทธิ์คุ้มค่า<br>ให้กดรับได้ง่ายขึ้น</h1><p>ค้นหาข้อเสนอจาก TikTok Shop, Shopee และ Lazada แล้วเรียงรายการฟรีและส่วนลดสูงไว้ก่อน</p><button id=scan class=scan onclick="scanNow()">✨ ค้นหาและอัปเดตสิทธิ์ล่าสุด</button></section>
<section class=stats><div class=stat><div class=label>รายการทั้งหมด</div><div class=num id=total>0</div></div><div class=stat><div class=label>แจกฟรี</div><div class=num id=free>0</div></div><div class=stat><div class=label>คูปอง/ส่วนลด</div><div class=num id=coupon>0</div></div><div class=stat><div class=label>ลิงก์ตรวจแล้ว</div><div class=num id=live>0</div></div></section>
<section class=panel><div class=panel-title><h2>สิทธิ์ที่น่าสนใจ</h2><span class=rule>เรียง: ฟรี → มูลค่าสูง → ต่ำ</span></div><div id=tabs class=tabs><button class="tab active" data-platform=all>ทั้งหมด</button></div><div class=filters><select id=mechanic onchange="loadDeals()"><option value=all>ทุกประเภท</option><option value=free>แจกฟรี</option><option value=coupon>คูปอง/ส่วนลด</option><option value=flash_sale>Flash Sale</option><option value=cashback>เงินคืน</option><option value=checkin>เหรียญ/เช็กอิน</option></select><span class=refresh-note id=updated>กำลังโหลดข้อมูล…</span></div><div id=list class=list><div class=skeleton></div><div class=skeleton></div></div><div id=status class=source-status></div></section><div class=footer>Money Hunter AI แสดงเฉพาะข้อมูลที่ต้นทางเปิดเผย • การรับสิทธิ์ยืนยันในบัญชีของคุณ</div></main>
<script>
let activePlatform='all';const esc=s=>String(s||'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));const logo=n=>n==='TikTok Shop'?'TT':n==='Shopee'?'S':'L';
function valueBadge(d){if(d.offer_mechanic==='free')return '<span class="badge free">🎁 ฟรี</span>';if(d.discount_amount>0)return `<span class="badge money">ลดสูงสุด ฿${Number(d.discount_amount).toLocaleString()}</span>`;if(d.discount_percent>0)return `<span class="badge percent">ลด ${Number(d.discount_percent)}%</span>`;if(((d.title||'')+(d.description||'')).includes('ส่งฟรี'))return '<span class="badge percent">ส่งฟรี</span>';return '<span class=badge>ไม่ระบุมูลค่า</span>'}
function card(d){let p=d.platform||d.source;return `<article class=offer><div class="platform-logo ${esc(p.split(' ')[0])}">${logo(p)}</div><div><div class=badges><span class=badge>${esc(p)}</span>${valueBadge(d)}${d.url_live?'<span class="badge verified">✓ ตรวจลิงก์แล้ว</span>':''}</div><h3>${esc(d.title)}</h3><div class=desc>${esc(d.description||d.claim_owner_reason)}${d.min_spend>0?` • ขั้นต่ำ ฿${Number(d.min_spend).toLocaleString()}`:''}</div></div><a class=claim href="/claim/${d.id}" target=_blank rel="noopener">กดรับที่ต้นทาง →</a></article>`}
async function setup(){let ms=await fetch('/api/marketplaces').then(r=>r.json());for(let m of ms)tabs.insertAdjacentHTML('beforeend',`<button class=tab data-platform="${esc(m.name)}">${esc(m.name)}</button>`);tabs.addEventListener('click',e=>{let b=e.target.closest('.tab');if(!b)return;activePlatform=b.dataset.platform;document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x===b));loadDeals()});await loadDeals()}
async function loadDeals(){list.innerHTML='<div class=skeleton></div><div class=skeleton></div>';let q=new URLSearchParams({mechanic:mechanic.value,scope:'marketplace',platform:activePlatform});let [ds,st,h]=await Promise.all([fetch('/api/deals?'+q).then(r=>r.json()),fetch('/api/stats').then(r=>r.json()),fetch('/api/source-health').then(r=>r.json())]);for(let k of ['total','free','coupon','live'])document.getElementById(k).textContent=st[k]||0;list.innerHTML=ds.length?ds.map(card).join(''):'<div class=empty>ยังไม่พบรายการในหมวดนี้</div>';status.innerHTML=h.filter(x=>['TikTok Shop','Shopee','Lazada'].includes(x.source)).map(x=>`${esc(x.source)}: ${esc(x.message)} (${x.found})`).join('<br>');updated.textContent='อัปเดตล่าสุด '+new Date().toLocaleTimeString('th-TH',{hour:'2-digit',minute:'2-digit'})}
async function scanNow(){scan.disabled=true;scan.textContent='⏳ กำลังค้นหาและจัดลำดับ…';try{let r=await fetch('/api/search').then(r=>r.json());await loadDeals();scan.textContent=`✓ อัปเดตแล้ว ${r.found} รายการ`}catch(e){scan.textContent='ลองใหม่อีกครั้ง'}finally{setTimeout(()=>{scan.textContent='✨ ค้นหาและอัปเดตสิทธิ์ล่าสุด';scan.disabled=false},3000)}}setup();
</script></body></html>'''

@app.get('/',response_class=HTMLResponse)
def home():return HTML_MARKETPLACE_V2
