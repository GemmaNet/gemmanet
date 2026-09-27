# Privacy Policy

**Last updated: September 2026**

## 1. Introduction

This Privacy Policy describes what GemmaNet ("we", "us"), the operator of the
network at gemmanet.net, collects when you use it, what happens to the
content of your requests, and how long we keep what.

## 2. Who Sees Your Requests

GemmaNet does not run a model of its own: the coordinator forwards each
request (its content and parameters) to **one node** that offers the requested
capability, and relays that node's answer back to you.

- **Official nodes** are run by us, on our server. They process requests in
  memory and do not store their content.
- **Community nodes** are run by independent operators: anyone with an API key
  can connect one. **The operator of the node that serves your request can see
  its content and the result.** Community operators are not bound by this
  policy and may log or keep what they process.

To keep a request on official nodes, ask for it: `"trust": "official"` in the
request body (or the `X-GemmaNet-Trust: official` header; `trust="official"` in
the Python SDK; `extra_body={"trust": "official"}` with the OpenAI SDK). If no
official node can take the request, it fails instead of going to a community
node. The dashboard shows each node's tier. Without this option, any node
may serve a request.

## 3. What We Collect

- **Account**: your API key, stored only as a SHA-256 hash; the email address
  you optionally give at registration; when the key was created and last used.
- **Nodes**: name, capabilities, languages, model information and tier of each
  node you connect, and the list of node ids your account has registered (so
  deleting the account can find them).
- **Reputation**: per node, the number of tasks, success rate, response times and
  user ratings. Which account requested a task and which node served it is kept
  for one hour, so the requester can rate it.
- **Usage**: the number of completed tasks per day (a single counter).
- **Feedback**: the messages you send us and the optional email address.
- **Forum**: posts, replies and the usernames you choose; these are public. For
  votes we store a keyed hash of your IP address (not the address itself) for
  30 days, which allows one vote per visitor and post. Rate limits hold IP
  addresses in memory for up to one hour.
- **Server logs**: every HTTP request to the coordinator is logged with the
  client IP address, time, method, path and status code. The logs are rotated
  by size (at most 3 × 20 MB per service), so older entries are overwritten;
  they are used only to run and protect the service.

## 4. What We Do Not Store

- **Request and response content**: the coordinator passes it between you and
  the serving node in memory and does not log or store it. (For what the
  serving node may do, see section 2.)
- **Tracking data**: we use no analytics, advertising or tracking tools.

## 5. How We Use Your Data

- **Routing**: to match requests with suitable, reliable nodes
- **Reputation**: to rank nodes by reliability and quality
- **Account management**: to authenticate your API key and run your nodes
- **Operations and security**: to monitor the platform, fix problems and fend off abuse

## 6. Where Data Is Stored

The coordinator and its databases run on our server at Google Cloud. All
traffic to gemmanet.net passes through Cloudflare, which also hosts the
website and documentation; Cloudflare processes requests under its own privacy
policy. API keys are stored only as hashes.

## 7. Data Sharing

We do not sell your data. Besides the node that serves each request (section
2) and the providers named in section 6, we share data only if required by law
or to protect the rights and safety of our users and platform. We may publish
aggregate statistics that identify no one.

## 8. Data Retention and Account Deletion

Account data is kept until you delete the account. You can do that yourself at
any time, with the API key:

```bash
curl -X DELETE https://api.gemmanet.net/api/v1/account -H "Authorization: Bearer $API_KEY"
```

(or `client.delete_account()` in the Python SDK). This immediately deletes all
API keys of the account and the email address, the feedback sent with the key,
and the reputation and benchmark data of the account's nodes, and disconnects
its nodes. The one-hour task records expire on their own. Copies in server
backups disappear as the backups roll over, within 30 days.

Forum posts are not linked to accounts; to have one removed, write to us.
Server logs and forum vote hashes are kept as described in section 3; the daily
task counter is aggregate and kept.

## 9. Your Rights

You may:

- **Delete** your account and associated data (section 8)
- **Request access** to your personal data, or an **export** in a portable format
- **Request correction** or deletion of anything else we hold about you
- **Opt out** of non-essential communications

For anything but self-service deletion, contact us at **contact@gemmanet.net**.

## 10. Cookies and Browser Storage

We set no cookies. The dashboard keeps the API key you type into it in your
browser's `localStorage`, on your device only, so you don't have to paste it
again; empty the field or clear the site's data in your browser to remove it.
Cloudflare may set cookies it needs for security (such as bot protection).

## 11. Changes to This Policy

We may update this Privacy Policy from time to time. We will notify registered
users of material changes.

## 12. Contact

For questions about this Privacy Policy, contact us at: **contact@gemmanet.net**
