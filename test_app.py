import pytest,secrets
from app import app,init_db,db,now
from werkzeug.security import generate_password_hash
@pytest.fixture
def client(tmp_path):
    app.config.update(TESTING=True,DATABASE=str(tmp_path/'test.db'))
    with app.app_context():
        init_db()
        db().execute("INSERT INTO users(id,contact,role,password,merchant) VALUES(1,'member@example.com','member',NULL,NULL),(2,'operator@perkfi.local','operator',?,NULL),(3,'merchant@perkfi.local','merchant',?,1),(4,'other@example.com','member',NULL,NULL)",(generate_password_hash('test-password'),generate_password_hash('test-password')))
        db().execute("INSERT INTO ledger(user,merchant,amount,reason,ts) VALUES(1,1,120,'test',?)",(now(),));db().commit()
    return app.test_client()
def auth(c,uid=1):
    c.get('/')
    with c.session_transaction() as s:s['uid']=uid;return s['csrf']
def test_pages_and_permissions(client):
    for path in ['/','/home','/explore','/place/1','/notices','/notice/1','/transport','/rewards','/login','/staff','/privacy']:
        assert client.get(path).status_code==200,path
    auth(client)
    assert client.get('/operator').status_code==403
    assert client.get('/merchant').status_code==403
    for path in ['/account','/claim/1']:assert client.get(path).status_code==200
    auth(client,2)
    for path in ['/operator','/operator/notice/1','/operator/export']:assert client.get(path).status_code==200,path
    auth(client,3);assert client.get('/merchant').status_code==200

def test_claim_redeem_and_isolation(client):
    csrf=auth(client);client.get('/claim/1')
    with client.session_transaction() as s:key=s['claim_key']
    result=client.post('/claim/1',data={'csrf':csrf,'claim_key':key});code=result.location.split('/')[-1]
    assert result.status_code==302
    # Retrying the exact POST does not charge twice.
    assert client.post('/claim/1',data={'csrf':csrf,'claim_key':key}).location==result.location
    with app.app_context():assert db().execute('SELECT sum(amount) FROM ledger WHERE user=1').fetchone()[0]==40
    assert client.get(result.location).status_code==200
    auth(client,4);assert client.get(result.location).status_code==404
    csrf=auth(client,3)
    assert b'Valid for Kopi Corner' in client.post('/merchant',data={'csrf':csrf,'code':code}).data
    assert b'Redemption confirmed' in client.post('/merchant',data={'csrf':csrf,'code':code,'action':'redeem'}).data
    assert b'already redeemed or expired' in client.post('/merchant',data={'csrf':csrf,'code':code,'action':'redeem'}).data

def test_expiry_and_funds(client):
    csrf=auth(client)
    with app.app_context():db().execute('UPDATE rewards SET expires=?',(now()-1,));db().commit()
    client.get('/claim/1')
    with client.session_transaction() as s:key=s['claim_key']
    client.post('/claim/1',data={'csrf':csrf,'claim_key':key})
    with app.app_context():assert db().execute('SELECT count(*) FROM vouchers').fetchone()[0]==0

def test_session_csrf_and_notice(client):
    csrf=auth(client)
    assert client.post('/connect').status_code==400
    client.post('/connect',data={'csrf':csrf,'terms':'yes'})
    client.post('/connect',data={'csrf':csrf,'terms':'yes'})
    with app.app_context():assert db().execute('SELECT count(*) FROM visits').fetchone()[0]==1
    client.post('/disconnect',data={'csrf':csrf})
    assert b'Browse as a guest' in client.get('/home').data
    csrf=auth(client,2)
    client.post('/operator/notice',data={'csrf':csrf,'title':'Draft test','body':'Body','expires':'2099-01-01T10:00'})
    with app.app_context():nid=db().execute('SELECT max(id) FROM notices').fetchone()[0]
    auth(client,1);assert client.get(f'/notice/{nid}').status_code==404

def test_api_failure(monkeypatch,client):
    import integrations
    integrations.CACHE.clear()
    def fail(*args,**kwargs):raise RuntimeError('offline')
    monkeypatch.setattr(integrations,'fetch',fail)
    assert client.get('/api/weather').json['status']=='unavailable'
    assert client.get('/api/transport').json['status']=='unavailable'

def test_voucher_qr_status_and_redemption(client):
    # An anonymous visitor must receive a JSON authentication error.
    response = client.get('/api/vouchers/KC-000000000000/status')
    assert response.status_code == 401

    # Claim a voucher as the member seeded by the existing test fixture.
    csrf = auth(client, 1)
    client.get('/claim/1')

    with client.session_transaction() as session_data:
        claim_key = session_data['claim_key']

    response = client.post(
        '/claim/1',
        data={'csrf': csrf, 'claim_key': claim_key}
    )

    assert response.status_code == 302
    code = response.location.rsplit('/', 1)[-1]

    qr_url = f'/voucher/{code}/qr.svg'
    status_url = f'/api/vouchers/{code}/status'

    # The owner can view the QR and its current status.
    response = client.get(qr_url)
    assert response.status_code == 200
    assert response.mimetype == 'image/svg+xml'
    assert b'<svg' in response.data
    assert 'no-store' in response.headers['Cache-Control']
    assert client.get(status_url).json['status'] == 'ready'

    # Another member cannot retrieve either resource.
    auth(client, 4)
    assert client.get(qr_url).status_code == 404
    assert client.get(status_url).status_code == 404

    # An expired voucher must stop producing a QR image.
    auth(client, 1)
    with app.app_context():
        db().execute(
            'UPDATE vouchers SET expires=? WHERE code=?',
            (now() - 1, code)
        )
        db().commit()

    assert client.get(status_url).json['status'] == 'expired'
    assert client.get(qr_url).status_code == 410

    # Restore validity to exercise merchant redemption.
    with app.app_context():
        db().execute(
            'UPDATE vouchers SET expires=? WHERE code=?',
            (now() + 3600, code)
        )
        db().commit()

    csrf = auth(client, 3)

    # Checking a code does not redeem it.
    client.post('/merchant', data={'csrf': csrf, 'code': code})
    with app.app_context():
        voucher = db().execute(
            'SELECT redeemed FROM vouchers WHERE code=?', (code,)
        ).fetchone()
        assert voucher['redeemed'] is None

    # Staff must explicitly confirm redemption.
    response = client.post(
        '/merchant',
        data={'csrf': csrf, 'code': code, 'action': 'redeem'}
    )
    assert b'Redemption confirmed' in response.data

    # A repeat redemption is rejected.
    response = client.post(
        '/merchant',
        data={'csrf': csrf, 'code': code, 'action': 'redeem'}
    )
    assert b'already redeemed or expired' in response.data

    # The customer's status now reflects redemption.
    auth(client, 1)
    response = client.get(status_url)
    assert response.json['status'] == 'redeemed'
    assert response.json['redeemed_at'] is not None
    assert client.get(qr_url).status_code == 410