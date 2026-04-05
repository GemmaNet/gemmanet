import sqlite3
import os

DB_PATH = os.getenv('FORUM_DB', 'forum.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_forum_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT DEFAULT 'anon',
            content TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            upvotes INTEGER DEFAULT 0,
            reply_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            username TEXT DEFAULT 'anon',
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (post_id) REFERENCES posts(id)
        );
        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            voter_ip TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(post_id, voter_ip)
        );
    ''')
    conn.commit()
    conn.close()


def seed_forum_db():
    conn = get_db()
    count = conn.execute('SELECT COUNT(*) FROM posts').fetchone()[0]
    if count > 0:
        conn.close()
        return

    seeds = [
        ('techfan', 'What open-source LLM are you running locally in 2026? I switched from Llama 3 to Gemma 4 last week and the quality improvement on reasoning tasks is significant. Curious what others are using.', 'ai', 12, '-1 hours'),
        ('builder', 'Hot take: most AI wrapper startups will fail not because of technology but because they have no distribution moat. The only defensible position is owning the network effect.', 'dev', 8, '-3 hours'),
        ('curious', 'Ask: What is the cheapest way to serve a 7B model for production use? I need about 1000 requests per day. Cloud GPU, own hardware, or distributed inference?', 'ask', 15, '-6 hours'),
        ('observer', 'The pace of open model releases in 2026 is insane. We went from GPT-4 being unreachable to multiple open models matching it in under 2 years. Competition is working.', 'general', 5, '-12 hours'),
        ('maker', 'Show: I built a CLI tool that benchmarks any GGUF model on your hardware in 60 seconds. Tests throughput, latency, and quality on 10 standard prompts. Open source.', 'show', 3, '-24 hours'),
    ]

    for username, content, category, upvotes, time_offset in seeds:
        conn.execute(
            "INSERT INTO posts (username, content, category, upvotes, reply_count, created_at) "
            "VALUES (?, ?, ?, ?, 0, datetime('now', ?))",
            (username, content, category, upvotes, time_offset)
        )

    # Update reply counts for posts that will get replies
    conn.execute("UPDATE posts SET reply_count = 2 WHERE id = 1")
    conn.execute("UPDATE posts SET reply_count = 1 WHERE id = 3")

    replies = [
        (1, 'mldev', 'Gemma 4 E4B is my daily driver. Fits in 3GB with Q4 quantization. The 140+ language support is a game changer for my translation project.', '-45 minutes'),
        (1, 'coder42', 'Still on Qwen 3.5 for coding tasks. Nothing beats it for Python generation in my experience. But Gemma 4 is better for general reasoning.', '-30 minutes'),
        (3, 'tinkerer', 'For 1000 req/day a Raspberry Pi 5 with Gemma 4 E2B could actually work. Latency around 5s per request but the cost is basically zero after hardware.', '-4 hours'),
    ]

    for post_id, username, content, time_offset in replies:
        conn.execute(
            "INSERT INTO replies (post_id, username, content, created_at) "
            "VALUES (?, ?, ?, datetime('now', ?))",
            (post_id, username, content, time_offset)
        )

    conn.commit()
    conn.close()
