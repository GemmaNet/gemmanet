"""Forum: escaping, client IP handling, and safe redirects."""
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

import gemmanet.forum.app as forum
import gemmanet.forum.database as forum_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(forum_db, 'DB_PATH', str(tmp_path / 'forum.db'))
    forum_db.init_forum_db()
    for store in (forum._rate_posts, forum._rate_replies, forum._rate_votes):
        store.clear()
    return TestClient(forum.forum_app)


def post(client, content, username='', **headers):
    return client.post('/submit', data={'content': content, 'username': username},
                       headers=headers, follow_redirects=False)


def test_post_is_escaped_exactly_once(client):
    r = post(client, 'Tom & Jerry say "hi" <b>bold</b>', username='a&b')
    assert r.status_code == 303
    page = client.get(r.headers['location'].removeprefix('/talk')).text
    assert 'Tom &amp; Jerry say &quot;hi&quot; bold' in page
    assert 'by a&amp;b' in page
    assert '&amp;amp;' not in page
    recent = client.get('/api/recent').json()
    assert recent == [] or all('&amp;' not in p['content'] for p in recent)


def test_migration_unescapes_legacy_rows(tmp_path, monkeypatch):
    path = tmp_path / 'legacy.db'
    conn = sqlite3.connect(path)
    conn.executescript('''
        CREATE TABLE posts (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT DEFAULT 'anon',
            content TEXT NOT NULL, category TEXT DEFAULT 'general', upvotes INTEGER DEFAULT 0,
            reply_count INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE replies (id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER NOT NULL,
            username TEXT DEFAULT 'anon', content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO posts (username, content) VALUES ('a&amp;b', 'x &amp; y &lt;3');
        INSERT INTO replies (post_id, username, content) VALUES (1, 'r', 'fish &amp; chips');
    ''')
    conn.commit()
    conn.close()
    monkeypatch.setattr(forum_db, 'DB_PATH', str(path))

    forum_db.init_forum_db()
    forum_db.init_forum_db()  # second run must not unescape again

    conn = sqlite3.connect(path)
    assert conn.execute('SELECT username, content FROM posts').fetchone() == ('a&b', 'x & y <3')
    assert conn.execute('SELECT content FROM replies').fetchone() == ('fish & chips',)
    assert conn.execute('PRAGMA user_version').fetchone()[0] == 2  # latest
    conn.close()


def test_forwarded_for_header_cannot_bypass_rate_limit(client):
    for i in range(3):
        assert post(client, f'post {i}', **{'X-Forwarded-For': f'10.0.0.{i}'}).status_code == 303
    assert post(client, 'one more', **{'X-Forwarded-For': '10.0.0.99'}).status_code == 429


def test_upvote_redirect_stays_on_forum(client):
    post(client, 'hello')
    outside = client.post('/upvote/1', headers={'referer': 'http://testserver/dashboard/'},
                          follow_redirects=False)
    assert outside.headers['location'] == '/talk/'
    back = client.post('/upvote/1', headers={'referer': 'http://testserver/talk/post/1?x=1'},
                       follow_redirects=False)
    assert back.headers['location'] == '/talk/post/1?x=1'


def test_home_link_follows_site_url(client, monkeypatch):
    assert 'href="/"' in client.get('/').text
    monkeypatch.setenv('GEMMANET_SITE_URL', 'https://gemmanet.net')
    page = client.get('/').text
    assert page.count('href="https://gemmanet.net"') == 2  # header and footer
    assert 'href="/"' not in page


@pytest.mark.parametrize('headers', [
    {'Origin': 'https://evil.example'},
    {'Origin': 'null'},                                     # origin withheld
    {'Referer': 'https://evil.example/page'},               # no Origin: Referer decides
    {'Origin': 'https://evil.example', 'Referer': 'http://testserver/talk/'},
])
def test_cross_origin_posts_are_refused(client, headers):
    assert post(client, 'forged', **headers).status_code == 403
    assert client.post('/reply/1', data={'content': 'forged'}, headers=headers).status_code == 403
    assert client.post('/upvote/1', headers=headers).status_code == 403
    assert 'forged' not in client.get('/').text


