from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from datetime import datetime, timezone
from collections import defaultdict
import math
import time
import html
import re

from gemmanet.forum.database import get_db

forum_app = FastAPI()

# Rate limiting: {ip: [timestamps]}
_rate_posts = defaultdict(list)
_rate_replies = defaultdict(list)
_rate_votes = defaultdict(list)

CATEGORIES = ('general', 'ai', 'dev', 'ask', 'show')
POSTS_PER_PAGE = 30

CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #fafafa; color: #111; font: 14px/1.6 Menlo, Consolas, monospace; max-width: 700px; margin: 0 auto; padding: 12px; }
a { color: #666; text-decoration: none; }
a:hover { text-decoration: underline; }
.header { margin-bottom: 8px; }
.header a { color: #111; font-size: 18px; font-weight: bold; }
.nav { margin-bottom: 16px; }
.nav a { margin-right: 12px; color: #666; }
.nav a.active { color: #111; font-weight: bold; }
.post-row { margin-bottom: 10px; line-height: 1.5; }
.arrow { color: #ff6600; cursor: pointer; font-size: 12px; margin-right: 4px; }
.arrow:hover { color: #ff8800; }
.score { color: #ff6600; margin-right: 6px; font-size: 12px; }
.meta { color: #999; font-size: 12px; }
.content { white-space: pre-wrap; word-wrap: break-word; margin: 12px 0; }
.reply { margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px solid #eee; }
textarea { width: 100%; font: 14px/1.6 Menlo, Consolas, monospace; background: #fff; border: 1px solid #ddd; padding: 8px; resize: vertical; }
input, select { font: 14px Menlo, Consolas, monospace; background: #fff; border: 1px solid #ddd; padding: 4px 8px; }
button { font: 14px Menlo, Consolas, monospace; background: #111; color: #fafafa; border: none; padding: 6px 16px; cursor: pointer; }
button:hover { background: #333; }
.counter { color: #999; font-size: 12px; }
.footer { margin-top: 24px; color: #999; font-size: 12px; }
.cat { color: #999; font-size: 11px; margin-right: 6px; }
"""


def _get_ip(request: Request) -> str:
    forwarded = request.headers.get('x-forwarded-for')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.client.host if request.client else '0.0.0.0'


def _check_rate(store: dict, ip: str, max_count: int, window: int = 3600) -> bool:
    now = time.time()
    store[ip] = [t for t in store[ip] if now - t < window]
    if len(store[ip]) >= max_count:
        return False
    store[ip].append(now)
    return True


def sanitize(text: str) -> str:
    return html.escape(text, quote=True)


def time_ago(created_at: str) -> str:
    try:
        dt = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        return '?'
    dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f'{seconds}s'
    minutes = seconds // 60
    if minutes < 60:
        return f'{minutes}m'
    hours = minutes // 60
    if hours < 24:
        return f'{hours}h'
    days = hours // 24
    if days < 7:
        return f'{days}d'
    weeks = days // 7
    return f'{weeks}w'


def hours_age(created_at: str) -> float:
    try:
        dt = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        return 1.0
    dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    return max(delta.total_seconds() / 3600, 0)


def calculate_score(upvotes: int, hours: float) -> float:
    return upvotes / math.pow(hours + 2, 1.5)


def render_page(title: str, body: str) -> HTMLResponse:
    h = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} - Talk</title><style>{CSS}</style></head><body>
<div class="header" style="display:flex;justify-content:space-between;align-items:center;"><a href="/talk/">Talk</a><a href="/" style="color:#999;font-size:14px;">GemmaNet</a></div>
<div class="nav"><a href="/talk/">New</a> <a href="/talk/?sort=top">Top</a> <a href="/talk/?sort=ask">Ask</a></div>
{body}
<div class="footer">Text only. No images. No distractions. | <a href="/">GemmaNet</a></div>
</body></html>"""
    return HTMLResponse(h)


@forum_app.get('/api/recent')
async def api_recent():
    conn = get_db()
    rows = conn.execute(
        'SELECT * FROM posts WHERE upvotes > 0 ORDER BY created_at DESC LIMIT 5'
    ).fetchall()
    result = []
    for r in rows:
        result.append({
            'id': r['id'],
            'content': r['content'][:100],
            'category': r['category'],
            'username': r['username'],
            'upvotes': r['upvotes'],
            'reply_count': r['reply_count'],
            'time_ago': time_ago(r['created_at']),
        })
    conn.close()
    return JSONResponse(result)


@forum_app.get('/', response_class=HTMLResponse)
async def front_page(sort: str = '', page: int = 1):
    conn = get_db()
    offset = (max(1, page) - 1) * POSTS_PER_PAGE

    if sort == 'top':
        rows = conn.execute(
            'SELECT * FROM posts ORDER BY upvotes DESC LIMIT ? OFFSET ?',
            (POSTS_PER_PAGE, offset)
        ).fetchall()
    elif sort == 'ask':
        rows = conn.execute(
            "SELECT * FROM posts WHERE category = 'ask' ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (POSTS_PER_PAGE, offset)
        ).fetchall()
    else:
        rows = conn.execute(
            'SELECT * FROM posts ORDER BY created_at DESC LIMIT ? OFFSET ?',
            (POSTS_PER_PAGE, offset)
        ).fetchall()

    # For default (no sort), apply gravity ranking
    if sort not in ('top', 'ask'):
        rows = sorted(rows, key=lambda r: calculate_score(r['upvotes'], hours_age(r['created_at'])), reverse=True)

    items = ''
    for r in rows:
        preview = sanitize(r['content'][:100])
        ago = time_ago(r['created_at'])
        cat = f'<span class="cat">[{sanitize(r["category"])}]</span>' if r['category'] != 'general' else ''
        items += f"""<div class="post-row">
<form method="post" action="/talk/upvote/{r['id']}" style="display:inline">
<button type="submit" class="arrow" title="upvote">&#9650;</button></form>
<span class="score">{r['upvotes']}</span>
{cat}<a href="/talk/post/{r['id']}">{preview}</a>
<span class="meta">by {sanitize(r['username'])} {ago} | <a href="/talk/post/{r['id']}">{r['reply_count']} replies</a></span>
</div>\n"""

    nav = ''
    if len(rows) >= POSTS_PER_PAGE:
        qs = f'sort={sort}&' if sort else ''
        nav = f'<div style="margin-top:16px"><a href="/talk/?{qs}page={page+1}">More</a></div>'

    body = items + nav + '<div style="margin-top:16px"><a href="/talk/new">Post</a></div>'
    conn.close()
    return render_page('Talk', body)


@forum_app.get('/post/{post_id}', response_class=HTMLResponse)
async def post_detail(post_id: int):
    conn = get_db()
    post = conn.execute('SELECT * FROM posts WHERE id = ?', (post_id,)).fetchone()
    if not post:
        conn.close()
        raise HTTPException(status_code=404, detail='Post not found')

    ago = time_ago(post['created_at'])
    body = f"""<div>
<form method="post" action="/talk/upvote/{post['id']}" style="display:inline">
<button type="submit" class="arrow" title="upvote">&#9650;</button></form>
<span class="score">{post['upvotes']}</span>
<span class="cat">[{sanitize(post['category'])}]</span>
<span class="meta">by {sanitize(post['username'])} {ago}</span>
</div>
<div class="content">{sanitize(post['content'])}</div>
<div style="margin-bottom:16px;color:#999;font-size:12px">{post['reply_count']} replies</div>
"""

    replies = conn.execute(
        'SELECT * FROM replies WHERE post_id = ? ORDER BY created_at ASC', (post_id,)
    ).fetchall()

    for r in replies:
        rago = time_ago(r['created_at'])
        body += f"""<div class="reply">
<span class="meta">{sanitize(r['username'])} {rago}</span>
<div class="content">{sanitize(r['content'])}</div>
</div>\n"""

    body += f"""<div style="margin-top:16px">
<form method="post" action="/talk/reply/{post_id}">
<textarea name="content" rows="3" maxlength="300" placeholder="Reply (max 300 chars)" oninput="document.getElementById('rc').textContent=this.value.length+'/300'"></textarea>
<span class="counter" id="rc">0/300</span><br>
<input name="username" placeholder="username (optional)" maxlength="30" style="margin:4px 0">
<button type="submit">Reply</button>
</form></div>"""

    conn.close()
    return render_page(f'Post #{post_id}', body)


@forum_app.get('/new', response_class=HTMLResponse)
async def compose():
    opts = ''.join(f'<option value="{c}">{c.title()}</option>' for c in CATEGORIES)
    body = f"""<div style="margin-bottom:8px">No images. No links. Just text.</div>
<form method="post" action="/talk/submit">
<textarea name="content" rows="6" maxlength="500" placeholder="What's on your mind? (max 500 chars)" oninput="document.getElementById('cc').textContent=this.value.length+'/500'"></textarea>
<span class="counter" id="cc">0/500</span><br>
<input name="username" placeholder="username (optional)" maxlength="30" style="margin:4px 0">
<select name="category" style="margin:4px 0">{opts}</select>
<button type="submit">Post</button>
</form>"""
    return render_page('New Post', body)


@forum_app.post('/submit')
async def submit_post(request: Request, content: str = Form(...), username: str = Form(''), category: str = Form('general')):
    ip = _get_ip(request)
    if not _check_rate(_rate_posts, ip, 3):
        raise HTTPException(status_code=429, detail='Rate limit: max 3 posts per hour')

    content = content.strip()
    if not content or len(content) > 500:
        raise HTTPException(status_code=400, detail='Content must be 1-500 characters')

    username = username.strip() or 'anon'
    if len(username) > 30:
        raise HTTPException(status_code=400, detail='Username max 30 characters')

    if category not in CATEGORIES:
        category = 'general'

    content = re.sub(r'<[^>]+>', '', content)
    content = html.escape(content)
    username = re.sub(r'<[^>]+>', '', username)
    username = html.escape(username)

    conn = get_db()
    cur = conn.execute(
        'INSERT INTO posts (username, content, category) VALUES (?, ?, ?)',
        (username, content, category)
    )
    post_id = cur.lastrowid
    conn.commit()
    conn.close()
    return RedirectResponse(url=f'/talk/post/{post_id}', status_code=303)


@forum_app.post('/reply/{post_id}')
async def submit_reply(post_id: int, request: Request, content: str = Form(...), username: str = Form('')):
    ip = _get_ip(request)
    if not _check_rate(_rate_replies, ip, 10):
        raise HTTPException(status_code=429, detail='Rate limit: max 10 replies per hour')

    content = content.strip()
    if not content or len(content) > 300:
        raise HTTPException(status_code=400, detail='Reply must be 1-300 characters')

    username = username.strip() or 'anon'
    if len(username) > 30:
        username = username[:30]

    content = re.sub(r'<[^>]+>', '', content)
    content = html.escape(content)
    username = re.sub(r'<[^>]+>', '', username)
    username = html.escape(username)

    conn = get_db()
    post = conn.execute('SELECT id FROM posts WHERE id = ?', (post_id,)).fetchone()
    if not post:
        conn.close()
        raise HTTPException(status_code=404, detail='Post not found')

    conn.execute(
        'INSERT INTO replies (post_id, username, content) VALUES (?, ?, ?)',
        (post_id, username, content)
    )
    conn.execute('UPDATE posts SET reply_count = reply_count + 1 WHERE id = ?', (post_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f'/talk/post/{post_id}', status_code=303)


@forum_app.post('/upvote/{post_id}')
async def upvote(post_id: int, request: Request):
    ip = _get_ip(request)
    if not _check_rate(_rate_votes, ip, 30):
        raise HTTPException(status_code=429, detail='Rate limit: max 30 votes per hour')

    conn = get_db()
    existing = conn.execute(
        'SELECT id FROM votes WHERE post_id = ? AND voter_ip = ?', (post_id, ip)
    ).fetchone()

    if not existing:
        try:
            conn.execute(
                'INSERT INTO votes (post_id, voter_ip) VALUES (?, ?)', (post_id, ip)
            )
            conn.execute('UPDATE posts SET upvotes = upvotes + 1 WHERE id = ?', (post_id,))
            conn.commit()
        except Exception:
            pass

    conn.close()
    referer = request.headers.get('referer', '/talk/')
    return RedirectResponse(url=referer, status_code=303)
