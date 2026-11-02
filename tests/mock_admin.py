"""A fake EVA Admin that copies the structure of the real admin.eva.gg pages (recorded 2 Nov 2026):
list table with data-booking rows, "Scope" menu button, date-range menu, '»' paging links, booking page titled
'Order#…', 7-cell booking row, 'Arena' label/value, 'View n/m attendees' pop-up, and the Player information page."""
import json, threading, urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BAHRAIN = timezone(timedelta(hours=3))
COOKIE = 'evasess=SECRETVALUE123'

def today():
    return datetime.now(BAHRAIN).date()

def clock(h, m):
    return datetime(2000, 1, 1, h, m).strftime('%I:%M %p').lstrip('0')

class B:
    def __init__(self, bid, start, end, status='Confirmed', owner='own1', arena='1', linked=(), total=10, day=None, game='EVA BATTLE ARENA'):
        self.id, self.start, self.end, self.status, self.owner, self.arena = str(bid), start, end, status, owner, arena
        self.linked, self.total, self.day, self.game = list(linked), total, day, game

USERS = {  # id -> (first, last, email)
    'own1': ('Owner', 'One', 'owner1@example.com'), 'own2': ('Owner', 'Two', ''),
    'MAH#33769': ('Player', 'Hash', 'hash@example.com'), 'u1': ('P', 'One', 'u1@example.com'),
    'u2': ('P', 'Two', 'u2@example.com'), 'u3': ('P', 'Three', 'u3@example.com'), 'u4': ('P', 'Four', 'u4@example.com'),
}