def test_same_origin_posts_are_accepted(client):
    assert post(client, 'from the forum page', Origin='http://testserver').status_code == 303
    assert post(client, 'from a referer', Referer='http://testserver/talk/new').status_code == 303
    assert post(client, 'from curl').status_code == 303       # no Origin/Referer at all


def test_pages_forbid_inline_scripts(client):
    post(client, 'hello')
    for path in ('/', '/new', '/post/1'):
        resp = client.get(path)
        policy = resp.headers['content-security-policy']
        assert "script-src 'self';" in policy and "frame-ancestors 'none'" in policy
        # Anything inline would be blocked by that policy, so there must be none.
        assert not re.search(r'<script(?![^>]*\ssrc=)[^>]*>', resp.text)
        assert not re.search(r'\son[a-z]+\s*=', resp.text)
    assert '<script src="/talk/forum.js"></script>' in client.get('/new').text


def test_counter_script_is_served(client):
    resp = client.get('/forum.js')
    assert resp.headers['content-type'].startswith('text/javascript')
    assert "data-counter" in resp.text or 'dataset.counter' in resp.text
    assert 'data-counter="cc"' in client.get('/new').text


def _votes(path=None):
    conn = sqlite3.connect(path or forum_db.DB_PATH)
    rows = conn.execute('SELECT post_id, voter_ip FROM votes').fetchall()
    conn.close()
    return rows


def test_votes_store_no_ip_address_and_still_dedupe(client, monkeypatch):
    monkeypatch.setenv('ADMIN_KEY', 'k1')
    post(client, 'vote for me')
    for _ in range(2):
        client.post('/upvote/1')
    [(_, stored)] = _votes()
    assert re.fullmatch(r'[0-9a-f]{64}', stored)
    assert 'testclient' not in stored          # TestClient's client address
    assert stored == forum_db.voter_id('testclient')
    assert client.get('/api/recent').json()[0]['upvotes'] == 1


def test_voter_ids_depend_on_a_server_secret(monkeypatch):
    monkeypatch.delenv('FORUM_IP_SECRET', raising=False)
    monkeypatch.setenv('ADMIN_KEY', 'one')
    first = forum_db.voter_id('203.0.113.9')
    monkeypatch.setenv('ADMIN_KEY', 'two')
    assert forum_db.voter_id('203.0.113.9') != first
    monkeypatch.setenv('FORUM_IP_SECRET', 'dedicated')
    dedicated = forum_db.voter_id('203.0.113.9')
    monkeypatch.setenv('ADMIN_KEY', 'three')
    assert forum_db.voter_id('203.0.113.9') == dedicated   # FORUM_IP_SECRET wins


def test_migration_hashes_stored_ip_addresses(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_KEY', 'k1')
    path = tmp_path / 'v1.db'
    monkeypatch.setattr(forum_db, 'DB_PATH', str(path))
    forum_db.init_forum_db()
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA user_version = 1')
    conn.execute("INSERT INTO posts (content, upvotes) VALUES ('old', 1)")
    conn.execute("INSERT INTO votes (post_id, voter_ip) VALUES (1, 'testclient')")
    conn.commit()
    conn.close()

    forum_db.init_forum_db()
    forum_db.init_forum_db()  # idempotent
    assert _votes(path) == [(1, forum_db.voter_id('testclient'))]
    # The migrated vote still counts: the same visitor cannot vote again.
    TestClient(forum.forum_app).post('/upvote/1')
    conn = sqlite3.connect(path)
    assert conn.execute('SELECT upvotes FROM posts').fetchone() == (1,)
    assert conn.execute('PRAGMA user_version').fetchone() == (2,)
    conn.close()


def test_votes_are_forgotten_after_30_days(client):
    post(client, 'old post')
    conn = sqlite3.connect(forum_db.DB_PATH)
    conn.execute("INSERT INTO votes (post_id, voter_ip, created_at) "
                 "VALUES (1, 'a', datetime('now', '-31 days'))")
    conn.execute("INSERT INTO votes (post_id, voter_ip, created_at) "
                 "VALUES (1, 'b', datetime('now', '-29 days'))")
    conn.commit()
    conn.close()
    client.post('/upvote/1')
    assert sorted(v for _, v in _votes()) == sorted(['b', forum_db.voter_id('testclient')])
