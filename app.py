"""PerkFi application prototype. Gateway enforcement is a separate adapter."""
import os, re, csv, io, sqlite3, secrets, time, hashlib, smtplib
import qrcode
from qrcode.image.svg import SvgPathFillImage
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path
from email.message import EmailMessage
from urllib.parse import urlparse
from flask import Flask, request, session, redirect, url_for, render_template, flash, abort, g, jsonify, Response
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from integrations import weather, transport
load_dotenv()
ROOT=Path(__file__).parent
app=Flask(__name__)
(ROOT/'instance').mkdir(exist_ok=True)
secretfile=ROOT/'instance'/'secret'
if not secretfile.exists(): secretfile.write_text(secrets.token_hex(32))
app.config.update(SECRET_KEY=os.getenv('SECRET_KEY') or secretfile.read_text(), DATABASE=str(ROOT/'instance'/'perkfi.db'), SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', MAX_CONTENT_LENGTH=100_000)

def now(): return int(time.time())
def db():
    if 'db' not in g:
        g.db=sqlite3.connect(app.config['DATABASE'], timeout=15)
        g.db.row_factory=sqlite3.Row
        g.db.execute('PRAGMA foreign_keys=ON')
    return g.db
@app.teardown_appcontext
def close(_):
    if 'db' in g: g.db.close()
def rows(q,p=()): return db().execute(q,p).fetchall()
def one(q,p=()): return db().execute(q,p).fetchone()
def execute(q,p=()):
    cur=db().execute(q,p); db().commit(); return cur

def init_db():
    db().executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, contact TEXT UNIQUE, role TEXT NOT NULL, password TEXT, merchant INTEGER, marketing INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS visits(id TEXT PRIMARY KEY, started INTEGER, ended INTEGER, expires INTEGER);
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, kind TEXT, ts INTEGER);
    CREATE TABLE IF NOT EXISTS otps(contact TEXT PRIMARY KEY, digest TEXT, expires INTEGER, attempts INTEGER, sent INTEGER);
    CREATE TABLE IF NOT EXISTS notices(id INTEGER PRIMARY KEY, title TEXT, body TEXT, category TEXT, publisher TEXT, source TEXT, expires INTEGER, published INTEGER, updated INTEGER);
    CREATE TABLE IF NOT EXISTS places(id INTEGER PRIMARY KEY, name TEXT, category TEXT, description TEXT, address TEXT, hours TEXT, source TEXT);
    CREATE TABLE IF NOT EXISTS rewards(id INTEGER PRIMARY KEY, merchant INTEGER, title TEXT, cost INTEGER CHECK(cost>0), stock INTEGER CHECK(stock>=0), expires INTEGER, terms TEXT);
    CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY, user INTEGER REFERENCES users(id), merchant INTEGER, amount INTEGER, reason TEXT, ts INTEGER);
    CREATE TABLE IF NOT EXISTS vouchers(code TEXT PRIMARY KEY, user INTEGER REFERENCES users(id), merchant INTEGER, title TEXT, cost INTEGER, terms TEXT, expires INTEGER, redeemed INTEGER, staff INTEGER, claim_key TEXT UNIQUE);
    CREATE TABLE IF NOT EXISTS awards(receipt TEXT PRIMARY KEY, user INTEGER, merchant INTEGER, points INTEGER, ts INTEGER);
    CREATE TABLE IF NOT EXISTS growth(user INTEGER, day TEXT, PRIMARY KEY(user,day));
    CREATE TABLE IF NOT EXISTS attempts(key TEXT PRIMARY KEY, count INTEGER, until INTEGER);
    ''')
    if not one('SELECT id FROM rewards LIMIT 1'):
        execute('INSERT INTO rewards(merchant,title,cost,stock,expires,terms) VALUES(1,?,?,?,?,?)',('Free topping',80,30,now()+30*86400,'One topping with a purchased drink. Present voucher to Kopi Corner staff.'))
    if not one('SELECT id FROM places LIMIT 1'):
        for item in [('Kopi Corner','Food & Drink','Fictional participating cafe. Browse optional rewards.','Taman Harmoni Community Hub','09:00–18:00',''),('Harmoni Library','Amenities','Fictional community library and reading space.','Taman Harmoni Community Hub','09:00–17:00',''),('Community clean-up','Events','Demonstration event. Contact the organiser before attending.','Community Hall','Date to be confirmed','')]:
            execute('INSERT INTO places(name,category,description,address,hours,source) VALUES(?,?,?,?,?,?)',item)
    if not one('SELECT id FROM notices LIMIT 1'):
        execute('INSERT INTO notices(title,body,category,publisher,source,expires,published,updated) VALUES(?,?,?,?,?,?,1,?)',('Welcome to PerkFi','Browse community information and optionally join rewards. This is sample community content.','Community','PerkFi demonstration team','',now()+30*86400,now()))

@app.before_request
def prepare():
    if 'csrf' not in session: session['csrf']=secrets.token_hex(24)
    g.user=one('SELECT * FROM users WHERE id=?',(session.get('uid'),)) if session.get('uid') else None
    if request.method=='POST' and not secrets.compare_digest(request.form.get('csrf',''),session['csrf']): abort(400, 'Form expired. Reload the page and try again.')
@app.context_processor
def context(): return dict(user=g.get('user'), csrf=session.get('csrf'), site=os.getenv('SITE_NAME','Taman Harmoni Community Hub'))
@app.template_filter('date')
def date(v): return datetime.fromtimestamp(v,timezone(timedelta(hours=8))).strftime('%d %b %Y, %H:%M MYT') if v else '—'

def role(*allowed):
    def deco(fn):
        @wraps(fn)
        def wrapped(*a,**kw):
            if not g.user: return redirect(url_for('login'))
            if g.user['role'] not in allowed: abort(403)
            return fn(*a,**kw)
        return wrapped
    return deco

def limit(key,maximum=10,seconds=600):
    r=one('SELECT * FROM attempts WHERE key=?',(key,))
    if not r or r['until']<now(): execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?)',(key,1,now()+seconds)); return
    if r['count']>=maximum: abort(429,'Too many attempts. Please try again later.')
    execute('UPDATE attempts SET count=count+1 WHERE key=?',(key,))

def page(title,body,**kw): return render_template('page.html',title=title,body=body,**kw)
def source_url(value):
    value=value.strip()
    if value and urlparse(value).scheme not in ('https','http'): abort(400,'Source must be an http or https URL.')
    return value

def balance(uid): return one('SELECT COALESCE(SUM(amount),0) n FROM ledger WHERE user=? AND merchant=1',(uid,))['n']

@app.get('/')
def welcome(): return render_template('welcome.html',title='Welcome')
@app.post('/connect')
def connect():
    if request.form.get('terms')!='yes': flash('Please accept the access terms.'); return redirect('/')
    current=one('SELECT * FROM visits WHERE id=?',(session.get('visit'),))
    if not current or current['ended'] or current['expires']<=now():
        sid=secrets.token_urlsafe(24); execute('INSERT INTO visits VALUES(?,?,NULL,?)',(sid,now(),now()+7200)); session['visit']=sid
        execute('INSERT INTO events(kind,ts) VALUES(?,?)',('portal_session_started',now()))
    return redirect('/home')
@app.post('/disconnect')
def disconnect():
    execute('UPDATE visits SET ended=? WHERE id=? AND ended IS NULL',(now(),session.get('visit')));session.pop('visit',None)
    flash('Your portal session has ended.'); return redirect('/')
@app.get('/home')
def home():
    visit=one('SELECT * FROM visits WHERE id=?',(session.get('visit'),))
    return render_template('home.html',title='Your community',visit=visit,active=bool(visit and not visit['ended'] and visit['expires']>now()),notices=rows('SELECT * FROM notices WHERE published=1 AND expires>? ORDER BY updated DESC',(now(),)))
@app.get('/notices')
def notices(): return render_template('notices.html',title='Community notices',notices=rows('SELECT * FROM notices WHERE published=1 AND expires>? ORDER BY updated DESC',(now(),)))
@app.get('/notice/<int:nid>')
def notice(nid):
    n=one('SELECT * FROM notices WHERE id=?',(nid,))
    if not n or (not n['published'] and (not g.user or g.user['role']!='operator')): abort(404)
    return render_template('notice.html',title=n['title'],n=n,expired=n['expires']<=now())
@app.get('/explore')
def explore():
    q=request.args.get('q','')[:100]
    return render_template('explore.html',title='Explore nearby',places=rows('SELECT * FROM places WHERE name LIKE ? OR category LIKE ?',('%'+q+'%','%'+q+'%')),q=q)
@app.get('/place/<int:pid>')
def place(pid):
    p=one('SELECT * FROM places WHERE id=?',(pid,))
    if not p: abort(404)
    return render_template('place.html',title=p['name'],p=p)
@app.get('/transport')
def transit(): return render_template('transport.html',title='Transport and weather')
@app.get('/api/weather')
def api_weather(): return jsonify(weather())
@app.get('/api/transport')
def api_transport(): return jsonify(transport(request.args.get('kind','positions')))

@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        contact=request.form.get('contact','').strip().lower()[:200]
        if not (re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',contact) or re.fullmatch(r'\+[1-9]\d{7,14}',contact)):
            flash('Enter a valid email or phone number with country code, such as +60123456789.');return redirect('/login')
        if one("SELECT id FROM users WHERE contact=? AND role!='member'",(contact,)): flash('Use staff sign-in for this account.');return redirect('/staff')
        limit('otp:'+contact,5,900);limit('otp-ip:'+str(request.remote_addr),15,900)
        r=one('SELECT sent FROM otps WHERE contact=?',(contact,))
        if r and now()-r['sent']<60: flash('Wait 60 seconds before requesting another code.');return redirect('/verify')
        code=f'{secrets.randbelow(1000000):06}'
        digest=hashlib.sha256((app.secret_key+contact+code).encode()).hexdigest()
        execute('INSERT OR REPLACE INTO otps VALUES(?,?,?,0,?)',(contact,digest,now()+600,now()))
        mode=os.getenv('OTP_MODE','console')
        try:
            if mode=='console': print(f'\nPerkFi verification for {contact}: {code}\n',flush=True)
            elif '@' in contact:
                msg=EmailMessage();msg['Subject']='Your PerkFi code';msg['From']=os.environ['SMTP_FROM'];msg['To']=contact;msg.set_content(f'Your code is {code}. It expires in 10 minutes.')
                with smtplib.SMTP(os.environ['SMTP_HOST'],int(os.getenv('SMTP_PORT','587')),timeout=15) as smtp:
                    smtp.starttls();smtp.login(os.environ['SMTP_USER'],os.environ['SMTP_PASSWORD']);smtp.send_message(msg)
            else:
                import requests
                r=requests.post('https://api.twilio.com/2010-04-01/Accounts/'+os.environ['TWILIO_SID']+'/Messages.json',auth=(os.environ['TWILIO_SID'],os.environ['TWILIO_TOKEN']),data={'From':os.environ['TWILIO_FROM'],'To':contact,'Body':f'PerkFi code: {code}. Expires in 10 minutes.'},timeout=15);r.raise_for_status()
        except Exception:
            execute('DELETE FROM otps WHERE contact=?',(contact,));flash('Code could not be sent. Check delivery settings or try again later.');return redirect('/login')
        session['pending']=contact;session['marketing']=request.form.get('marketing')=='yes'
        return redirect('/verify')
    return render_template('login.html',title='Optional membership')
@app.route('/verify',methods=['GET','POST'])
def verify():
    contact=session.get('pending')
    if not contact: return redirect('/login')
    if request.method=='POST':
        db().execute('BEGIN IMMEDIATE')
        r=one('SELECT * FROM otps WHERE contact=?',(contact,))
        code=request.form.get('code','')
        valid=r and r['expires']>now() and r['attempts']<5 and secrets.compare_digest(r['digest'],hashlib.sha256((app.secret_key+contact+code).encode()).hexdigest())
        if not valid:
            if r: db().execute('UPDATE otps SET attempts=attempts+1 WHERE contact=?',(contact,))
            db().commit();flash('Code is incorrect, expired, or locked. Request a new code after the cooldown.');return redirect('/verify')
        db().execute('DELETE FROM otps WHERE contact=?',(contact,))
        u=one('SELECT * FROM users WHERE contact=?',(contact,))
        if not u:
            cur=db().execute('INSERT INTO users(contact,role,marketing) VALUES(?,?,?)',(contact,'member',int(session.get('marketing',False))));uid=cur.lastrowid
            db().execute('INSERT INTO ledger(user,merchant,amount,reason,ts) VALUES(?,1,120,?,?)',(uid,'Prototype welcome credit',now()))
        else: uid=u['id']
        db().commit();session['uid']=uid;session.pop('pending',None);session.pop('marketing',None)
        flash('Welcome. Your rewards account is ready.');return redirect('/rewards')
    return render_template('verify.html',title='Verify your contact',console=os.getenv('OTP_MODE','console')=='console')
@app.route('/staff',methods=['GET','POST'])
def staff():
    if request.method=='POST':
        limit('staff:'+str(request.remote_addr),10,600)
        u=one("SELECT * FROM users WHERE contact=? AND role IN ('operator','merchant')",(request.form.get('contact','').lower(),))
        if u and check_password_hash(u['password'],request.form.get('password','')):
            session['uid']=u['id'];return redirect('/operator' if u['role']=='operator' else '/merchant')
        flash('Sign-in failed. Check your username and password.')
    return render_template('staff.html',title='Staff sign-in')
@app.post('/logout')
def logout(): session.pop('uid',None);flash('Signed out.');return redirect('/home')
@app.get('/rewards')
def rewards():
    return render_template('rewards.html',title='Kopi Corner rewards',rewards=rows('SELECT * FROM rewards WHERE expires>?',(now(),)),balance=balance(g.user['id']) if g.user else None)
@app.route('/claim/<int:rid>',methods=['GET','POST'])
@role('member')
def claim(rid):
    r=one('SELECT * FROM rewards WHERE id=?',(rid,))
    if not r: abort(404)
    if request.method=='POST':
        key=request.form.get('claim_key','')
        if key!=session.get('claim_key'): abort(400)
        db().execute('BEGIN IMMEDIATE')
        prior=one('SELECT code FROM vouchers WHERE claim_key=?',(key,))
        if prior: db().rollback();return redirect('/voucher/'+prior['code'])
        r=one('SELECT * FROM rewards WHERE id=?',(rid,))
        if r['expires']<=now() or r['stock']<1 or balance(g.user['id'])<r['cost']:
            db().rollback();flash('Reward unavailable or insufficient points.');return redirect('/rewards')
        code='KC-'+secrets.token_hex(6).upper()
        db().execute('UPDATE rewards SET stock=stock-1 WHERE id=?',(rid,))
        db().execute('INSERT INTO ledger(user,merchant,amount,reason,ts) VALUES(?,?,?,?,?)',(g.user['id'],r['merchant'],-r['cost'],'Claim '+code,now()))
        db().execute('INSERT INTO vouchers VALUES(?,?,?,?,?,?,?,?,?,?)',(code,g.user['id'],r['merchant'],r['title'],r['cost'],r['terms'],r['expires'],None,None,key));db().commit()
        return redirect('/voucher/'+code)
    session['claim_key']=secrets.token_urlsafe(24)
    return render_template('claim.html',title='Confirm reward claim',r=r,balance=balance(g.user['id']),claim_key=session['claim_key'])
@app.get('/voucher/<code>')
@role('member')
def voucher(code):
    v=one('SELECT * FROM vouchers WHERE code=? AND user=?',(code,g.user['id']))
    if not v: abort(404)
    return render_template('voucher.html',title='Your voucher',v=v,expired=v['expires']<=now())
@app.after_request
def protect_voucher_responses(response):
    # Voucher pages, QR images and status responses contain private data.
    if request.path.startswith(('/voucher/', '/api/vouchers/')):
        response.headers['Cache-Control'] = 'no-store, private'
        response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.get('/voucher/<code>/qr.svg')
@role('member')
def voucher_qr(code):
    v = one(
        'SELECT * FROM vouchers WHERE code=? AND user=?',
        (code, g.user['id'])
    )

    if not v:
        abort(404)

    if v['redeemed'] is not None or v['expires'] <= now():
        return Response(
            'This voucher is no longer available for redemption.',
            status=410,
            mimetype='text/plain'
        )

    # Encode only the voucher code: no phone number, email or redeem URL.
    image = qrcode.make(
        v['code'],
        image_factory=SvgPathFillImage,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4
    )

    output = io.BytesIO()
    image.save(output)

    return Response(
        output.getvalue(),
        mimetype='image/svg+xml'
    )


@app.get('/api/vouchers/<code>/status')
def voucher_status(code):
    # JSON errors let the browser handle an expired login explicitly.
    if not g.user:
        return jsonify(error='Sign in to view this voucher.'), 401

    if g.user['role'] != 'member':
        return jsonify(error='Member access required.'), 403

    v = one(
        'SELECT * FROM vouchers WHERE code=? AND user=?',
        (code, g.user['id'])
    )

    if not v:
        return jsonify(error='Voucher not found.'), 404

    if v['redeemed'] is not None:
        status = 'redeemed'
    elif v['expires'] <= now():
        status = 'expired'
    else:
        status = 'ready'

    return jsonify(
        status=status,
        expires_at=v['expires'],
        redeemed_at=v['redeemed']
    )
@app.route('/account',methods=['GET','POST'])
@role('member')
def account():
    if request.method=='POST':
        if request.form.get('action')=='grow':
            visit=one('SELECT * FROM visits WHERE id=?',(session.get('visit'),))
            if not visit or visit['ended'] or visit['expires']<=now() or now()-visit['started']<60: flash('Keep an active portal session for one minute before watering Sprout.')
            else:
                day=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
                cur=execute('INSERT OR IGNORE INTO growth VALUES(?,?)',(g.user['id'],day));flash('Sprout grew!' if cur.rowcount else 'Already watered today. Come back tomorrow.')
        else: execute('UPDATE users SET marketing=? WHERE id=?',(int(request.form.get('marketing')=='yes'),g.user['id']));flash('Preference saved.')
        return redirect('/account')
    return render_template('account.html',title='Your account and Sprout',balance=balance(g.user['id']),growth=one('SELECT count(*) n FROM growth WHERE user=?',(g.user['id'],))['n'],ledger=rows('SELECT * FROM ledger WHERE user=? ORDER BY id DESC',(g.user['id'],)),vouchers=rows('SELECT * FROM vouchers WHERE user=?',(g.user['id'],)))
def heatmap():
    today=datetime.now(timezone(timedelta(hours=8))).date()
    counts={r['hour']:r['n'] for r in rows("SELECT strftime('%Y-%m-%d %H',started,'unixepoch','+8 hours') hour,count(*) n FROM visits GROUP BY hour")}
    return [(str(today-timedelta(days=d)),[counts.get(str(today-timedelta(days=d))+f' {h:02}',0) for h in range(24)]) for d in range(6,-1,-1)]

@app.post('/merchant/award')
@role('merchant')
def award():
    try: uid=int(request.form['member']); points=int(request.form['points'])
    except (KeyError,ValueError): abort(400,'Enter a member ID and points.')
    receipt=request.form.get('receipt','').strip()[:100]
    if not receipt or not 1<=points<=100: abort(400,'Use a receipt reference and 1–100 points.')
    if not one("SELECT id FROM users WHERE id=? AND role='member'",(uid,)): abort(400,'Member not found.')
    db().execute('BEGIN IMMEDIATE')
    try:
        db().execute('INSERT INTO awards VALUES(?,?,?,?,?)',(str(g.user['merchant'])+':'+receipt,uid,g.user['merchant'],points,now()))
        db().execute('INSERT INTO ledger(user,merchant,amount,reason,ts) VALUES(?,?,?,?,?)',(uid,g.user['merchant'],points,'Staff-confirmed purchase '+receipt,now()));db().commit()
    except sqlite3.IntegrityError:
        db().rollback();flash('This receipt was already credited.');return redirect('/merchant')
    flash('Purchase points credited.');return redirect('/merchant')

@app.get('/operator')
@role('operator')
def operator():
    return render_template('operator.html',title='Community operator',notices=rows('SELECT * FROM notices ORDER BY updated DESC'),places=rows('SELECT * FROM places'),total=one('SELECT count(*) n FROM visits')['n'],active=one('SELECT count(*) n FROM visits WHERE ended IS NULL AND expires>?',(now(),))['n'],heat=heatmap(),activity=rows("SELECT strftime('%Y-%m-%d %H:00',started,'unixepoch','+8 hours') hour,count(*) n FROM visits GROUP BY hour ORDER BY hour DESC LIMIT 168"))
@app.post('/operator/notice')
@role('operator')
def save_notice():
    title=request.form.get('title','').strip()[:120];body=request.form.get('body','').strip()[:5000]
    try: expires=int(datetime.fromisoformat(request.form['expires']).replace(tzinfo=timezone(timedelta(hours=8))).timestamp())
    except (ValueError,KeyError): abort(400,'Choose an expiry date and time.')
    if not title or not body or expires<=now(): abort(400,'Title, message, and future expiry are required.')
    values=(title,body,request.form.get('category','Community'),request.form.get('publisher','Community operator')[:100],source_url(request.form.get('source','')),expires,int(request.form.get('published')=='yes'),now())
    nid=request.form.get('id')
    if nid: execute('UPDATE notices SET title=?,body=?,category=?,publisher=?,source=?,expires=?,published=?,updated=? WHERE id=?',values+(int(nid),))
    else: execute('INSERT INTO notices(title,body,category,publisher,source,expires,published,updated) VALUES(?,?,?,?,?,?,?,?)',values)
    flash('Notice saved.');return redirect('/operator')
@app.get('/operator/notice/<int:nid>')
@role('operator')
def edit_notice(nid):
    n=one('SELECT * FROM notices WHERE id=?',(nid,))
    if not n: abort(404)
    return render_template('edit_notice.html',title='Edit notice',n=n,expiry=datetime.fromtimestamp(n['expires'],timezone(timedelta(hours=8))).strftime('%Y-%m-%dT%H:%M'))
@app.post('/operator/place')
@role('operator')
def save_place():
    values=tuple(request.form.get(k,'').strip()[:1000] for k in ['name','category','description','address','hours'])+(source_url(request.form.get('source','')),)
    if not values[0]: abort(400,'Name is required.')
    execute('INSERT INTO places(name,category,description,address,hours,source) VALUES(?,?,?,?,?,?)',values);flash('Listing added.');return redirect('/operator')
@app.get('/operator/export')
@role('operator')
def export():
    out=io.StringIO();w=csv.writer(out);w.writerow(['hour_MYT','portal_sessions_started'])
    for r in rows("SELECT strftime('%Y-%m-%d %H:00',started,'unixepoch','+8 hours') hour,count(*) n FROM visits GROUP BY hour"): w.writerow(tuple(r))
    return Response(out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=perkfi-usage.csv'})
@app.route('/merchant',methods=['GET','POST'])
@role('merchant')
def merchant():
    v=None
    if request.method=='POST':
        code=request.form.get('code','').strip().upper()
        v=one('SELECT * FROM vouchers WHERE code=? AND merchant=?',(code,g.user['merchant']))
        if not v: flash('Voucher not found for your merchant. Check the code.')
        elif request.form.get('action')=='redeem':
            cur=execute('UPDATE vouchers SET redeemed=?,staff=? WHERE code=? AND merchant=? AND redeemed IS NULL AND expires>?',(now(),g.user['id'],code,g.user['merchant'],now()))
            flash('Redemption confirmed.' if cur.rowcount else 'Voucher already redeemed or expired.')
            v=one('SELECT * FROM vouchers WHERE code=?',(code,))
    return render_template('merchant.html',title='Kopi Corner staff',v=v,now=now(),rewards=rows('SELECT * FROM rewards WHERE merchant=?',(g.user['merchant'],)),claimed=one('SELECT count(*) n FROM vouchers WHERE merchant=?',(g.user['merchant'],))['n'],redeemed=one('SELECT count(*) n FROM vouchers WHERE merchant=? AND redeemed IS NOT NULL',(g.user['merchant'],))['n'])
@app.post('/merchant/reward')
@role('merchant')
def save_reward():
    try: cost=int(request.form['cost']);stock=int(request.form['stock']);days=int(request.form['days'])
    except (ValueError,KeyError): abort(400)
    if not (0<cost<=100000 and 0<=stock<=10000 and 1<=days<=365): abort(400,'Check cost, stock and validity.')
    title=request.form.get('title','').strip()[:120]
    if not title: abort(400,'Reward title is required.')
    execute('INSERT INTO rewards(merchant,title,cost,stock,expires,terms) VALUES(?,?,?,?,?,?)',(g.user['merchant'],title,cost,stock,now()+days*86400,request.form.get('terms','')[:1000]));return redirect('/merchant')
@app.get('/privacy')
def privacy(): return page('Access terms and privacy','1. Service Provision & Session Limits \nPerkFi provides free, best-effort internet access. The service is not guaranteed to be uninterrupted, and bandwidth may be managed to ensure fair usage. The default session limit is set to two (2) hours of uninterrupted access, after which you may be required to re-authenticate. \n2. Acceptable Use \nYou agree not to use this network for illegal activities, to distribute malware, or to attempt to bypass network security controls. \n3. Anonymous Access & Data Minimization \nWe believe internet access should not require surrendering your privacy. You do not need to provide a phone number, email address, or create an account to access the basic WiFi and view public community information. \n4. Network Session Data \nWe record anonymous network session data (connection time, duration, and device MAC address) solely for network authorization, security, and generating aggregated, anonymized foot-traffic heatmaps for the host operator. This data is never linked to your personal identity in operator reports. \n5. Optional Accounts & Rewards (PDPA 2010 Compliance) \nIf you choose to register for the optional loyalty program, we collect your email or phone number to verify your identity and secure your account. Points transactions and voucher claims are recorded to prevent fraud and resolve disputes. Merchant staff can only view the specific voucher status, not your overall profile. We will never sell your contact information to third-party data brokers. \n6. Marketing Preferences \nMarketing consent is strictly opt-in. You will only receive promotional updates if you actively check the marketing consent box during registration. Every voucher or promotional message includes a one-click opt-out mechanism. \n7. Prototype Disclaimer \nPlease note that this application is a university prototype. Any accounts created, vouchers claimed, or sessions recorded during this demonstration are simulated for academic purposes and will be purged post-demonstration.')
@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(429)
def error(e): return page('Unable to complete request',str(e.description)), e.code

@app.cli.command('init-db')
def init_command():
    init_db()
    print('Database ready. Existing records preserved.')
    for contact,role_name,merchant_id in [('operator@perkfi.local','operator',None),('merchant@perkfi.local','merchant',1)]:
        if not one('SELECT id FROM users WHERE contact=?',(contact,)):
            password=secrets.token_urlsafe(12)
            execute('INSERT INTO users(contact,role,password,merchant) VALUES(?,?,?,?)',(contact,role_name,generate_password_hash(password),merchant_id))
            print(f'{role_name}: {contact}  password: {password}')
@app.cli.command('reset-staff')
def reset_staff():
    import click
    contact=click.prompt('Staff email').strip().lower()
    u=one("SELECT id FROM users WHERE contact=? AND role IN ('operator','merchant')",(contact,))
    if not u: raise click.ClickException('Staff account not found. Run init-db first.')
    password=click.prompt('New password (12+ characters)',hide_input=True,confirmation_prompt=True)
    if len(password)<12: raise click.ClickException('Use at least 12 characters.')
    execute('UPDATE users SET password=? WHERE id=?',(generate_password_hash(password),u['id']))
    print('Staff password updated.')

if __name__=='__main__':
    with app.app_context(): init_db()
    from waitress import serve
    serve(app,host='127.0.0.1',port=int(os.getenv('PORT','5000')))
