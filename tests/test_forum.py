"""Forum: escaping, client IP handling, and safe redirects."""
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
    assert conn.execute('PRAGMA user_version').fetchone()[0] == 1
    conn.close()


def test_forwarded_for_header_cannot_bypass_rate_limit(client):
    for i in range(3):
        assert post(client, f'post {i}', **{'X-Forwarded-For': f'10.0.0.{i}'}).status_code == 303
    assert post(client, 'one more', **{'X-Forwarded-For': '10.0.0.99'}).status_code == 429


def test_upvote_redirect_stays_on_forum(client):
    post(client, 'hello')
    evil = client.post('/upvote/1', headers={'referer': 'https://evil.example/talk/x'},
                       follow_redirects=False)
    assert evil.headers['location'] == '/talk/'
    outside = client.post('/upvote/1', headers={'referer': 'http://testserver/dashboard/'},
                          follow_redirects=False)
    assert outside.headers['location'] == '/talk/'
    back = client.post('/upvote/1', headers={'referer': 'http://testserver/talk/post/1?x=1'},
                       follow_redirects=False)
    assert back.headers['location'] == '/talk/post/1?x=1'