def default_bookings():
    d = today()
    rows = [
        B(1001, (14, 0), (14, 40), status='Cancelled', day=d),
        B(1002, (18, 40), (19, 20), linked=['MAH#33769'], total=10, day=d),            # normal: 1 of 10 linked
        B(1003, (19, 20), (20, 0), linked=['u1', 'u2', 'u3'], total=3, owner='own1', day=d),   # all linked
        B(1004, (19, 20), (20, 0), linked=['u4'], total=5, owner='own2', day=d),                # second booking, same session
        B(1005, (20, 0), (20, 40), arena='2', linked=['u1'], total=2, day=d),                    # other arena
        B(1099, (23, 0), (23, 40), linked=['u1'], total=1, day=d - timedelta(days=1)),          # yesterday
    ]
    for n in range(12):  # filler so the list needs a second page
        rows.append(B(2000 + n, (9 + n // 4, (n % 4) * 15), (9 + n // 4, (n % 4) * 15 + 10), day=d, owner='own1'))
    return sorted(rows, key=lambda b: (b.day, b.start))

CSS = '<style>table{border-collapse:collapse}.modal{display:none}.modal.show{display:block}.disabled a{pointer-events:none}</style>'

def page_list(m):
    return f'''<!doctype html><html><head>{CSS}</head><body>
<nav><button class="scope" type="button"><div>Scope</div><div>{m.scope}</div></button></nav>
<h2>Bookings</h2>
<select id="status"><option>All</option><option>Cancelled</option><option>Confirmed</option></select>
<button type="button" id="date-range-picker" class="dropdown-toggle btn">Coming up</button>
<div class="dropdown-menu" id="menu" style="display:none"><button type="button">Today</button><button type="button">Last 30 days</button><button type="button">Coming up</button><button type="button">All sessions</button></div>
<table class="table"><thead><tr><th>BOOKING (ORDER)</th><th>STATUS</th><th>USER</th><th>GAME</th><th>DATE</th><th>SESSION</th><th>CONVOCATION TIME</th><th>PLAYERS</th><th>PAYMENT</th></tr></thead><tbody id="rows"></tbody></table>
<ul class="pagination" id="pager"></ul>
<script>
let preset='Coming up', page=1, DATA=[];
async function load(){{ document.getElementById('status').disabled=true;
  const r=await fetch('/api/rows?preset='+encodeURIComponent(preset)); DATA=await r.json(); render();
  setTimeout(()=>document.getElementById('status').disabled=false,150); }}
function render(){{ const per=10, total=Math.max(1,Math.ceil(DATA.length/per)); if(page>total) page=total;
  document.getElementById('rows').innerHTML=DATA.slice((page-1)*per,page*per).map(b=>`<tr data-booking="${{b.id}}" data-start-time="${{b.s}}"><td>#${{b.id}} (#9${{b.id}})</td><td>${{b.status}}</td><td><a href="/users/view/${{encodeURIComponent(b.owner)}}">Owner</a><br>@owner</td><td>${{b.game}}</td><td>${{b.date}}</td><td>${{b.session}}</td><td>${{b.conv}}</td><td>${{b.players}}</td><td>Paid</td></tr>`).join('');
  let h='<li class="page-item"><span class="page-link">«</span></li>';
  for(let i=1;i<=total;i++) h+=`<li class="page-item"><a class="page-link" role="button" data-p="${{i}}">${{i}}</a></li>`;
  h+=`<li class="page-item ${{page>=total?'disabled':''}}"><a class="page-link" role="button" id="nxt">»</a></li>`;
  document.getElementById('pager').innerHTML=h;
  document.getElementById('nxt').onclick=()=>{{ if(page<total){{ page++; document.getElementById('status').disabled=true; setTimeout(()=>{{render();document.getElementById('status').disabled=false}},120) }} }}; }}
document.getElementById('date-range-picker').onclick=()=>{{const m=document.getElementById('menu'); m.style.display=m.style.display==='none'?'block':'none'; m.classList.toggle('show')}};
document.querySelectorAll('#menu button').forEach(b=>b.onclick=()=>{{preset=b.textContent; page=1; document.getElementById('date-range-picker').textContent=preset; document.getElementById('menu').style.display='none'; load()}});
load();
</script></body></html>'''

def page_booking(m, b):
    status = b.status
    return f'''<!doctype html><html><head>{CSS}</head><body>
<nav><span>Marassi</span></nav><h2>Bookings</h2><h1>Order#9{b.id}</h1>
<table><thead><tr><th></th><th>STATUS</th><th>DATE</th><th>SESSION</th><th>CONVOCATION TIME</th><th>GAME</th><th>PLAYER</th></tr></thead>
<tbody><tr><td><a href="/bookings/{b.id}">#{b.id}</a></td><td>{status}</td><td>{b.day.strftime('%m/%d/%Y')}</td><td>{clock(*b.start)} (To {clock(*b.end)})</td><td>13:50</td><td>{b.game}</td><td>{b.total}</td></tr></tbody></table>
<dl><dt>Arena</dt><dd>{b.arena}</dd><dt>Mode</dt><dd>Standard</dd></dl>
<button type="button" id="att">View {len(b.linked)}/{b.total} attendees</button>
<div class="modal fade" id="modal"><div class="modal-dialog"><div class="modal-content"><div class="modal-header"><h5 class="modal-title">Associate a player account</h5></div>
<div class="modal-body"><table><thead><tr><th>USER</th><th></th></tr></thead><tbody>{''.join(f'<tr><td><a href="/users/view/{urllib.parse.quote(u, safe="")}">{u}</a></td><td></td></tr>' for u in b.linked)}</tbody></table></div>
<button type="button">Close</button></div></div></div>
<script>document.getElementById('att').onclick=()=>{{const m=document.getElementById('modal'); m.classList.add('show')}}</script></body></html>'''

def page_user(uid):
    first, last, email = USERS.get(uid, ('', '', ''))
    return f'''<!doctype html><html><body><h1>Player information</h1>
<input name="email" readonly value="{email}"><input name="firstName" readonly value="{first}"><input name="lastName" readonly value="{last}"></body></html>'''


class Mock:
    def __init__(self, bookings=None, scope='Marassi'):
        self.bookings = bookings or default_bookings()
        self.scope, self.valid, self.calls = scope, True, []
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def _authed(self): return mock.valid and COOKIE in (self.headers.get('Cookie') or '')
            def _send(self, code, body, ctype='text/html; charset=utf-8', headers=None):
                data = body.encode() if isinstance(body, str) else body
                self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(data)))
                for k, v in (headers or {}).items(): self.send_header(k, v)
                self.end_headers(); self.wfile.write(data)
            def do_GET(self):
                u = urllib.parse.urlparse(self.path); path = u.path; mock.calls.append(path)
                if path == '/login':
                    # being sent here by a redirect only shows the sign-in page; the cookie is issued once the operator submits it (?done=1 / a direct visit)
                    return self._send(200, '<html><body>Sign in</body></html>', headers=None if 'need=1' in u.query else {'Set-Cookie': COOKIE + '; Path=/'})
                if not self._authed(): return self._send(302, '', headers={'Location': '/login?need=1'})
                if path == '/bookings': return self._send(200, page_list(mock))
                if path.startswith('/bookings/'):
                    b = next((x for x in mock.bookings if x.id == path.split('/')[2]), None)
                    return self._send(200, page_booking(mock, b)) if b else self._send(404, 'no')
                if path.startswith('/users/view/'):
                    return self._send(200, page_user(urllib.parse.unquote(path.split('/')[3])))
                if path == '/api/rows':
                    preset = urllib.parse.parse_qs(u.query)['preset'][0]; t = today()
                    keep = {'Today': lambda d: d == t, 'Last 30 days': lambda d: t - timedelta(days=30) <= d <= t,
                            'Coming up': lambda d: d >= t, 'All sessions': lambda d: True}[preset]
                    rows = [dict(id=b.id, status=b.status, owner=b.owner, game=b.game, date=b.day.strftime('%m/%d/%Y'),
                                 session=f'{clock(*b.start)} (To {clock(*b.end)})', conv='13:50', players=b.total, s=f'{b.start[0]}:{b.start[1]:02d}')
                            for b in mock.bookings if keep(b.day)]
                    return self._send(200, json.dumps(rows), 'application/json')
                self._send(404, 'nope')
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self): return f'http://127.0.0.1:{self.port}'
    def stop(self): self.httpd.shutdown()
